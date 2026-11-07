#!/usr/bin/env bash
# ============================================================
# census-chat full deployment script
# Usage: ./deploy.sh [--skip-etl] [--skip-build]
# ============================================================
set -euo pipefail

REGION="us-east-1"
ACCOUNT="601277096233"
AWS_PROFILE="deploy"
ECR_REPO="census-chat-agent"
MODEL_ID="us.amazon.nova-pro-v1:0"  # swap to us.anthropic.claude-opus-5-5 once Anthropic verification clears (~2hrs)

SKIP_ETL=${1:-""}
SKIP_BUILD=${2:-""}

echo "============================================================"
echo " census-chat deployment — $(date)"
echo "============================================================"

export AWS_PROFILE=$AWS_PROFILE

# ── Step 1: Install CDK dependencies ─────────────────────────
echo ""
echo "▶ Step 1: Installing CDK Python dependencies..."
cd "$(dirname "$0")/infrastructure"
pip install -r requirements.txt -q

# ── Step 2: CDK Bootstrap (only needed once per account/region)
echo ""
echo "▶ Step 2: CDK Bootstrap..."
cdk bootstrap aws://$ACCOUNT/$REGION --profile $AWS_PROFILE 2>&1 | tail -3 || true

# ── Step 3: Deploy DataStack ──────────────────────────────────
echo ""
echo "▶ Step 3: Deploying DataStack (S3, Knowledge Base, Guardrail)..."
cdk deploy CensusChatDataStack --require-approval never --profile $AWS_PROFILE --outputs-file /tmp/data-stack-outputs.json
echo "   ✅ DataStack deployed"

# Extract outputs
KB_BUCKET=$(jq -r '.CensusChatDataStack.KBBucketName' /tmp/data-stack-outputs.json)
KB_ID=$(jq -r '.CensusChatDataStack.KnowledgeBaseId' /tmp/data-stack-outputs.json)
DS_ID=$(jq -r '.CensusChatDataStack.DataSourceId' /tmp/data-stack-outputs.json)
GUARDRAIL_ID=$(jq -r '.CensusChatDataStack.GuardrailId' /tmp/data-stack-outputs.json)
GUARDRAIL_VERSION=$(jq -r '.CensusChatDataStack.GuardrailVersion' /tmp/data-stack-outputs.json)

# Store additional SSM params not in CDK
aws ssm put-parameter --name "/census-chat/kb/bucket-name" --value "$KB_BUCKET" --type String --overwrite --region $REGION --profile $AWS_PROFILE
aws ssm put-parameter --name "/census-chat/kb/datasource-id" --value "$DS_ID" --type String --overwrite --region $REGION --profile $AWS_PROFILE

# ── Step 4: Deploy AgentStack ─────────────────────────────────
echo ""
echo "▶ Step 4: Deploying AgentStack (Cognito, IAM)..."
cdk deploy CensusChatAgentStack --require-approval never --profile $AWS_PROFILE --outputs-file /tmp/agent-stack-outputs.json
echo "   ✅ AgentStack deployed"

USER_POOL_ID=$(jq -r '.CensusChatAgentStack.UserPoolId' /tmp/agent-stack-outputs.json)
CLIENT_ID=$(jq -r '.CensusChatAgentStack.UserPoolClientId' /tmp/agent-stack-outputs.json)
AGENT_ROLE_ARN=$(jq -r '.CensusChatAgentStack.AgentExecutionRoleArn' /tmp/agent-stack-outputs.json)
COGNITO_ISSUER=$(jq -r '.CensusChatAgentStack.CognitoIssuerUrl' /tmp/agent-stack-outputs.json)

# ── Step 5: Create AgentCore Memory store ────────────────────
echo ""
echo "▶ Step 5: Creating AgentCore Memory store..."
cd "$(dirname "$0")"
MEMORY_RESULT=$(python3 infrastructure/stacks/memory_stack.py 2>&1)
echo "$MEMORY_RESULT"
MEMORY_ID=$(echo "$MEMORY_RESULT" | grep -o 'Memory[a-zA-Z0-9-]*' | head -1 || echo "")
# Store memory ID
if [[ -n "$MEMORY_ID" ]]; then
    aws ssm put-parameter --name "/census-chat/memory/id" --value "$MEMORY_ID" --type String --overwrite --region $REGION --profile $AWS_PROFILE
