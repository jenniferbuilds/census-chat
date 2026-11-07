"""
DataStack — S3 data source bucket, IAM role for Bedrock KB, and Guardrail.

Bedrock Knowledge Base and Data Source are created via boto3 in deploy.sh
(using S3 Vectors as the vector store backend) because CDK doesn't yet
reliably support the newest storage configuration options.
"""
from aws_cdk import (
    Stack, RemovalPolicy, CfnOutput, Duration,
    aws_s3 as s3,
    aws_iam as iam,
    aws_bedrock as bedrock,
)
from constructs import Construct


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── S3: Knowledge-Base data source ────────────────────────────────
        self.kb_bucket = s3.Bucket(
            self, "CensusChatKBBucket",
            bucket_name=f"census-chat-kb-data-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-old-versions",
                    noncurrent_version_expiration=Duration.days(30),
                )
            ],
        )

        # ── IAM: Role for Bedrock Knowledge Base ──────────────────────────
        self.kb_role = iam.Role(
            self, "BedrockKBRole",
            role_name="CensusChatBedrockKBRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Allows Bedrock KB to read from S3 and use Titan Embeddings",
        )

        # S3 read access for the data source bucket
        self.kb_bucket.grant_read(self.kb_role)

        # Bedrock foundation model invocation (for embeddings)
        self.kb_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock-runtime:InvokeModel",
                ],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0",
                    f"arn:aws:bedrock:{self.region}::foundation-model/amazon.titan-embed-text-v2:0:*",
                ],
            )
        )

        # S3 Vectors permissions for vector store backend
        self.kb_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3vectors:CreateVector",
                    "s3vectors:GetVector",
                    "s3vectors:DeleteVector",
                    "s3vectors:QueryVectors",
                    "s3vectors:PutVectors",
                    "s3vectors:GetVectorBucket",
                    "s3vectors:ListVectorBuckets",
                ],
                resources=["*"],
            )
        )

        # ── Bedrock Guardrail ─────────────────────────────────────────────
        self.guardrail = bedrock.CfnGuardrail(
            self, "CensusChatGuardrail",
            name="census-chat-guardrail",
            description="Filters off-topic, harmful, and hallucinated content",
            blocked_input_messaging=(
                "I'm focused on answering questions about US Census data. "
                "Please ask me about population, demographics, income, housing, or employment statistics."
            ),
            blocked_outputs_messaging=(
                "I wasn't able to provide a reliable answer based on the available census data. "
                "Please try rephrasing your question."
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="general-purpose-and-unrelated-requests",
                        definition=(
                            "Requests for entertainment, creative writing, code, weather, sports, "
                            "travel, shopping, financial, medical or legal advice, news, personal "
                            "errands, or private system instructions."
                        ),
                        examples=[
                            "Tell me a joke",
                            "Write me a poem",
                            "What's the weather like?",
                            "Debug my Python program",
                            "What stocks should I buy?",
                        ],
                        type="DENY",
                    )
                ]
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE", input_strength="HIGH", output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE", input_strength="MEDIUM", output_strength="MEDIUM",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE",
                    ),
                ]
            ),
            contextual_grounding_policy_config=bedrock.CfnGuardrail.ContextualGroundingPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="GROUNDING", threshold=0.7,
                    ),
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="RELEVANCE", threshold=0.7,
                    ),
                ]
            ),
        )

        # Keep V1 and its export during the two-phase migration because the existing
        # AgentStack imports it. V2 contains the corrected topic definition.
        self.legacy_guardrail_version = bedrock.CfnGuardrailVersion(
            self, "CensusChatGuardrailV1",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="Initial version",
        )
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self, "CensusChatGuardrailV2",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="Corrected topic classification and contextual grounding",
        )

        legacy_export = CfnOutput(
            self,
            "LegacyGuardrailVersionExport",
            value=self.legacy_guardrail_version.attr_version,
            export_name=(
                "CensusChatDataStack:ExportsOutputFnGetAtt"
                "CensusChatGuardrailV1Version1F5D5FAF"
            ),
        )
        legacy_export.override_logical_id(
            "ExportsOutputFnGetAttCensusChatGuardrailV1Version1F5D5FAF"
        )

        # ── Outputs ────────────────────────────────────────────────────────
        CfnOutput(self, "KBBucketName",      value=self.kb_bucket.bucket_name,          export_name="CensusChatKBBucketName")
        CfnOutput(self, "KBRoleArn",         value=self.kb_role.role_arn,               export_name="CensusChatKBRoleArn")
        CfnOutput(self, "GuardrailId",       value=self.guardrail.attr_guardrail_id,    export_name="CensusChatGuardrailId")
        CfnOutput(self, "GuardrailVersion",  value=self.guardrail_version.attr_version, export_name="CensusChatGuardrailVersion")
