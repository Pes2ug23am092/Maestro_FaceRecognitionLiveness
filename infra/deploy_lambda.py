"""Deploy the backend as a Lambda container image behind an API Gateway HTTP API (+ keep-warm schedule, X-Ray, log retention).

    (AWS creds in env)  python infra/deploy_lambda.py            # first deploy or update
Needs: docker engine running, local image hid-kiosk-backend:latest, IAM role hidb-kiosk-lambda-role (see memory.md).
Everything is prefixed hidb- / named hid-kiosk-backend so it never collides with A's resources. Writes config/deployed.json."""
import json
import subprocess
import time
from pathlib import Path

import boto3

R, NAME, ROLE, REPO = "us-east-1", "hidb-kiosk-backend", "hidb-kiosk-lambda-role", "hidb-kiosk-backend"
s = boto3.session.Session(region_name=R)
acct = s.client("sts").get_caller_identity()["Account"]
ecr, lam, apigw, logs, events, iam = (s.client(x) for x in ("ecr", "lambda", "apigatewayv2", "logs", "events", "iam"))

# 1. ECR repo + push
try:
    ecr.create_repository(repositoryName=REPO, imageScanningConfiguration={"scanOnPush": False})
except ecr.exceptions.RepositoryAlreadyExistsException:
    pass
registry = f"{acct}.dkr.ecr.{R}.amazonaws.com"
tok = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
import base64
pw = base64.b64decode(tok).decode().split(":", 1)[1]
subprocess.run(["docker", "login", "-u", "AWS", "--password-stdin", registry], input=pw.encode(), check=True, capture_output=True)
uri = f"{registry}/{REPO}:latest"
subprocess.run(["docker", "tag", "hid-kiosk-backend:latest", uri], check=True)
print("pushing image (first push is large)...", flush=True)
subprocess.run(["docker", "push", uri], check=True)
role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]

# 2. Lambda
env = {"Variables": {"OTP_DEMO": "1", "TIER1_QUALITY_MIN": "0.15"}}
cfg = dict(Role=role_arn, MemorySize=2048, Timeout=30, TracingConfig={"Mode": "Active"}, Environment=env)
try:
    lam.get_function(FunctionName=NAME)
    lam.update_function_code(FunctionName=NAME, ImageUri=uri)
    lam.get_waiter("function_updated").wait(FunctionName=NAME)
    lam.update_function_configuration(FunctionName=NAME, **cfg)
except lam.exceptions.ResourceNotFoundException:
    for attempt in range(8):  # a new role can take a few seconds to become assumable
        try:
            lam.create_function(FunctionName=NAME, PackageType="Image", Code={"ImageUri": uri}, Architectures=["x86_64"], **cfg)
            break
        except lam.exceptions.InvalidParameterValueException as e:
            if "assume" not in str(e).lower() or attempt == 7:
                raise
            time.sleep(5)
lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
fn_arn = lam.get_function(FunctionName=NAME)["Configuration"]["FunctionArn"]
try:
    logs.create_log_group(logGroupName=f"/aws/lambda/{NAME}")
except logs.exceptions.ResourceAlreadyExistsException:
    pass
logs.put_retention_policy(logGroupName=f"/aws/lambda/{NAME}", retentionInDays=7)

# 3. API Gateway HTTP API -> Lambda (payload v2, $default route, auto-deploy stage)
apis = [a for a in apigw.get_apis()["Items"] if a["Name"] == NAME]
if apis:
    api = apis[0]
else:
    api = apigw.create_api(Name=NAME, ProtocolType="HTTP", CorsConfiguration={"AllowOrigins": ["*"], "AllowMethods": ["*"], "AllowHeaders": ["*"]})
    integ = apigw.create_integration(ApiId=api["ApiId"], IntegrationType="AWS_PROXY", IntegrationUri=fn_arn, PayloadFormatVersion="2.0")
    apigw.create_route(ApiId=api["ApiId"], RouteKey="$default", Target=f"integrations/{integ['IntegrationId']}")
    apigw.create_stage(ApiId=api["ApiId"], StageName="$default", AutoDeploy=True)
try:
    lam.add_permission(FunctionName=NAME, StatementId="apigw-invoke", Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
                       SourceArn=f"arn:aws:execute-api:{R}:{acct}:{api['ApiId']}/*/*")
except lam.exceptions.ResourceConflictException:
    pass
url = api["ApiEndpoint"]

# 4. EventBridge keep-warm every 5 minutes
events.put_rule(Name="hidb-kiosk-keepwarm", ScheduleExpression="rate(5 minutes)", State="ENABLED", Description="keep kiosk Lambda + connections warm")
events.put_targets(Rule="hidb-kiosk-keepwarm", Targets=[{"Id": "1", "Arn": fn_arn}])
try:
    lam.add_permission(FunctionName=NAME, StatementId="events-warm", Action="lambda:InvokeFunction", Principal="events.amazonaws.com",
                       SourceArn=f"arn:aws:events:{R}:{acct}:rule/hidb-kiosk-keepwarm")
except lam.exceptions.ResourceConflictException:
    pass

Path(__file__).resolve().parents[1].joinpath("config", "deployed.json").write_text(json.dumps(
    {"region": R, "function": NAME, "api_id": api["ApiId"], "url": url, "kiosk": url + "/kiosk", "ecr_repo": REPO}, indent=2))
print("DEPLOYED:", url, "| kiosk:", url + "/kiosk")
