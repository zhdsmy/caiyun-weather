#!/usr/bin/env python3
"""Optional live contract check for caiyun-weather.

Runs only when CAIYUN_TOKEN is present. It prints a redacted summary and never
prints tokens, full API URLs, or full weather payloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weather_data.py"


def main() -> None:
    if not os.environ.get("CAIYUN_TOKEN"):
        print("skip - CAIYUN_TOKEN is not configured")
        return

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--lng",
            "116.4075",
            "--lat",
            "39.9040",
            "--location",
            "北京",
            "--format",
            "bundle",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"weather_data.py failed with non-JSON output: {exc}") from exc
        error = payload.get("error") or {}
        code = error.get("code")
        if code in {"network_error", "timeout", "http_error"}:
            print(f"skip - live API unavailable: {code}")
            return
        raise SystemExit(f"weather_data.py returned error: {error}")

    bundle = json.loads(result.stdout)
    if "error" in bundle:
        raise SystemExit(f"weather_data.py returned error: {bundle.get('error')}")

    data = bundle.get("json") or {}
    brief = bundle.get("brief") or {}
    checks = {
        "schema_version": bundle.get("schema_version"),
        "location": data.get("location"),
        "same_location": data.get("location") == brief.get("location"),
        "location_quality": brief.get("location_quality"),
        "has_keypoint_hourly": bool(data.get("keypoint_hourly")),
        "has_minutely": isinstance(data.get("minutely"), dict),
        "has_rain_now": isinstance(brief.get("rain_now"), dict),
        "has_first_window": "first_window" in (brief.get("rain") or {}),
        "has_peak_window": "peak_window" in (brief.get("rain") or {}),
        "days_count": len(data.get("days") or []),
    }
    required = (
        checks["schema_version"] == "6.4.0"
        and checks["same_location"]
        and checks["has_keypoint_hourly"]
        and checks["has_minutely"]
        and checks["has_rain_now"]
        and checks["has_first_window"]
        and checks["has_peak_window"]
        and checks["days_count"] >= 1
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    if not required:
        raise SystemExit("live check failed")


if __name__ == "__main__":
    main()
