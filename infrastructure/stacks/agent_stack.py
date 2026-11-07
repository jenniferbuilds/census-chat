"""Cognito, AgentCore Memory, and AgentCore Runtime infrastructure."""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_cognito as cognito,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_ssm as ssm,
)
from constructs import Construct


class AgentStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        kb_bucket_name: str,
        knowledge_base_id: str,
        guardrail_id: str,
        guardrail_version: str,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        # Cognito authentication for direct browser -> AgentCore Runtime requests.
        self.user_pool = cognito.UserPool(
            self,
            "CensusChatUserPool",
            user_pool_name="census-chat-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True, username=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.user_pool_client = self.user_pool.add_client(
            "CensusChatWebClient",
            user_pool_client_name="census-chat-web",
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            access_token_validity=Duration.hours(8),
            id_token_validity=Duration.hours(8),
            refresh_token_validity=Duration.days(30),
            prevent_user_existence_errors=True,
            generate_secret=False,
        )

        # Managed short-term events plus all four built-in long-term strategies.
        self.memory = agentcore.Memory(
            self,
            "CensusChatMemory",
            memory_name="censusChatMemoryManaged",
            description="Conversation continuity and user preferences for Census Chat",
            expiration_duration=Duration.days(30),
            memory_strategies=[
                agentcore.MemoryStrategy.using_built_in_summarization(),
                agentcore.MemoryStrategy.using_built_in_user_preference(),
                agentcore.MemoryStrategy.using_built_in_semantic(),
                agentcore.MemoryStrategy.using_built_in_episodic(),
            ],
        )

        self.agent_execution_role = iam.Role(
            self,
            "AgentCoreExecutionRole",
            role_name="CensusChatAgentCoreRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Execution role for the Census Chat AgentCore Runtime",
        )

        # Kimi K3.1 uses a US cross-region inference profile.
        self.agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.moonshotai.kimi-k3.1",
                    "arn:aws:bedrock:*::foundation-model/moonshotai.kimi-k3.1",
                ],
            )
        )
        self.agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/{knowledge_base_id}"
                ],
            )
        )
        self.agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:ApplyGuardrail"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:guardrail/{guardrail_id}"
                ],
            )
        )
        self.memory.grant_read(self.agent_execution_role)
        self.memory.grant_write(self.agent_execution_role)
        self.agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetMemory"],
                resources=[self.memory.memory_arn],
            )
        )
        self.agent_execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                ],
                resources=["*"],
            )
        )

        repository = ecr.Repository.from_repository_name(
            self, "CensusChatAgentRepository", "census-chat-agent"
        )
        artifact = agentcore.AgentRuntimeArtifact.from_ecr_repository(repository, "v4")
        self.runtime = agentcore.Runtime(
            self,
            "CensusChatRuntime",
            runtime_name="censusChatAgentManaged",
            description="Authenticated RAG agent for US Census data",
            agent_runtime_artifact=artifact,
            execution_role=self.agent_execution_role,
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            protocol_configuration=agentcore.ProtocolType.HTTP,
            platform_version=agentcore.PlatformVersion.V2,
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_cognito(
                self.user_pool,
                [self.user_pool_client],
            ),
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(15),
                max_lifetime=Duration.hours(8),
            ),
            environment_variables={
                "AWS_REGION": self.region,
                "MODEL_ID": "us.moonshotai.kimi-k3.1",
                "KNOWLEDGE_BASE_ID": knowledge_base_id,
                "GUARDRAIL_ID": guardrail_id,
                "GUARDRAIL_VERSION": guardrail_version,
                "AGENTCORE_MEMORY_ID": self.memory.memory_id,
                "KB_TOP_K": "8",
                "KB_MIN_SCORE": "0.25",
                "MAX_INPUT_CHARS": "2000",
            },
            tracing_enabled=False,
        )

        # Stable parameters used by deployment and frontend build scripts.
        ssm.StringParameter(
            self,
            "UserPoolIdParam",
            parameter_name="/census-chat/cognito/user-pool-id",
            string_value=self.user_pool.user_pool_id,
        )
        ssm.StringParameter(
            self,
            "UserPoolClientIdParam",
            parameter_name="/census-chat/cognito/client-id",
            string_value=self.user_pool_client.user_pool_client_id,
        )
        ssm.StringParameter(
            self,
            "AgentRoleArnParam",
            parameter_name="/census-chat/agent/execution-role-arn",
            string_value=self.agent_execution_role.role_arn,
        )
        ssm.StringParameter(
            self,
            "KBIdParam",
            parameter_name="/census-chat/kb/id",
            string_value=knowledge_base_id,
        )
        ssm.StringParameter(
            self,
            "GuardrailIdParam",
            parameter_name="/census-chat/guardrail/id",
            string_value=guardrail_id,
        )
        ssm.StringParameter(
            self,
            "GuardrailVersionParam",
            parameter_name="/census-chat/guardrail/version",
            string_value=guardrail_version,
        )

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "AgentExecutionRoleArn", value=self.agent_execution_role.role_arn)
        CfnOutput(self, "AgentRuntimeId", value=self.runtime.agent_runtime_id)
        CfnOutput(self, "AgentRuntimeArn", value=self.runtime.agent_runtime_arn)
        CfnOutput(self, "AgentCoreMemoryId", value=self.memory.memory_id)
        CfnOutput(self, "AgentCoreMemoryArn", value=self.memory.memory_arn)
        CfnOutput(
            self,
            "CognitoIssuerUrl",
            value=f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}",
        )
