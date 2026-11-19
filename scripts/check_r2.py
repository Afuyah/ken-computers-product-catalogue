#!/usr/bin/env python
"""Verify Cloudflare R2 credentials, the bucket and the CDN domain.

Fill the R2_* values in .env, then run:

    python scripts/check_r2.py

It uploads a tiny object, reads it back through a presigned URL, tries the
public CDN URL, then deletes it. Nothing else in the bucket is touched.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

REQUIRED = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
TEST_KEY = "_healthcheck/hello.txt"
PAYLOAD = b"ken computers r2 healthcheck"
# r2.dev sits behind Cloudflare, which rejects script user-agents with error 1010.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "*/*"})
    return urllib.request.urlopen(request, timeout=20).read()


def fail(message: str) -> int:
    print(f"\nFAILED: {message}")
    return 1


def main() -> int:
    values = {name: os.getenv(name, "").strip() for name in REQUIRED}
    missing = [name for name, value in values.items() if not value]
    if missing:
        return fail("missing in .env: " + ", ".join(missing))

    cdn = os.getenv("CDN_BASE_URL", "").strip().rstrip("/")
    print(f"bucket   : {values['R2_BUCKET']}")
    print(f"account  : {values['R2_ACCOUNT_ID']}")
    print(f"cdn      : {cdn or '(not set — will skip the CDN check)'}\n")

    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError:
        return fail("boto3 is not installed. Run: pip install -r requirements.txt")

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{values['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=values["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 2}),
    )
    bucket = values["R2_BUCKET"]

    # 1. list (proves the token can reach the bucket)
    try:
        client.list_objects_v2(Bucket=bucket, MaxKeys=1)
        print("PASS  token can read the bucket")
    except Exception as exc:  # noqa: BLE001
        return fail(f"cannot access bucket: {exc}")

    # 2. write
    try:
        client.put_object(
            Bucket=bucket,
            Key=TEST_KEY,
            Body=PAYLOAD,
            ContentType="text/plain",
            CacheControl="public, max-age=60",
        )
        print("PASS  upload works")
    except Exception as exc:  # noqa: BLE001
        return fail(f"upload failed: {exc}")

    # 3. presigned download (this is what /d/<id> redirects to)
    try:
        url = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": TEST_KEY}, ExpiresIn=120
        )
        body = fetch(url)
        print("PASS  presigned download works" if body == PAYLOAD else "FAIL  presigned body mismatch")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  presigned download: {exc}")

    # 4. public CDN domain
    if cdn:
        try:
            body = fetch(f"{cdn}/{TEST_KEY}")
            print("PASS  CDN domain serves the file" if body == PAYLOAD else "FAIL  CDN body mismatch")
        except urllib.error.HTTPError as exc:
            print(f"FAIL  CDN returned HTTP {exc.code} — is cdn.{'your domain'} connected to the bucket?")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  CDN unreachable: {exc}")
    else:
        print("SKIP  CDN check (CDN_BASE_URL not set)")

    # 5. clean up
    try:
        client.delete_object(Bucket=bucket, Key=TEST_KEY)
        print("PASS  cleanup")
    except Exception:  # noqa: BLE001
        print("WARN  could not delete the test object")

    print("\nDone. If every line says PASS, set the same values in Railway and deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
