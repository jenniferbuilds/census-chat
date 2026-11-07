"""
Upload ETL output documents to S3 for Bedrock Knowledge Base ingestion.
"""
import boto3
import os
from pathlib import Path
from tqdm import tqdm

REGION = "us-east-1"
DOCUMENTS_DIR = Path(__file__).parent / "output" / "documents"


def get_bucket_name(region: str = REGION) -> str:
    ssm = boto3.client("ssm", region_name=region)
    bucket = ssm.get_parameter(Name="/census-chat/kb/bucket-name")["Parameter"]["Value"]
    return bucket


def upload_documents(bucket_name: str, prefix: str = "documents/", region: str = REGION):
    s3 = boto3.client("s3", region_name=region)
    docs = list(DOCUMENTS_DIR.glob("*.txt"))

    if not docs:
        print("❌ No documents found. Run transform.py first.")
        return 0

    print(f"Uploading {len(docs)} documents to s3://{bucket_name}/{prefix} ...")
    uploaded = 0
    failed = 0

    for doc_path in tqdm(docs):
        key = f"{prefix}{doc_path.name}"
        try:
            s3.upload_file(
                str(doc_path),
                bucket_name,
                key,
                ExtraArgs={"ContentType": "text/plain"},
            )
            uploaded += 1
        except Exception as e:
            print(f"  ⚠️ Failed to upload {doc_path.name}: {e}")
            failed += 1

    print(f"\n✅ Uploaded {uploaded} documents ({failed} failed)")
    return uploaded


if __name__ == "__main__":
    import sys
    bucket = sys.argv[1] if len(sys.argv) > 1 else get_bucket_name()
    upload_documents(bucket)
