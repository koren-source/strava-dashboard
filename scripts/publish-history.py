#!/usr/bin/env python3
"""Deliver a completed Strava scan to private dashboard history with a receipt."""
import json
import os
import re
from pathlib import Path
import sys
import time
import urllib.request
import urllib.parse


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Private fitness capture must not redirect")


def publish(payload, request=None):
    base = os.environ.get("QMC_BASE", "https://q-mission-control-ten.vercel.app").rstrip("/")
    secret = os.environ.get("FITNESS_CAPTURE_SECRET")
    if not secret:
        raise ValueError("FITNESS_CAPTURE_SECRET is required for durable capture")
    origin = urllib.parse.urlsplit(base)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment:
        raise ValueError("QMC_BASE must be an HTTPS origin")
    body = json.dumps(payload).encode()
    if len(body) > 4_000_000:
        raise ValueError("Fitness capture exceeds upload limit; previous history preserved")
    request = request or urllib.request.build_opener(NoRedirect()).open
    for attempt in range(3):
        try:
            req = urllib.request.Request(base + "/api/fitness/capture", data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret}, method="POST")
            with request(req, timeout=60) as response:
                receipt = json.load(response)
            if receipt.get("ok") is not True or not isinstance(receipt.get("captureId"), str) or not re.fullmatch(r"[a-f0-9]{64}", receipt["captureId"]) or type(receipt.get("rides")) is not int or receipt["rides"] != len(payload.get("rides", [])):
                raise ValueError("Fitness server did not confirm the complete activity capture")
            return receipt
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


if __name__ == "__main__":
    try:
        data = json.loads(Path(os.environ["STRAVA_CAPTURE_PATH"]).read_text())
        data["recommendation"] = json.loads((Path(__file__).resolve().parents[1] / "data/recommendation.json").read_text())
        result = publish(data)
        print("Private fitness history saved: " + result["captureId"])
    except Exception as error:
        # Exception strings can contain service URLs; log only a safe class.
        print("Private fitness capture failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
