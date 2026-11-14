import os
import sys
import json
import time
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
session = boto3.Session(region_name=REGION)
dynamodb = session.client("dynamodb")
s3 = session.client("s3")
rekognition = session.client("rekognition")
kms = session.client("kms")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]

TABLES = {
    "kiosk_users": {
        "KeySchema": [{"AttributeName": "badge_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "badge_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_sessions": {
        "KeySchema": [{"AttributeName": "nonce", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "nonce", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_decisions": {
        "KeySchema": [{"AttributeName": "event_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "event_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_sync_batches": {
        "KeySchema": [{"AttributeName": "batch_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "batch_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_zones": {
        "KeySchema": [{"AttributeName": "zone_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "zone_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_groups": {
        "KeySchema": [{"AttributeName": "group_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "group_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    "kiosk_schedules": {
        "KeySchema": [{"AttributeName": "schedule_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "schedule_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
}

COLLECTION_ID = "kiosk-face-collection-v1"
BUCKET_NAME = f"kiosk-evidence-{ACCOUNT_ID}-{REGION}"

def provision_dynamodb():
    print("--- Provisioning DynamoDB Tables ---")
    existing_tables = dynamodb.list_tables()["TableNames"]
    for table_name, schema in TABLES.items():
        if table_name in existing_tables:
            print(f"[EXISTS] DynamoDB table: {table_name}")
        else:
            print(f"[CREATING] DynamoDB table: {table_name}...")
            dynamodb.create_table(TableName=table_name, **schema)
            waiter = dynamodb.get_waiter("table_exists")
            waiter.wait(TableName=table_name)
            print(f"[READY] DynamoDB table: {table_name}")

        # Enable TTL on kiosk_sessions
        if table_name == "kiosk_sessions":
            try:
                dynamodb.update_time_to_live(
                    TableName=table_name,
                    TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
                )
                print("  TTL enabled on 'ttl' attribute.")
            except Exception as e:
                # May already be enabled
                pass

def provision_rekognition():
    print("\n--- Provisioning Rekognition Collection ---")
    try:
        cols = rekognition.list_collections()["CollectionIds"]
        if COLLECTION_ID in cols:
            print(f"[EXISTS] Rekognition collection: {COLLECTION_ID}")
        else:
            print(f"[CREATING] Rekognition collection: {COLLECTION_ID}...")
            rekognition.create_collection(CollectionId=COLLECTION_ID)
            print(f"[READY] Rekognition collection: {COLLECTION_ID}")
    except Exception as e:
        print(f"[ERROR] Rekognition collection provisioning: {e}")

def provision_s3():
    print("\n--- Provisioning S3 Evidence Bucket ---")
    try:
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if BUCKET_NAME in buckets:
            print(f"[EXISTS] S3 bucket: {BUCKET_NAME}")
        else:
            print(f"[CREATING] S3 bucket: {BUCKET_NAME} in {REGION}...")
            if REGION == "us-east-1":
                s3.create_bucket(Bucket=BUCKET_NAME)
            else:
                s3.create_bucket(
                    Bucket=BUCKET_NAME,
                    CreateBucketConfiguration={"LocationConstraint": REGION},
                )
            print(f"[READY] S3 bucket created: {BUCKET_NAME}")

        # Apply Public Access Block
        s3.put_public_access_block(
            Bucket=BUCKET_NAME,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        print("  Public access blocked.")

        # Apply Default SSE encryption
        s3.put_bucket_encryption(
            Bucket=BUCKET_NAME,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            },
        )
        print("  Default AES256 server-side encryption enabled.")

        # Lifecycle rule: expire evidence items after 1 day (constraint C8: short retention)
        s3.put_bucket_lifecycle_configuration(
            Bucket=BUCKET_NAME,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "ExpireEvidenceAfter1Day",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "evidence/"},
                        "Expiration": {"Days": 1},
                    }
                ]
            },
        )
        print("  Lifecycle expiration rule (1 day) applied.")
    except Exception as e:
        print(f"[ERROR] S3 provisioning: {e}")

def get_or_create_kms_key():
    print("\n--- Checking KMS Key for Policy Bundles ---")
    try:
        aliases = kms.list_aliases().get("Aliases", [])
        target_alias = "alias/kiosk-policy-signer"
        key_id = None
        for alias in aliases:
            if alias.get("AliasName") == target_alias:
                key_id = alias.get("TargetKeyId")
                print(f"[EXISTS] KMS signing key found with alias {target_alias}: {key_id}")
                break

        if not key_id:
            print(f"[CREATING] Asymmetric KMS Key (ECC_NIST_P256 / ECDSA_SHA_256)...")
            res = kms.create_key(
                Description="Key for signing offline policy bundles and credentials",
                KeyUsage="SIGN_VERIFY",
                KeySpec="ECC_NIST_P256",
            )
            key_id = res["KeyMetadata"]["KeyId"]
            kms.create_alias(AliasName=target_alias, TargetKeyId=key_id)
            print(f"[READY] KMS Key created: {key_id} with alias {target_alias}")

        return key_id
    except Exception as e:
        print(f"[WARN] KMS key creation failed ({e}); fallback to HMAC / software key if needed.")
        return None

if __name__ == "__main__":
    provision_dynamodb()
    provision_rekognition()
    provision_s3()
    kms_key = get_or_create_kms_key()

    # Save output config for backend Lambdas / services
    config_data = {
        "REGION": REGION,
        "ACCOUNT_ID": ACCOUNT_ID,
        "BUCKET_NAME": BUCKET_NAME,
        "COLLECTION_ID": COLLECTION_ID,
        "KMS_KEY_ALIAS": "alias/kiosk-policy-signer",
        "KMS_KEY_ID": kms_key,
        "TABLES": {k: k for k in TABLES.keys()},
    }
    with open("config/aws_resources.json", "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)
    print("\nResource metadata saved to config/aws_resources.json")
