"""Lambda entry for the zip deployment: heavy deps (numpy, opencv, onnxruntime, fastapi...) live in S3 as deps.zip and are
unpacked into /tmp on cold start (zip size limit is 250 MB). Warm invocations skip this entirely."""
import os
import sys
import zipfile

import boto3

DEPS = "/tmp/deps"
if not os.path.exists(DEPS + "/.ok"):
    os.makedirs(DEPS, exist_ok=True)
    boto3.client("s3").download_file(os.environ["DEPS_BUCKET"], os.environ["DEPS_KEY"], "/tmp/deps.zip")
    with zipfile.ZipFile("/tmp/deps.zip") as z:
        z.extractall(DEPS)
    os.remove("/tmp/deps.zip")
    if os.environ.get("EXTRA_KEY"):  # small extra wheels (cryptography for KMS signature handling)
        boto3.client("s3").download_file(os.environ["DEPS_BUCKET"], os.environ["EXTRA_KEY"], "/tmp/extra.zip")
        with zipfile.ZipFile("/tmp/extra.zip") as z:
            z.extractall(DEPS)
        os.remove("/tmp/extra.zip")
    open(DEPS + "/.ok", "w").close()
sys.path.insert(0, DEPS)

from backend.main import handler as _handler  # noqa: E402


def handler(event, context):
    return _handler(event, context)