fi

# ── Step 6: Run ETL pipeline ──────────────────────────────────
if [[ "$SKIP_ETL" != "--skip-etl" ]]; then
    echo ""
    echo "▶ Step 6: Running ETL pipeline..."
    cd "$(dirname "$0")/etl"
    pip install -r requirements.txt -q

    echo "  6a. Transforming SafeGraph CSVs to KB documents..."
    python3 transform.py

    echo "  6b. Uploading documents to S3..."
    python3 upload_to_s3.py "$KB_BUCKET"

    echo "  6c. Triggering Bedrock Knowledge Base sync..."
    python3 sync_kb.py
    echo "   ✅ ETL complete"
else
    echo "   ⏭  Skipping ETL (--skip-etl flag set)"
fi

# ── Step 7: Build and push agent Docker image ─────────────────
if [[ "$SKIP_BUILD" != "--skip-build" ]]; then
    echo ""
    echo "▶ Step 7: Building and pushing agent Docker image..."
    ECR_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"

    # Create ECR repo if not exists
    aws ecr describe-repositories --repository-names $ECR_REPO --region $REGION --profile $AWS_PROFILE 2>/dev/null || \
    aws ecr create-repository --repository-name $ECR_REPO --region $REGION --profile $AWS_PROFILE

    # Login and push
    aws ecr get-login-password --region $REGION --profile $AWS_PROFILE | \
        docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

    cd "$(dirname "$0")/agent"
    docker build -t $ECR_REPO:latest --platform linux/amd64 .
    docker tag $ECR_REPO:latest $ECR_URI:latest
    docker push $ECR_URI:latest
    echo "   ✅ Image pushed: $ECR_URI:latest"
else
    ECR_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO"
    echo "   ⏭  Skipping Docker build (--skip-build flag set)"
fi

# ── Step 8: Create / update AgentCore Runtime ────────────────
echo ""
echo "▶ Step 8: Creating AgentCore Runtime..."

MEMORY_ID_VAL=$(aws ssm get-parameter --name "/census-chat/memory/id" --region $REGION --profile $AWS_PROFILE --query 'Parameter.Value' --output text 2>/dev/null || echo "")

RUNTIME_EXISTS=$(aws bedrock-agentcore-control list-agent-runtimes \
    --region $REGION --profile $AWS_PROFILE \
    --query "agentRuntimes[?name=='census-chat-agent'].agentRuntimeId" \
    --output text 2>/dev/null || echo "")

RUNTIME_CONFIG=$(cat <<EOF
{
  "name": "census-chat-agent",
  "description": "Census Chat agent — US population data explorer",
  "agentRuntimeArtifact": {
    "containerConfiguration": {
      "containerUri": "$ECR_URI:latest"
    }
  },
  "roleArn": "$AGENT_ROLE_ARN",
  "platformVersion": "V2",
  "environmentVariables": {
    "MODEL_ID": "$MODEL_ID",
    "KNOWLEDGE_BASE_ID": "$KB_ID",
    "GUARDRAIL_ID": "$GUARDRAIL_ID",
    "GUARDRAIL_VERSION": "$GUARDRAIL_VERSION",
    "AGENTCORE_MEMORY_ID": "$MEMORY_ID_VAL",
    "AWS_REGION": "$REGION"
  },
  "authorizerConfiguration": {
    "customJWTAuthorizer": {
      "allowedAudiences": ["$CLIENT_ID"],
      "discoveryUrl": "$COGNITO_ISSUER/.well-known/openid-configuration"
    }
  }
}
EOF
)

if [[ -z "$RUNTIME_EXISTS" ]]; then
    RUNTIME_RESULT=$(aws bedrock-agentcore-control create-agent-runtime \
        --region $REGION --profile $AWS_PROFILE \
        --cli-input-json "$RUNTIME_CONFIG" 2>&1)
    RUNTIME_ID=$(echo "$RUNTIME_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('agentRuntimeId',''))" 2>/dev/null || echo "")
    echo "   ✅ AgentCore Runtime created: $RUNTIME_ID"
else
    RUNTIME_ID=$RUNTIME_EXISTS
    aws bedrock-agentcore-control update-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region $REGION --profile $AWS_PROFILE \
        --cli-input-json "$RUNTIME_CONFIG" 2>&1 | tail -2
    echo "   ✅ AgentCore Runtime updated: $RUNTIME_ID"
