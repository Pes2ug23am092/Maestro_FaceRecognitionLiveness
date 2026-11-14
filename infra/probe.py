import os
import boto3
from dotenv import load_dotenv

# Load local environment if present
load_dotenv()

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
s = boto3.Session(region_name=REGION)
c = s.client

checks = {
    "STS identity": lambda: c("sts").get_caller_identity(),
    "S3": lambda: c("s3").list_buckets(),
    "DynamoDB": lambda: c("dynamodb").list_tables(Limit=1),
    "Lambda": lambda: c("lambda").list_functions(MaxItems=1),
    "ECR (container image)": lambda: c("ecr").describe_repositories(maxResults=1),
    "API Gateway": lambda: c("apigateway").get_rest_apis(limit=1),
    "Cognito": lambda: c("cognito-idp").list_user_pools(MaxResults=1),
    "KMS": lambda: c("kms").list_keys(Limit=1),
    "WAF": lambda: c("wafv2").list_web_acls(Scope="REGIONAL"),
    "Rekognition": lambda: c("rekognition").list_collections(MaxResults=1),
    "SES": lambda: c("sesv2").list_email_identities(PageSize=1),
    "SNS": lambda: c("sns").list_topics(),
    "CloudFront": lambda: c("cloudfront").list_distributions(MaxItems="1"),
    "X-Ray": lambda: c("xray").get_sampling_rules(),
    "Face Liveness (billed per session, run once)": lambda: c("rekognition").create_face_liveness_session(),
}

rows = ["| Service | Result | Note |", "|---|---|---|"]
print(f"Running sandbox probe in region: {REGION}...")

for name, fn in checks.items():
    try:
        fn()
        rows.append(f"| {name} | OK | |")
        print(f"[OK]   {name}")
    except Exception as e:
        err_msg = str(e)[:80].replace("|", "/").replace("\n", " ")
        rows.append(f"| {name} | FAIL | {err_msg} |")
        print(f"[FAIL] {name}: {err_msg}")

os.makedirs("docs", exist_ok=True)
probe_out_path = os.path.join("docs", "sandbox-probe.md")
with open(probe_out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(rows) + "\n")

print(f"\nProbe completed and saved to {probe_out_path}")
