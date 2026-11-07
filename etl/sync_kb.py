"""
Trigger Bedrock Knowledge Base ingestion and wait for completion.
"""
import boto3
import time
import sys

REGION = "us-east-1"


def get_kb_config(region: str = REGION) -> dict:
    ssm = boto3.client("ssm", region_name=region)
    return {
        "kb_id": ssm.get_parameter(Name="/census-chat/kb/id")["Parameter"]["Value"],
        "ds_id": ssm.get_parameter(Name="/census-chat/kb/datasource-id")["Parameter"]["Value"],
    }


def start_ingestion(kb_id: str, ds_id: str, region: str = REGION) -> str:
    client = boto3.client("bedrock-agent", region_name=region)
    resp = client.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
    )
    job_id = resp["ingestionJob"]["ingestionJobId"]
    print(f"✅ Ingestion job started: {job_id}")
    return job_id


def wait_for_ingestion(kb_id: str, ds_id: str, job_id: str, region: str = REGION):
    client = boto3.client("bedrock-agent", region_name=region)
    print("Waiting for ingestion to complete", end="", flush=True)

    while True:
        resp = client.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job_id,
        )
        status = resp["ingestionJob"]["status"]
        stats = resp["ingestionJob"].get("statistics", {})

        if status == "COMPLETE":
            print(f"\n✅ Ingestion COMPLETE")
            print(f"   Documents scanned:  {stats.get('numberOfDocumentsScanned', 0)}")
            print(f"   Documents indexed:  {stats.get('numberOfDocumentsIndexed', 0)}")
            print(f"   Documents failed:   {stats.get('numberOfDocumentsFailed', 0)}")
            break
        elif status == "FAILED":
            print(f"\n❌ Ingestion FAILED")
            for failure in resp["ingestionJob"].get("failureReasons", []):
                print(f"   - {failure}")
            sys.exit(1)
        else:
            print(".", end="", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    cfg = get_kb_config()
    job_id = start_ingestion(cfg["kb_id"], cfg["ds_id"])
    wait_for_ingestion(cfg["kb_id"], cfg["ds_id"], job_id)
