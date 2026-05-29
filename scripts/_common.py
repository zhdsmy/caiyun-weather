#!/usr/bin/env python3
"""caiyun-weather 脚本共用工具。

目标：统一 stdout JSON 输出、环境变量加载、HTTP 请求、数值处理，
让 weather_data.py 和 geocode.py 共享同一套实现，避免重复。

保持纯数据层，不做中文转换。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_ENV_SEARCH = ()  # 脚本不再自行加载 .env，保留常量以兼容旧引用


def _error_code_from_message(message: str) -> str:
    if "缺少必填环境变量" in message or "缺少高德" in message or "高德 Web 服务 Key" in message:
        return "missing_env"
    if "缺少定位信息" in message:
        return "missing_location"
    if "经纬度" in message:
        return "invalid_location"
    if "预报天数" in message or "WEATHER_DAILY_STEPS" in message:
        return "invalid_days"
    if "WEATHER_TZ" in message:
        return "invalid_timezone"
    if "高德 API" in message:
        return "amap_error"
    if "彩云 API" in message:
        return "caiyun_error"
    if "HTTP" in message:
        return "http_error"
    if "网络请求失败" in message:
        return "network_error"
    if "超时" in message:
        return "timeout"
    if "非 JSON" in message:
        return "invalid_response"
    return "error"


def normalize_error_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy {"error": "..."} payloads to a structured error object."""
    if "error" not in payload or isinstance(payload.get("error"), dict):
        return payload

    raw = dict(payload)
    message = str(raw.pop("error"))
    hint = raw.pop("hint", None)
    code = str(raw.pop("error_code", "") or _error_code_from_message(message))
    error: dict[str, Any] = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    if raw:
        error["details"] = raw
    return {"ok": False, "error": error}


def emit(payload: dict[str, Any], *, exit_code: int = 0) -> None:
    """始终向 stdout 输出可解析 JSON，便于 LLM/Cron 消费。"""
    if exit_code:
        payload = normalize_error_payload(payload)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if exit_code:
        sys.exit(exit_code)


def load_env_file(path: Path | None = None) -> list[str]:
    """保留接口兼容：脚本本身不加载任何 .env 文件，环境变量由调用方/AI 工具准备。

    始终返回空列表。继续暴露此函数仅为兼容旧 import。
    """
    return []


class RequestError(Exception):
    """request_json 在 raise_errors=True 时抛出，便于调用方在 --check 等场景自行处理。"""


def request_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    service: str,
    timeout: int = 15,
    raise_errors: bool = False,
) -> dict[str, Any]:
    """GET url?params，统一把网络/解析错误收敛。

    默认把错误 emit 成 JSON 并退出（exit 1）；
    传 raise_errors=True 时改抛 RequestError，便于调用方组合多次请求后一次性输出。
    """
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        if query:
            url = f"{url}?{query}"

    def _fail(message: str) -> dict[str, Any]:
        if raise_errors:
            raise RequestError(message)
        emit({"error": message}, exit_code=1)
        return {}

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return _fail(f"HTTP {exc.code}: {service}接口请求失败")
    except urllib.error.URLError as exc:
        return _fail(f"网络请求失败: {exc.reason}")
    except TimeoutError:
        return _fail(f"{service}接口请求超时")
    except json.JSONDecodeError as exc:
        return _fail(f"{service}接口返回非 JSON: {exc}")
    except Exception as exc:  # noqa: BLE001 - CLI 入口兜底为错误
        return _fail(f"请求失败: {exc}")
    return {}


def safe(obj: Any, *keys: Any) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, (list, tuple)) and isinstance(key, int) and 0 <= key < len(obj):
            obj = obj[key]
        else:
            return None
    return obj


def num(value: Any, ndigits: int | None = None) -> Any:
    """安全转数字，失败返原值。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return value
    return round(number, ndigits) if ndigits is not None else number


def prob_percent(value: Any) -> int | None:
    """将 0-1 或 0-100 概率统一成 0-100 整数百分比。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def prob_fraction(value: Any) -> float | None:
    """将概率统一成 0-1 浮点数。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if number > 1:
        number /= 100
    return round(max(0.0, min(1.0, number)), 3)


def parse_api_time(value: Any, tz: timezone) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def validate_lng_lat(lng: float, lat: float) -> None:
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        emit({"error": "经纬度超出合法范围", "lng": lng, "lat": lat}, exit_code=1)


def parse_lng_lat(lng_raw: Any, lat_raw: Any) -> tuple[float, float]:
    try:
        lng = float(lng_raw)
        lat = float(lat_raw)
    except (TypeError, ValueError):
        emit({"error": "经纬度必须是数字", "lng": lng_raw, "lat": lat_raw}, exit_code=1)
    validate_lng_lat(lng, lat)
    return lng, lat


def pick_first_string(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, str) else None
    return None


def amap_key() -> str | None:
    return os.environ.get("AMAP_KEY") or os.environ.get("GAODE_KEY")


def default_city() -> str | None:
    return (
        os.environ.get("WEATHER_DEFAULT_CITY")
        or os.environ.get("DEFAULT_CITY")
        or None
    )


def default_output_dir() -> Path:
    raw = os.environ.get("WEATHER_OUTPUT_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "caiyun-weather" / "outputs"


def display_path(path: Path) -> str:
    """Return a user-facing path without exposing the local home directory."""
    try:
        resolved = path.expanduser().resolve()
        home = Path.home().resolve()
        return "~" if resolved == home else f"~/{resolved.relative_to(home)}"
    except ValueError:
        return str(path)
    except Exception:  # noqa: BLE001 - display helper must not break CLI output
        return str(path)


def default_output_dir_display() -> str:
    raw = os.environ.get("WEATHER_OUTPUT_DIR")
    if raw:
        return display_path(Path(raw))
    return "~/.cache/caiyun-weather/outputs"


def env_hint() -> str:
    """通用化提示：仅说明缺什么环境变量，不指引具体加载路径。"""
    return "请通过调用方所在的 AI 工具或 shell 注入相应环境变量后重试。"


def record_log(event: dict[str, Any]) -> None:
    """如配置 WEATHER_LOG_PATH，则向其追加一行 JSONL；未配置则静默跳过。"""
    raw = os.environ.get("WEATHER_LOG_PATH")
    if not raw:
        return
    path = Path(raw).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime as _dt
        payload = dict(event)
        payload.setdefault("ts", _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程
        return


AMAP_LEVEL_QUALITY = {
    "门牌号": "high",
    "单元号": "high",
    "楼栋号": "high",
    "热点商圈": "high",
    "兴趣点": "high",
    "POI": "high",
    "道路": "medium",
    "交叉路口": "medium",
    "公交线路": "medium",
    "地铁线路": "medium",
    "铁路": "medium",
    "街道": "medium",
    "乡镇": "medium",
    "开发区": "medium",
    "区县": "low",
    "城市": "low",
    "省": "low",
    "国家": "low",
}


def amap_quality(level: Any) -> str:
    if not isinstance(level, str):
        return "unknown"
    for key, value in AMAP_LEVEL_QUALITY.items():
        if key in level:
            return value
    return "unknown"


def normalize_amap(data: dict[str, Any]) -> None:
    if str(data.get("status")) != "1":
        emit({
            "error": f"高德 API status: {data.get('status')}",
            "info": data.get("info"),
            "infocode": data.get("infocode"),
        }, exit_code=1)
