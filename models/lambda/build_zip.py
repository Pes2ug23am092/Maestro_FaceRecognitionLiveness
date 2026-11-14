"""Build models/dist/hid-liveness-lambda.zip (Linux x86_64, Python 3.12) without Docker.

    python models/lambda/build_zip.py
Handler: handler.handler | Runtime: python3.12 | Architecture: x86_64 | Memory: >= 1024 MB | Timeout: 10 s.
Zip > 50 MB must be uploaded via S3 (aws lambda update-function-code --s3-bucket ... --s3-key ...); limit 250 MB unzipped.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

M = Path(__file__).resolve().parents[1]
BUILD, DIST = M / "build" / "lambda_pkg", M / "dist"
REPACK = "--repack" in sys.argv  # reuse the already-downloaded wheels in build/ (skip pip)
if not REPACK:
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
DIST.mkdir(exist_ok=True)
if not REPACK:
  subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(BUILD), "--platform",
                       "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64", "--platform",
                       "manylinux_2_27_x86_64", "--platform", "manylinux_2_17_x86_64", "--implementation", "cp", "--python-version", "3.12", "--only-binary=:all:",
                       "-r", str(M / "lambda" / "requirements-lambda.txt")])
shutil.rmtree(BUILD / "liveness", ignore_errors=True)
shutil.copytree(M / "liveness", BUILD / "liveness", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copy(M / "lambda" / "handler.py", BUILD / "handler.py")
(BUILD / "artifacts").mkdir(exist_ok=True)
for n in ("liveness_cnn.onnx", "face_detection_yunet_2023mar.onnx"):
    shutil.copy(M / "artifacts" / n, BUILD / "artifacts" / n)
# prune what inference does not need (Lambda limit: 250 MB unzipped). sympy/mpmath are only used by onnxruntime's
# offline tools/transformers sub-packages, which we also remove. cv2/data = Haar cascades (we use YuNet).
for rel in ("sympy", "mpmath", "onnxruntime/transformers", "onnxruntime/tools", "onnxruntime/quantization",
            "onnxruntime/datasets", "cv2/data", "numpy/tests", "numpy/_core/tests", "numpy/f2py", "numpy/typing/tests"):
    shutil.rmtree(BUILD / rel, ignore_errors=True)
for d in list(BUILD.glob("sympy*")) + list(BUILD.glob("mpmath*")):
    shutil.rmtree(d, ignore_errors=True)
for junk in list(BUILD.rglob("__pycache__")) + list(BUILD.glob("*.dist-info")) + list(BUILD.glob("bin")):
    shutil.rmtree(junk, ignore_errors=True)
out = DIST / "hid-liveness-lambda.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in BUILD.rglob("*"):
        if p.is_file():
            z.write(p, p.relative_to(BUILD).as_posix())
raw = sum(p.stat().st_size for p in BUILD.rglob("*") if p.is_file())
print(f"{out}: {out.stat().st_size / 1e6:.1f} MB zipped, {raw / 1e6:.1f} MB unzipped (limit 250 MB)")
