"""Fast deploy (no Docker): code zip -> Lambda; heavy deps zip -> S3 (loaded into /tmp at cold start); API Gateway HTTP API;
EventBridge keep-warm; log retention. Prefix hidb- so nothing of A's is touched.   python infra/deploy_zip.py"""
import io, json, shutil, subprocess, sys, time, zipfile
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
R, NAME, ROLE, KEY = "us-east-1", "hidb-kiosk-backend", "hidb-kiosk-lambda-role", "hidb/deps.zip"
s = boto3.session.Session(region_name=R)
acct = s.client("sts").get_caller_identity()["Account"]
BUCKET = f"kiosk-evidence-{acct}-{R}"
lam, apigw, logs, events, iam, s3 = (s.client(x) for x in ("lambda", "apigatewayv2", "logs", "events", "iam", "s3"))
BUILD = ROOT / "models" / "build"
HEAVY = Path("C:/PESU/HID/models/build/lambda_pkg")  # pruned numpy/opencv/onnxruntime wheels from models/lambda/build_zip.py

def zipdir(src: Path, dst: Path, skip=()):
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        for p in src.rglob("*"):
            if p.is_file() and not any(part in skip for part in p.relative_to(src).parts) and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(src).as_posix())

# 1. deps.zip (skipped with --extra-only: only the small extra zip + code are refreshed)
if "--extra-only" not in sys.argv:
    # 1. deps.zip = heavy wheels (minus our own code) + light pure/linux wheels for fastapi stack
    deps = BUILD / "deps"; shutil.rmtree(deps, ignore_errors=True)
    shutil.copytree(HEAVY, deps, ignore=shutil.ignore_patterns("liveness", "artifacts", "handler.py", "__pycache__"))
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(deps), "--platform", "manylinux2014_x86_64",
        "--platform", "manylinux_2_17_x86_64", "--platform", "manylinux_2_28_x86_64", "--implementation", "cp", "--python-version", "3.12",
        "--only-binary=:all:", "--no-deps", "fastapi==0.115.6", "starlette==0.41.3", "pydantic==2.10.4", "pydantic-core==2.27.2",
        "typing-extensions", "annotated-types", "anyio", "idna", "sniffio", "mangum==0.19.0", "python-multipart==0.0.20", "python-dotenv==1.0.1"])
    depzip = BUILD / "deps.zip"; zipdir(deps, depzip)
    print(f"deps.zip {depzip.stat().st_size/1e6:.0f} MB -> s3://{BUCKET}/{KEY}", flush=True)
    s3.upload_file(str(depzip), BUCKET, KEY)


# 1b. extra wheels (cryptography is needed by the KMS signature code)
extra = BUILD / "extra"; shutil.rmtree(extra, ignore_errors=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(extra), "--platform", "manylinux2014_x86_64",
    "--platform", "manylinux_2_17_x86_64", "--platform", "manylinux_2_28_x86_64", "--implementation", "cp", "--python-version", "3.12",
    "--only-binary=:all:", "--no-deps", "cryptography", "cffi", "pycparser"])
EXTRA_KEY = "hidb/deps_extra.zip"; extrazip = BUILD / "deps_extra.zip"; zipdir(extra, extrazip); s3.upload_file(str(extrazip), BUCKET, EXTRA_KEY)
print(f"deps_extra.zip {extrazip.stat().st_size/1e6:.1f} MB uploaded", flush=True)

# 2. code zip
code = BUILD / "code"; shutil.rmtree(code, ignore_errors=True); code.mkdir(parents=True)
shutil.copytree(ROOT / "backend", code / "backend", ignore=shutil.ignore_patterns("__pycache__", "requirements*.txt"))
shutil.copytree(ROOT / "config", code / "config")
shutil.copytree(ROOT / "models" / "liveness", code / "models" / "liveness", ignore=shutil.ignore_patterns("__pycache__"))
(code / "models" / "artifacts").mkdir(parents=True)
for n in ("liveness_cnn.onnx", "face_detection_yunet_2023mar.onnx"):
    shutil.copy(ROOT / "models" / "artifacts" / n, code / "models" / "artifacts" / n)
