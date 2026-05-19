#!/usr/bin/env python3
"""Offline smoke tests for caiyun-weather.

These tests intentionally avoid real API calls. They verify the CLI contract,
schema files, mock data shape, and import-time syntax using only stdlib tools.
"""

from __future__ import annotations

import json
import py_compile
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMAS = ROOT / "schemas"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "weather_data.py"), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


def load_cli_json(*args: str) -> dict:
    return json.loads(run_cli(*args).stdout)


def test_python_sources_compile() -> None:
    for path in sorted(SCRIPTS.glob("*.py")):
        py_compile.compile(str(path), doraise=True)


def test_schema_files_are_valid_json() -> None:
    for path in sorted(SCHEMAS.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            json.load(fh)


def test_weather_schema_tracks_current_output_fields() -> None:
    with (SCHEMAS / "weather.json").open(encoding="utf-8") as fh:
        schema = json.load(fh)
    props = schema["properties"]
    alert_props = schema["$defs"]["alert"]["properties"]
    realtime_props = props["realtime"]["properties"]
    today_props = props["today"]["properties"]
    day_props = schema["$defs"]["day"]["properties"]

    for key in ("keypoint_hourly", "minutely"):
        assert key in props
    assert "schema_version" in props
    for key in (
        "precip_nearest",
        "visibility_level",
        "life_index",
        "sunrise_today",
        "sunset_today",
        "is_daytime",
    ):
        assert key in realtime_props
    for key in (
        "skycon_day",
        "skycon_night",
        "precip_max",
        "precip_day_prob",
        "precip_night_prob",
    ):
        assert key in today_props
    for key in ("sunrise", "sunset"):
        assert key in day_props
    assert "code" in alert_props


def test_mock_weather_json_contract() -> None:
    for scenario in ("sunny", "rain", "alert"):
        data = load_cli_json("--mock", scenario, "--format", "json")
        assert data["schema_version"] == "6.4.0"
        assert data["mock"] == scenario
        assert data["provider"]["weather"]["id"] == "caiyun"
        assert data["keypoint_hourly"]
        assert isinstance(data["minutely"]["available"], bool)
        assert "precip_nearest" in data["realtime"]
        assert "life_index" in data["realtime"]
        assert "skycon_day" in data["today"]
        assert "precip_night_prob" in data["today"]
        assert len(data["days"]) == 7
        assert data["days"][0]["sunrise"]
        assert data["days"][0]["sunset"]


def test_mock_brief_contract() -> None:
    for scenario in ("sunny", "rain", "alert"):
        data = load_cli_json("--mock", scenario, "--format", "brief")
        assert data["schema_version"] == "6.4.0"
        assert data["location"] == "示例地点"
        assert data["keywords"]
        assert "rain_now" in data
        assert "today_split" in data
        assert "full_day_precip_max" in data["today_split"]
        assert "day_night_peak_precip" in data["today_split"]
        assert "minutely" in data
        assert isinstance(data["risks"], list)
        assert data["umbrella"]["code"] in {"carry_today", "carry_tomorrow_morning", "none"}
        assert "first_window" in data["rain"]
        assert "peak_window" in data["rain"]
        assert "windows_chronological" in data["rain"]

    alert = load_cli_json("--mock", "alert", "--format", "brief")
    assert alert["alerts"][0]["code"] == "0503"
    assert alert["alerts"][0]["level"] == "03"


def test_mock_bundle_contract() -> None:
    data = load_cli_json("--mock", "rain", "--format", "bundle")
    assert data["schema_version"] == "6.4.0"
    assert data["json"]["schema_version"] == "6.4.0"
    assert data["brief"]["schema_version"] == "6.4.0"
    assert data["json"]["mock"] == "rain"
    assert data["brief"]["location"] == data["json"]["location"]
    assert data["brief"]["rain"]["peak_window"] == data["brief"]["rain"]["windows"][0]
    assert data["brief"]["rain"]["first_window"] == data["brief"]["rain"]["windows_chronological"][0]
    assert data["brief"]["today_split"]["full_day_precip_max"] == data["json"]["today"]["precip_max"]


def test_mock_short_contract() -> None:
    output = run_cli("--mock", "rain", "--format", "short").stdout.strip()
    assert "示例地点" in output
    assert "出门建议" in output


def test_error_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "weather_data.py"), "--mock", "rain", "--days", "0"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_days"
    assert "预报天数" in payload["error"]["message"]


def test_mock_daily_astro_has_one_entry_per_day() -> None:
    sys.path.insert(0, str(SCRIPTS))
    import weather_data  # noqa: PLC0415

    steps = 7
    response = weather_data.build_mock_weather_response(
        "sunny",
        datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        daily_steps=steps,
    )
    astro = response["result"]["daily"]["astro"]
    assert len(astro) == steps
    assert [item["date"] for item in astro] == [
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
        "2026-05-22",
        "2026-05-23",
        "2026-05-24",
        "2026-05-25",
    ]


def main() -> None:
    tests = [
        test_python_sources_compile,
        test_schema_files_are_valid_json,
        test_weather_schema_tracks_current_output_fields,
        test_mock_weather_json_contract,
        test_mock_brief_contract,
        test_mock_bundle_contract,
        test_mock_short_contract,
        test_error_contract,
        test_mock_daily_astro_has_one_entry_per_day,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")


if __name__ == "__main__":
    main()
