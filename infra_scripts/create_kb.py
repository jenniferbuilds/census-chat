"""
Creates the Bedrock Knowledge Base with S3 Vectors backend via boto3.
Called from deploy.sh after DataStack is deployed.
"""
import boto3, time, json, sys, os

REGION  = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
ACCOUNT = os.environ.get("AWS_ACCOUNT_ID", "601277096233")

def run(kb_bucket: str, kb_role_arn: str):
    s3v  = boto3.client("s3vectors",    region_name=REGION)
    ba   = boto3.client("bedrock-agent", region_name=REGION)
    ssm  = boto3.client("ssm",           region_name=REGION)

    VECTOR_BUCKET = "census-chat-vectors"
    KB_NAME       = "census-chat-kb"
    DS_NAME       = "census-s3-datasource"

    # ── 1. Create S3 Vectors bucket ───────────────────────────────────────
    print("Creating S3 Vectors bucket...")
    try:
        s3v.create_vector_bucket(vectorBucketName=VECTOR_BUCKET)
        print(f"  ✅ Created: {VECTOR_BUCKET}")
    except s3v.exceptions.from_code("ConflictException"):
        print(f"  ℹ️  Already exists: {VECTOR_BUCKET}")
    except Exception as e:
        if "already" in str(e).lower() or "exists" in str(e).lower():
            print(f"  ℹ️  Already exists: {VECTOR_BUCKET}")
        else:
            raise

    vector_bucket_arn = f"arn:aws:s3vectors:{REGION}:{ACCOUNT}:bucket/{VECTOR_BUCKET}"
    print(f"  Vector bucket ARN: {vector_bucket_arn}")

    # ── 2. Create Bedrock Knowledge Base ──────────────────────────────────
    print("\nCreating Bedrock Knowledge Base...")
    
    # Check if KB already exists
    existing_kbs = ba.list_knowledge_bases().get("knowledgeBaseSummaries", [])
    kb_id = next((kb["knowledgeBaseId"] for kb in existing_kbs if kb["name"] == KB_NAME), None)

    if not kb_id:
        resp = ba.create_knowledge_base(
            name=KB_NAME,
            description="US Census ACS 2020 data — county and state level statistics",
            roleArn=kb_role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0",
                    "embeddingModelConfiguration": {
                        "bedrockEmbeddingModelConfiguration": {"dimensions": 1024}
                    },
                },
            },
            storageConfiguration={
                "type": "S3_VECTORS",
                "s3VectorsConfiguration": {
                    "vectorBucketArn": vector_bucket_arn,
                    "indexName": "census-index",
                },
            },
        )
        kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
        print(f"  ✅ Knowledge Base created: {kb_id}")

        # Wait for KB to become ACTIVE
        print("  Waiting for KB to become ACTIVE...")
        for _ in range(30):
            status = ba.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]
            if status == "ACTIVE":
                print(f"  ✅ KB is ACTIVE")
                break
            elif "FAIL" in status:
                raise RuntimeError(f"KB failed: {status}")
            print(f"    status: {status}", end="\r")
            time.sleep(5)
    else:
        print(f"  ℹ️  Already exists: {kb_id}")

    # ── 3. Create Data Source ─────────────────────────────────────────────
    print("\nCreating KB Data Source...")
    existing_ds = ba.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", [])
    ds_id = next((ds["dataSourceId"] for ds in existing_ds if ds["name"] == DS_NAME), None)

    if not ds_id:
        resp = ba.create_data_source(
            knowledgeBaseId=kb_id,
            name=DS_NAME,
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": f"arn:aws:s3:::{kb_bucket}",
                    "inclusionPrefixes": ["documents/"],
                },
            },
            vectorIngestionConfiguration={
                "chunkingConfiguration": {
                    "chunkingStrategy": "FIXED_SIZE",
                    "fixedSizeChunkingConfiguration": {
                        "maxTokens": 512,
                        "overlapPercentage": 20,
                    },
                }
            },
        )
        ds_id = resp["dataSource"]["dataSourceId"]
        print(f"  ✅ Data Source created: {ds_id}")
    else:
        print(f"  ℹ️  Already exists: {ds_id}")

    # ── 4. Store IDs in SSM ───────────────────────────────────────────────
    for name, value in [
        ("/census-chat/kb/id", kb_id),
        ("/census-chat/kb/datasource-id", ds_id),
        ("/census-chat/kb/bucket-name", kb_bucket),
        ("/census-chat/kb/vector-bucket-arn", vector_bucket_arn),
    ]:
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)

    print(f"\n✅ KB setup complete")
    print(f"   KB ID:         {kb_id}")
    print(f"   DS ID:         {ds_id}")
    print(f"   Vector bucket: {vector_bucket_arn}")
    return {"kb_id": kb_id, "ds_id": ds_id}


if __name__ == "__main__":
    kb_bucket  = sys.argv[1]
    kb_role    = sys.argv[2]
    result = run(kb_bucket, kb_role)
    print(json.dumps(result))
