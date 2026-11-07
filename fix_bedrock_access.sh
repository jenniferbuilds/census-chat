#!/bin/bash
# Emergency Bedrock Access Fix Script
# Run this with: bash fix_bedrock_access.sh

set -e

echo "=== Bedrock Knowledge Base Access Fix ==="
echo ""
echo "⚠️  WARNING: Rotate the AWS credentials after running this script!"
echo ""

# Set temporary credentials (ROTATE THESE AFTER USE!)
# Replace these with your actual credentials or use AWS CLI profiles
export AWS_ACCESS_KEY_ID="YOUR_AWS_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="YOUR_AWS_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="us-east-1"

echo "Step 1: Testing Bedrock access with these credentials..."
aws bedrock list-foundation-models --region us-east-1 --query 'modelSummaries[?contains(modelId,`titan-embed`)].modelId' 2>&1 | head -5

echo ""
echo "Step 2: Attempting to start Knowledge Base ingestion..."
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id 9MT5LSN50L \
  --data-source-id PZY1BZCEBU \
  --region us-east-1 2>&1

echo ""
echo "If ingestion started successfully, monitor with:"
echo "  aws bedrock-agent get-ingestion-job --knowledge-base-id 9MT5LSN50L --data-source-id PZY1BZCEBU --ingestion-job-id <JOB_ID> --region us-east-1"
echo ""
echo "⚠️  CRITICAL: Go to IAM Console and ROTATE these credentials NOW!"
echo "    IAM → Users → [user] → Security credentials → Delete this access key"