shutil.copy(ROOT / "lambda_boot.py", code / "lambda_boot.py")
codezip = BUILD / "code.zip"; zipdir(code, codezip)
print(f"code.zip {codezip.stat().st_size/1e6:.1f} MB", flush=True)

# 3. Lambda
role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
cfg = dict(Role=role_arn, Runtime="python3.12", Handler="lambda_boot.handler", MemorySize=2048, Timeout=60,
           EphemeralStorage={"Size": 1024}, TracingConfig={"Mode": "Active"},
           Environment={"Variables": {"DEPS_BUCKET": BUCKET, "DEPS_KEY": KEY, "EXTRA_KEY": EXTRA_KEY, "OTP_DEMO": "1", "TIER1_QUALITY_MIN": "0.15"}})
blob = codezip.read_bytes()
try:
    lam.get_function(FunctionName=NAME)
    lam.update_function_code(FunctionName=NAME, ZipFile=blob); lam.get_waiter("function_updated").wait(FunctionName=NAME)
    lam.update_function_configuration(FunctionName=NAME, **cfg)
except lam.exceptions.ResourceNotFoundException:
    for attempt in range(10):
        try:
            lam.create_function(FunctionName=NAME, Code={"ZipFile": blob}, Architectures=["x86_64"], **cfg); break
        except lam.exceptions.InvalidParameterValueException as e:
            if "assume" not in str(e).lower() or attempt == 9: raise
            time.sleep(5)
lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
fn_arn = lam.get_function(FunctionName=NAME)["Configuration"]["FunctionArn"]
try: logs.create_log_group(logGroupName=f"/aws/lambda/{NAME}")
except logs.exceptions.ResourceAlreadyExistsException: pass
logs.put_retention_policy(logGroupName=f"/aws/lambda/{NAME}", retentionInDays=7)

# 4. API Gateway HTTP API
apis = [a for a in apigw.get_apis()["Items"] if a["Name"] == NAME]
if apis: api = apis[0]
else:
    api = apigw.create_api(Name=NAME, ProtocolType="HTTP", CorsConfiguration={"AllowOrigins": ["*"], "AllowMethods": ["*"], "AllowHeaders": ["*"]})
    integ = apigw.create_integration(ApiId=api["ApiId"], IntegrationType="AWS_PROXY", IntegrationUri=fn_arn, PayloadFormatVersion="2.0")
    apigw.create_route(ApiId=api["ApiId"], RouteKey="$default", Target=f"integrations/{integ['IntegrationId']}")
    apigw.create_stage(ApiId=api["ApiId"], StageName="$default", AutoDeploy=True)
for sid, princ, src in (("apigw-invoke", "apigateway.amazonaws.com", f"arn:aws:execute-api:{R}:{acct}:{api['ApiId']}/*/*"),
                        ("events-warm", "events.amazonaws.com", f"arn:aws:events:{R}:{acct}:rule/hidb-kiosk-keepwarm")):
    try: lam.add_permission(FunctionName=NAME, StatementId=sid, Action="lambda:InvokeFunction", Principal=princ, SourceArn=src)
    except lam.exceptions.ResourceConflictException: pass
events.put_rule(Name="hidb-kiosk-keepwarm", ScheduleExpression="rate(5 minutes)", State="ENABLED")
events.put_targets(Rule="hidb-kiosk-keepwarm", Targets=[{"Id": "1", "Arn": fn_arn}])
url = api["ApiEndpoint"]
(ROOT / "config" / "deployed.json").write_text(json.dumps({"region": R, "function": NAME, "api_id": api["ApiId"], "url": url, "kiosk": url + "/kiosk"}, indent=2))
print("DEPLOYED:", url, "| kiosk:", url + "/kiosk")
