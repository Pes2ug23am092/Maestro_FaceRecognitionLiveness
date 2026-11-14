"""Reject face media, embeddings, or biometric payloads in offline JSON exports."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SENSITIVE_KEY = re.compile(
    r"(?:^face$|biometric|face[_-]?(?:image|frame|crop|embedding|template|media)|"
    r"(?:raw[_-]?)?(?:image|video|frame|embedding|template|portrait|media))",
    re.IGNORECASE,
)
DATA_URI = re.compile(r"^data:(?:image|video)/", re.IGNORECASE)
LONG_BASE64 = re.compile(r"^[A-Za-z0-9+/]{512,}={0,2}$")
MEDIA_PATH = re.compile(r"\.(?:png|jpe?g|webp|gif|mp4|mov|webm)$", re.IGNORECASE)


def scan_document(document: Any, path: str = "$", findings: list[str] | None = None) -> list[str]:
    if findings is None:
        findings = []
    if isinstance(document, dict):
        for key, value in document.items():
            child_path = f"{path}.{key}"
            if SENSITIVE_KEY.search(str(key)):
                findings.append(f"{child_path}: forbidden biometric/media field")
            scan_document(value, child_path, findings)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            scan_document(value, f"{path}[{index}]", findings)
    elif isinstance(document, str):
        if DATA_URI.match(document):
            findings.append(f"{path}: embedded image/video data URI")
        elif LONG_BASE64.fullmatch(document):
            findings.append(f"{path}: long base64-like payload")
        elif MEDIA_PATH.search(document):
            findings.append(f"{path}: media file reference")
    return findings


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            findings.append(f"{path}: path does not exist")

    if not files and not findings:
        findings.append("No JSON files found to scan")
    for file_path in files:
        try:
            document = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            findings.append(f"{file_path}: unable to parse JSON ({type(error).__name__})")
            continue
        findings.extend(f"{file_path}{finding[1:]}" for finding in scan_document(document))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="JSON exports of offline storage or sync payloads")
    args = parser.parse_args()
    findings = scan_paths(args.paths)
    if findings:
        print("FAIL: biometric/media content found or scan incomplete")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("PASS: no biometric/media fields found in scanned JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())