fi

# Wait for runtime to be READY
echo "   Waiting for AgentCore Runtime to be READY (V2 snapshot)..."
for i in $(seq 1 40); do
    STATUS=$(aws bedrock-agentcore-control get-agent-runtime \
        --agent-runtime-id "$RUNTIME_ID" \
        --region $REGION --profile $AWS_PROFILE \
        --query 'status' --output text 2>/dev/null || echo "UNKNOWN")
    echo "   [$i/40] Status: $STATUS"
    if [[ "$STATUS" == "READY" ]]; then break; fi
    if [[ "$STATUS" == *"FAILED"* ]]; then
        echo "❌ Runtime failed to start"
        exit 1
    fi
    sleep 15
done

RUNTIME_ARN=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --region $REGION --profile $AWS_PROFILE \
    --query 'agentRuntimeArn' --output text)
echo "   Runtime ARN: $RUNTIME_ARN"

aws ssm put-parameter --name "/census-chat/agent/runtime-arn" --value "$RUNTIME_ARN" --type String --overwrite --region $REGION --profile $AWS_PROFILE
aws ssm put-parameter --name "/census-chat/agent/runtime-id" --value "$RUNTIME_ID" --type String --overwrite --region $REGION --profile $AWS_PROFILE

# ── Step 9: Create Cognito test user ─────────────────────────
echo ""
echo "▶ Step 9: Creating Cognito test user..."
TEST_USER="test_census"
TEST_PASS="Census2024!"

aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$TEST_USER" \
    --temporary-password "$TEST_PASS" \
    --message-action SUPPRESS \
    --region $REGION --profile $AWS_PROFILE 2>/dev/null || echo "   User may already exist"

# Set permanent password (skip first-login change)
aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$TEST_USER" \
    --password "$TEST_PASS" \
    --permanent \
    --region $REGION --profile $AWS_PROFILE
echo "   ✅ Test user created: $TEST_USER / $TEST_PASS"

# ── Step 10: Build and deploy frontend ───────────────────────
echo ""
echo "▶ Step 10: Building and deploying React frontend..."
cd "$(dirname "$0")/frontend"
npm install --silent

# Write env file for Vite
cat > .env.production <<ENVEOF
VITE_COGNITO_USER_POOL_ID=$USER_POOL_ID
VITE_COGNITO_CLIENT_ID=$CLIENT_ID
VITE_AGENT_RUNTIME_ARN=$RUNTIME_ARN
VITE_AWS_REGION=$REGION
ENVEOF

npm run build

# Deploy CDK frontend stack (S3 + CloudFront)
cd "$(dirname "$0")/infrastructure"
cdk deploy CensusChatFrontendStack --require-approval never --profile $AWS_PROFILE --outputs-file /tmp/frontend-stack-outputs.json
FRONTEND_BUCKET=$(jq -r '.CensusChatFrontendStack.FrontendBucketName' /tmp/frontend-stack-outputs.json)
CLOUDFRONT_URL=$(jq -r '.CensusChatFrontendStack.CloudFrontUrl' /tmp/frontend-stack-outputs.json)
DISTRIBUTION_ID=$(jq -r '.CensusChatFrontendStack.DistributionId' /tmp/frontend-stack-outputs.json)

# Upload built assets to S3
cd "$(dirname "$0")/frontend"
aws s3 sync dist/ s3://$FRONTEND_BUCKET --delete --region $REGION --profile $AWS_PROFILE

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id $DISTRIBUTION_ID --paths "/*" --region $REGION --profile $AWS_PROFILE > /dev/null
echo "   ✅ Frontend deployed"

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " ✅ DEPLOYMENT COMPLETE"
echo "============================================================"
echo ""
echo " 🌐 Web App:        $CLOUDFRONT_URL"
echo " 🤖 Agent Runtime:  $RUNTIME_ARN"
echo " 📚 Knowledge Base: $KB_ID"
echo " 🧠 Memory Store:   $MEMORY_ID_VAL"
echo " 🛡️  Guardrail:      $GUARDRAIL_ID (v$GUARDRAIL_VERSION)"
echo ""
echo " 🔑 Test Credentials:"
echo "    Username: $TEST_USER"
echo "    Password: $TEST_PASS"
echo ""
