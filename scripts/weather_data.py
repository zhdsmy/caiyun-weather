#!/usr/bin/env python3
"""彩云天气数据提取器 — 纯数据层，不做天气中文转换。

能力：
  - 经纬度 -> 彩云天气 JSON
  - 结构化地址 -> 高德地理编码 -> 彩云天气 JSON
  - 经纬度缺少位置名 -> 可选逆地理编码补齐 location
  - --check 诊断：检查彩云 Token、高德 Key 是否注入

输出：始终向 stdout 输出 JSON；错误也输出 JSON，便于任意 AI 工具/CLI/Cron 消费。

环境变量由调用方负责注入（AI 工具运行时、shell 等），脚本只读取，不主动加载 .env。

关键环境变量：
  CAIYUN_TOKEN            彩云 API token（所有天气查询必填）
  AMAP_KEY / GAODE_KEY    高德 Web 服务 Key（地址查天气、逆地理编码必填）
  WEATHER_LNG/LAT         默认经纬度（也接受别名 LONGITUDE/LATITUDE）——经纬度模式定位
  WEATHER_ADDRESS         默认结构化地址——地址模式定位（需高德 Key，与经纬度二选一）
  WEATHER_LOCATION        输出用位置名（可选，不设则由高德 formatted_address 自动推导）
  WEATHER_CITY            本次地理编码限定城市
  WEATHER_DEFAULT_CITY    未显式传城市时的兜底（也接受 DEFAULT_CITY）
  WEATHER_TZ              时区偏移小时，默认 8
  WEATHER_DAILY_STEPS     预报天数（1–15），默认 7；也可用 --days N 覆盖
  WEATHER_OUTPUT_DIR      完整播报输出目录，默认 ~/.cache/caiyun-weather/outputs
  WEATHER_CACHE_DIR       缓存目录，默认 ~/.cache/caiyun-weather
  WEATHER_CACHE_SECONDS   缓存秒数，默认 0 表示不缓存
  WEATHER_LOG_PATH        可选：写 JSONL 调用日志的路径
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (
    amap_key,
    amap_quality,
    default_city,
    default_output_dir,
    emit,
    env_hint,
    load_env_file,
    normalize_amap,
    num,
    parse_api_time,
    parse_lng_lat,
    pick_first_string,
    prob_fraction,
    prob_percent,
    record_log,
    RequestError,
    request_json,
    safe,
)

import formatters  # noqa: E402 - 本地模块

CAIYUN_ENDPOINT_TEMPLATE = "https://api.caiyunapp.com/v2.6/{token}/{lng},{lat}/weather?alert=true&hourlysteps=24&dailysteps={dailysteps}"
AMAP_GEO_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
AMAP_REGEO_ENDPOINT = "https://restapi.amap.com/v3/geocode/regeo"

# 预报天数范围与默认值：彩云 v2.6 高于免费额度需走付费 token，
# 默认 7 保留原有行为；上限 15 是彩云接口公开的 dailysteps 最大值。
DEFAULT_DAILY_STEPS = 7
MAX_DAILY_STEPS = 15

PROVIDER_INFO: dict[str, Any] = {
    "weather": {"id": "caiyun", "name": "彩云天气", "api_version": "v2.6"},
    "geocode": {"id": "amap", "name": "高德地理编码"},
    "attribution": "彩云天气 API v2.6 · 高德地理编码",
}

UNITS_INFO: dict[str, str] = {
    "temperature": "℃",
    "apparent_temperature": "℃",
    "wind_speed": "m/s",
    "precipitation": "mm/h",
    "visibility": "km",
    "pressure": "hPa",
    "humidity": "%",
    "precip_probability": "%",
}


# ─────────────────────── 派生字段辅助函数 ───────────────────────

def visibility_level(km: Any) -> str | None:
    """能见度分级。用于 realtime.visibility_level 字段。

    级别枚举（从差到优）：极差 / 差 / 一般 / 良好 / 极佳；无法识别时返回 None。
    <1km 通常伴随雾/霾，下游据此把能见度作为风险项列入 risks。
    """
    if not isinstance(km, (int, float)):
        return None
    if km < 1:
        return "极差"
    if km < 4:
        return "差"
    if km < 10:
        return "一般"
    if km < 20:
        return "良好"
    return "极佳"


def is_daytime(now: datetime, sunrise: Any, sunset: Any) -> bool | None:
    """按日出日落时间判断当前是否是白天。

    sunrise / sunset 是彩云给的 "HH:MM" 本地时间字符串；无法解析时返回 None。
    LLM 可以据此校准「现在应该是 CLEAR_DAY 还是 CLEAR_NIGHT」的歧义。
    """
    if not isinstance(sunrise, str) or not isinstance(sunset, str):
        return None
    try:
        sr_h, sr_m = (int(x) for x in sunrise.split(":", 1))
        ss_h, ss_m = (int(x) for x in sunset.split(":", 1))
    except (ValueError, AttributeError):
        return None
    current_min = now.hour * 60 + now.minute
    sr_min = sr_h * 60 + sr_m
    ss_min = ss_h * 60 + ss_m
    return sr_min <= current_min < ss_min


def summarize_minutely(minutely: Any) -> dict[str, Any] | None:
    """把彩云 minutely（未来 2h 逐分钟降雨）压成一个给 LLM 决策用的摘要。

    彩云 result.minutely 典型字段：
      - `status`: "ok" 时数据可信
      - `description`: 中文描述，如"未来两小时内无雨"
      - `precipitation_2h`: 120 个 float（mm/h），每分钟一个
      - `precipitation`: 60 个 float（mm/h）
      - `probability`: 未来 2h 每 30 分钟一段的降雨概率
    输出摘要：
      - `available`: bool，false 时表示接口没返回逐分钟数据（**免费 token 永远是 false**），
        其他字段全部为 None；下游应改用 realtime.precipitation 与 hourly.description。
      - `description`: 原始中文描述
      - `has_rain_in_2h`: 未来 120 分钟是否有任一分钟 > 0.03mm/h
      - `starts_in_minutes`: 当前不下雨时，几分钟后开始下雨；已在下雨为 0；不下雨为 None
      - `stops_in_minutes`: 当前在下雨时，再持续多少分钟；未降雨为 None
      - `peak_intensity`: 未来 2h 内最大 mm/h
      - `peak_in_minutes`: 峰值出现在第几分钟
    使用约定：0.03 mm/h 是彩云官方定义的"下雨起点阈值"，和他们 forecast_keypoint 口径一致。
    重要：彩云 v2.6 综合接口对**免费 token**（result.primary == 0）会返回 `minutely: {}`——
    没有 description、没有 precipitation_2h、没有 probability。这种情况下 `available=false`，
    LLM 不应再回答「再过几分钟下雨」这类逐分钟问题，应明确告知数据不可用。
    """
    if not isinstance(minutely, dict):
        # 极少数情况下接口连 minutely 字段都没返回
        return {
            "available": False,
            "status": None,
            "description": None,
            "has_rain_in_2h": None,
            "starts_in_minutes": None,
            "stops_in_minutes": None,
            "peak_intensity": None,
            "peak_in_minutes": None,
        }
    precip_2h = minutely.get("precipitation_2h") or minutely.get("precipitation") or []
    if not isinstance(precip_2h, list) or not precip_2h:
        # 免费 token 走这一支：所有详细字段置 None，由 available=False 标记
        return {
            "available": False,
            "status": minutely.get("status"),
            "description": minutely.get("description"),
            "has_rain_in_2h": None,
            "starts_in_minutes": None,
            "stops_in_minutes": None,
            "peak_intensity": None,
            "peak_in_minutes": None,
        }
    threshold = 0.03
    peak = 0.0
    peak_idx = 0
    for i, v in enumerate(precip_2h):
        try:
            vv = float(v or 0)
        except (TypeError, ValueError):
            vv = 0.0
        if vv > peak:
            peak = vv
            peak_idx = i
    has_rain = peak > threshold
    starts_in: int | None = None
    stops_in: int | None = None
    # 走到这里 = 真有 120 分钟数据 → available=True
    available = True
    # 当前是否下雨（第 0 分钟）
    try:
        current = float(precip_2h[0] or 0)
    except (TypeError, ValueError):
        current = 0.0
    if current > threshold:
        # 现在在下雨，找第一个 <= 阈值 的分钟 → 再多少分钟停
        stops_in = None
        for i, v in enumerate(precip_2h):
            try:
                vv = float(v or 0)
            except (TypeError, ValueError):
                vv = 0.0
            if vv <= threshold:
                stops_in = i
                break
    else:
        # 现在不下雨，找第一个 > 阈值 的分钟 → 还有多少分钟开始下
        for i, v in enumerate(precip_2h):
            try:
                vv = float(v or 0)
            except (TypeError, ValueError):
                vv = 0.0
            if vv > threshold:
                starts_in = i
                break
    return {
        "available": available,
        "status": minutely.get("status"),
        "description": minutely.get("description"),
        "has_rain_in_2h": has_rain,
        "starts_in_minutes": starts_in,
        "stops_in_minutes": stops_in,
        "peak_intensity": round(peak, 3) if peak > 0 else 0.0,
        "peak_in_minutes": peak_idx if has_rain else None,
    }


def mask_token(token: str | None) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return f"{token[:2]}***{token[-2:]}"


_LOADED_ENV_FILES: list[str] = []  # 脚本不再加载 .env，保留占位以兼容旧引用


def loaded_files_cache() -> list[str]:
    return list(_LOADED_ENV_FILES)


def resolve_save_path(raw: str, now: datetime) -> Path:
    """把 --save 的参数解析成绝对路径；默认文件名精确到分钟，避免频繁触发时覆盖。"""
    if raw and raw != "__default__":
        return Path(raw).expanduser().resolve()
    base = default_output_dir()
    filename = f"weather_{now.strftime('%Y%m%d_%H%M')}.md"
    return (base / filename).resolve()


def _cache_seconds_from_args(args: argparse.Namespace) -> int:
    if args.no_cache:
        return 0
    if args.cache_seconds is not None:
        return max(0, args.cache_seconds)
    env_value = os.environ.get("WEATHER_CACHE_SECONDS")
    if env_value is None:
        return 0
    try:
        return max(0, int(env_value))
    except ValueError:
        return 0


def _cache_dir() -> Path:
    raw = os.environ.get("WEATHER_CACHE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "caiyun-weather"


def cache_lookup(lng: float, lat: float, ttl_seconds: int) -> dict[str, Any] | None:
    if ttl_seconds <= 0:
        return None
    path = _cache_dir() / f"caiyun_{lng:.6f}_{lat:.6f}.json"
    try:
        if not path.exists():
            return None
        import time
        if time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - cache 是尽力而为
        return None


def cache_store(lng: float, lat: float, data: dict[str, Any]) -> None:
    path = _cache_dir() / f"caiyun_{lng:.6f}_{lat:.6f}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 - cache 是尽力而为
        return


MOCK_PROFILES: dict[str, dict[str, Any]] = {
    "sunny": {
        "forecast_keypoint": "未来两小时天气晴朗",
        "hourly_description": "今天全天晴朗，气温舒适，适合出行",
        "alert": {"content": []},
        "minutely": {"status": "ok", "description": "未来两小时内无雨", "precip_peak": 0.0, "rain_starts_at": None},
        "astro": {"sunrise": "05:48", "sunset": "19:02"},
        "realtime": {
            "skycon": "CLEAR_DAY",
            "temperature": 27.3,
            "apparent_temperature": 28.6,
            "humidity": 0.52,
            "wind": {"speed": 3.1, "direction": 180.0},
            "precipitation": {
                "local": {"status": "ok", "datasource": "radar", "intensity": 0.0},
                "nearest": {"status": "ok", "distance": 25.0, "intensity": 0.0},
            },
            "visibility": 20.0,
            "pressure": 100800,
            "air_quality": {"aqi": {"chn": 42}, "pm25": 18},
            "life_index": {
                "ultraviolet": {"index": 5, "desc": "中等"},
                "comfort": {"index": 5, "desc": "舒适"},
                "coldRisk": {"index": 1, "desc": "少发"},
                "dressing": {"index": 3, "desc": "短袖短裤"},
                "carWashing": {"index": 1, "desc": "适宜"},
            },
        },
        "daily": {
            "temperature": (22.0, 31.0),
            "precip_probability": 0,
            "skycon": "CLEAR_DAY",
            "aqi": 45,
            "uv": 5,
        },
        "hourly": {"skycon": "CLEAR_DAY", "precip": 0.0, "probability": 0.0, "wind_speed": 3.0},
    },
    "rain": {
        "forecast_keypoint": "傍晚到夜间雨势更明显",
        "hourly_description": "多云，傍晚到太阳下山前后开始下雨，会持续超过两小时",
        "alert": {"content": []},
        "minutely": {"status": "ok", "description": "约 25 分钟后开始下雨，持续约 90 分钟", "precip_peak": 1.8, "rain_starts_at": 25, "rain_stops_at": None, "initial_rain": False},
        "astro": {"sunrise": "06:02", "sunset": "18:41"},
        "realtime": {
            "skycon": "LIGHT_RAIN",
            "temperature": 23.5,
            "apparent_temperature": 24.0,
            "humidity": 0.88,
            "wind": {"speed": 6.4, "direction": 95.0},
            "precipitation": {
                "local": {"status": "ok", "datasource": "radar", "intensity": 0.9},
                "nearest": {"status": "ok", "distance": 0.0, "intensity": 1.4},
            },
            "visibility": 7.5,
            "pressure": 100400,
            "air_quality": {"aqi": {"chn": 58}, "pm25": 28},
            "life_index": {
                "ultraviolet": {"index": 2, "desc": "弱"},
                "comfort": {"index": 7, "desc": "潮湿"},
                "coldRisk": {"index": 3, "desc": "较易发"},
                "dressing": {"index": 5, "desc": "长袖长裤"},
                "carWashing": {"index": 4, "desc": "不宜"},
            },
        },
        "daily": {
            "temperature": (21.0, 26.0),
            "precip_probability": 80,
            "skycon": "MODERATE_RAIN",
            "aqi": 60,
            "uv": 2,
        },
        "hourly": {"skycon": "CLOUDY", "precip": 0.0, "probability": 0.1, "wind_speed": 5.5},
        "events": [
            {"start": 4, "duration": 3, "skycon": "MODERATE_RAIN", "precip": 1.8, "probability": 0.72, "wind_speed": 6.0},
            {"start": 9, "duration": 2, "skycon": "LIGHT_RAIN", "precip": 0.6, "probability": 0.58, "wind_speed": 5.8},
        ],
    },
    "alert": {
        "forecast_keypoint": "暴雨橙色预警，未来 6 小时持续降雨",
        "hourly_description": "暴雨，今天夜间00点钟后雨势减小，转中雨，明早转阴",
        "alert": {
            "content": [{
                "code": "0503",
                "title": "杭州市气象局发布暴雨橙色预警",
                "description": "预计未来 6 小时内本市降雨量将达 50 毫米以上。",
            }]
        },
        "minutely": {"status": "ok", "description": "正在下中雨，下雨预计持续超过两小时", "precip_peak": 6.0, "rain_starts_at": 0, "rain_stops_at": None, "initial_rain": True},
        "astro": {"sunrise": "06:08", "sunset": "18:29"},
        "realtime": {
            "skycon": "STORM_RAIN",
            "temperature": 22.0,
            "apparent_temperature": 23.0,
            "humidity": 0.95,
            "wind": {"speed": 11.5, "direction": 45.0},
            "precipitation": {
                "local": {"status": "ok", "datasource": "radar", "intensity": 6.0},
                "nearest": {"status": "ok", "distance": 0.0, "intensity": 8.5},
            },
            "visibility": 2.0,
            "pressure": 100100,
            "air_quality": {"aqi": {"chn": 110}, "pm25": 85},
            "life_index": {
                "ultraviolet": {"index": 1, "desc": "弱"},
                "comfort": {"index": 9, "desc": "阴冷"},
                "coldRisk": {"index": 4, "desc": "易发"},
                "dressing": {"index": 6, "desc": "外套"},
                "carWashing": {"index": 4, "desc": "不宜"},
            },
        },
        "daily": {
            "temperature": (21.0, 25.0),
            "precip_probability": 95,
            "skycon": "STORM_RAIN",
            "aqi": 115,
            "uv": 1,
        },
        "hourly": {"skycon": "MODERATE_RAIN", "precip": 1.5, "probability": 0.75, "wind_speed": 9.0},
        "events": [
            {"start": 1, "duration": 6, "skycon": "STORM_RAIN", "precip": 6.0, "probability": 0.95, "wind_speed": 12.0},
            {"start": 7, "duration": 3, "skycon": "HEAVY_RAIN", "precip": 3.2, "probability": 0.88, "wind_speed": 10.5},
        ],
    },
}

def _mock_datetime(base: datetime, offset_hours: int) -> str:
    return (base + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def build_mock_weather_response(scenario: str, now: datetime, daily_steps: int = DEFAULT_DAILY_STEPS) -> dict[str, Any]:
    """按当前时间生成 mock 彩云响应，避免演示报告出现旧日期或重复关键时段。"""
    profile = MOCK_PROFILES.get(scenario) or MOCK_PROFILES["sunny"]
    profile = dict(profile)
    profile["scenario"] = scenario  # 供 daily 拆分逻辑识别场景
    base = now.replace(minute=0, second=0, microsecond=0)
    hourly_defaults = profile["hourly"]
    hourly_temperature: list[dict[str, Any]] = []
    hourly_skycon: list[dict[str, Any]] = []
    hourly_precipitation: list[dict[str, Any]] = []
    hourly_wind: list[dict[str, Any]] = []

    event_by_offset: dict[int, dict[str, Any]] = {}
    for event in profile.get("events", []):
        start = int(event.get("start", 0))
        duration = max(1, int(event.get("duration", 1)))
        for offset in range(start, start + duration):
            event_by_offset[offset] = event

    for offset in range(24):
        dt_text = _mock_datetime(base, offset)
        local_hour = (base + timedelta(hours=offset)).hour
        # 用轻微日变化模拟温度曲线，保证四个关键时段不会全部是同一个值。
        temp_delta = -2 if local_hour < 7 else (2 if 12 <= local_hour <= 16 else 0)
        event = event_by_offset.get(offset)
        skycon = (event or hourly_defaults).get("skycon")
        precip = float((event or hourly_defaults).get("precip", 0.0))
        probability = float((event or hourly_defaults).get("probability", 0.0))
        wind_speed = float((event or hourly_defaults).get("wind_speed", 3.0))
        hourly_temperature.append({"datetime": dt_text, "value": round(profile["realtime"]["temperature"] + temp_delta, 1)})
        hourly_skycon.append({"datetime": dt_text, "value": skycon})
        hourly_precipitation.append({"datetime": dt_text, "value": precip, "probability": probability})
        hourly_wind.append({"datetime": dt_text, "speed": wind_speed, "direction": profile["realtime"]["wind"]["direction"]})

    daily_profile = profile["daily"]
    temp_min, temp_max = daily_profile["temperature"]
    daily_temperature = []
    daily_precipitation = []
    daily_precip_day_arr: list[dict[str, Any]] = []
    daily_precip_night_arr: list[dict[str, Any]] = []
    daily_skycon = []
    daily_skycon_day_arr: list[dict[str, Any]] = []
    daily_skycon_night_arr: list[dict[str, Any]] = []
    daily_aqi = []
    daily_uv = []
    daily_astro = []
    astro_profile = profile.get("astro") or {"sunrise": "06:00", "sunset": "18:30"}
    steps = max(1, min(int(daily_steps), MAX_DAILY_STEPS))
    # 让 mock 也能展示「白天 vs 夜里」雨强差异：按场景选不同接近实际的拆分。
    scenario = profile.get("scenario") or ""
    for offset in range(steps):
        daily_temperature.append({"min": temp_min + min(offset, 3) * 0.5, "max": temp_max + min(offset, 3) * 0.4})
        full_prob = max(0, daily_profile["precip_probability"] - offset * 8)
        full_skycon = daily_profile["skycon"] if offset < 3 else "PARTLY_CLOUDY_DAY"
        # daily.precipitation 顶层同时召 max/avg，较贴近彩云真实返回。
        full_max = round(max(0.0, full_prob / 100.0) * 1.6, 2)
        full_avg = round(full_max * 0.4, 2)
        daily_precipitation.append({
            "date": (now + timedelta(days=offset)).isoformat(timespec="seconds"),
            "max": full_max,
            "min": 0.0,
            "avg": full_avg,
            "probability": full_prob,
        })
        # 白天/夜间拆分：rain 场景下夜里雨势更大；alert 场景下两者都大；sunny 场景都为 0。
        if scenario == "rain":
            day_skycon = "CLOUDY"
            night_skycon = full_skycon
            day_max = round(full_max * 0.4, 2)
            night_max = round(full_max * 1.0, 2)
            day_prob = max(0, full_prob - 25)
            night_prob = full_prob
        elif scenario == "alert":
            day_skycon = full_skycon
            night_skycon = full_skycon
            day_max = full_max
            night_max = round(full_max * 0.6, 2)
            day_prob = full_prob
            night_prob = max(0, full_prob - 10)
        else:  # sunny / 其他
            day_skycon = full_skycon
            night_skycon = "CLEAR_NIGHT" if "CLEAR" in full_skycon else full_skycon
            day_max = full_max
            night_max = 0.0
            day_prob = full_prob
            night_prob = 0
        daily_skycon.append({"value": full_skycon})
        daily_skycon_day_arr.append({"value": day_skycon})
        daily_skycon_night_arr.append({"value": night_skycon})
        daily_precip_day_arr.append({"max": day_max, "min": 0.0, "avg": round(day_max * 0.4, 2), "probability": day_prob})
        daily_precip_night_arr.append({"max": night_max, "min": 0.0, "avg": round(night_max * 0.4, 2), "probability": night_prob})
        daily_aqi.append({"avg": {"chn": daily_profile["aqi"] + offset}})
        daily_uv.append({"index": daily_profile["uv"]})
        # astro 日历不随天变化太多，随机微调几分钟让数据看起来不是复制粘贴
        daily_astro.append({
            "date": (now + timedelta(days=offset)).strftime("%Y-%m-%d"),
            "sunrise": {"time": astro_profile["sunrise"]},
            "sunset": {"time": astro_profile["sunset"]},
        })

    # 按场景拼 minutely 原始结构，让下游 summarize_minutely 能正常分析。
    # precipitation_2h 长度 120，满足其彩云可观察到的分布形态。
    mp = profile.get("minutely") or {}
    starts = mp.get("rain_starts_at")
    stops = mp.get("rain_stops_at")
    peak = float(mp.get("precip_peak") or 0.0)
    initial_rain = bool(mp.get("initial_rain", False))
    minutely_values = []
    for minute in range(120):
        if initial_rain:
            # 正在下雨：前 60 分钟雨强慢降，之后降到需要的 stops 时点停止
            if isinstance(stops, int) and minute >= stops:
                minutely_values.append(0.0)
            else:
                # 线性慢降到 peak*0.3，保持连续雨感
                minutely_values.append(round(max(peak * (1 - minute / 180.0), peak * 0.3), 3))
        elif isinstance(starts, int):
            if minute < starts:
                minutely_values.append(0.0)
            else:
                # 从 starts 开始爬升到 peak，再慢降回零
                ramp = min(1.0, (minute - starts) / 10.0)
                tail = max(0.0, 1.0 - (minute - starts - 30) / 60.0)
                minutely_values.append(round(peak * ramp * min(1.0, tail), 3))
        else:
            minutely_values.append(0.0)
    minutely_block = {
        "status": mp.get("status", "ok"),
        "description": mp.get("description") or profile["forecast_keypoint"],
        "precipitation_2h": minutely_values,
        "precipitation": minutely_values[:60],
    }

    # alert.content 里补 pubDate：mock 场景下统一为当前整点时间。
    alert = dict(profile["alert"])
    if alert.get("content"):
        alert["content"] = [
            {**item, "pubDate": base.isoformat(timespec="seconds")}
            for item in alert["content"]
        ]

    return {
        "status": "ok",
        "result": {
            "forecast_keypoint": profile["forecast_keypoint"],
            "alert": alert,
            "minutely": minutely_block,
            "realtime": profile["realtime"],
            "hourly": {
                "description": profile.get("hourly_description") or profile.get("forecast_keypoint"),
                "temperature": hourly_temperature,
                "skycon": hourly_skycon,
                "precipitation": hourly_precipitation,
                "wind": hourly_wind,
            },
            "daily": {
                "temperature": daily_temperature,
                "precipitation": daily_precipitation,
                "precipitation_08h_20h": daily_precip_day_arr,
                "precipitation_20h_32h": daily_precip_night_arr,
                "skycon": daily_skycon,
                "skycon_08h_20h": daily_skycon_day_arr,
                "skycon_20h_32h": daily_skycon_night_arr,
                "air_quality": {"aqi": daily_aqi},
                "life_index": {"ultraviolet": daily_uv},
                "astro": daily_astro,
            },
        },
    }


def geocode_address(address: str, city: str | None, key: str) -> dict[str, Any]:
    data = request_json(AMAP_GEO_ENDPOINT, {
        "key": key,
        "address": address,
        "city": city,
        "output": "JSON",
    }, service="高德地理编码")
    normalize_amap(data)
    geocodes = data.get("geocodes", [])
    if not geocodes:
        emit({"error": "未找到地址对应的经纬度", "address": address, "city": city}, exit_code=1)

    item = geocodes[0]
    location = item.get("location")
    if not isinstance(location, str) or "," not in location:
        emit({"error": "高德返回缺少 location", "address": address}, exit_code=1)
    lng_raw, lat_raw = location.split(",", 1)
    lng, lat = parse_lng_lat(lng_raw, lat_raw)
    level = item.get("level")
    return {
        "source": "amap_geo",
        "query_address": address,
        "query_city": city,
        "formatted_address": item.get("formatted_address"),
        "province": pick_first_string(item.get("province")),
        "city": pick_first_string(item.get("city")),
        "district": pick_first_string(item.get("district")),
        "adcode": item.get("adcode"),
        "level": level,
        "quality": amap_quality(level),
        "lng": round(lng, 6),
        "lat": round(lat, 6),
        "location": location,
    }


def reverse_geocode(lng: float, lat: float, key: str) -> dict[str, Any]:
    location = f"{lng:.6f},{lat:.6f}"
    data = request_json(AMAP_REGEO_ENDPOINT, {
        "key": key,
        "location": location,
        "radius": 1000,
        "extensions": "base",
        "output": "JSON",
    }, service="高德逆地理编码")
    normalize_amap(data)
    regeocode = data.get("regeocode", {})
    component = regeocode.get("addressComponent", {}) if isinstance(regeocode, dict) else {}
    street_number = component.get("streetNumber", {}) if isinstance(component, dict) else {}
    quality = "high" if isinstance(street_number, dict) and street_number.get("number") else "medium"
    return {
        "source": "amap_regeo",
        "formatted_address": regeocode.get("formatted_address"),
        "province": pick_first_string(component.get("province")),
        "city": pick_first_string(component.get("city")),
        "district": pick_first_string(component.get("district")),
        "township": pick_first_string(component.get("township")),
        "adcode": component.get("adcode"),
        "street": street_number.get("street") if isinstance(street_number, dict) else None,
        "number": street_number.get("number") if isinstance(street_number, dict) else None,
        "quality": quality,
        "lng": round(lng, 6),
        "lat": round(lat, 6),
        "location": location,
    }


def infer_location_name(location: str | None, geocode: dict[str, Any] | None, address: str | None) -> str:
    if location:
        return location
    if geocode:
        for key in ("formatted_address", "query_address", "township", "district", "city"):
            value = geocode.get(key)
            if isinstance(value, str) and value:
                return value
    if address:
        return address
    return "当前位置"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="彩云天气数据 JSON 提取工具")
    parser.add_argument("--address", help="结构化地址，如 杭州市西湖风景名胜区。未提供经纬度时会用高德地理编码。")
    parser.add_argument("--city", help="地理编码限定城市，缺省读 WEATHER_CITY 或 WEATHER_DEFAULT_CITY/DEFAULT_CITY。")
    parser.add_argument("--lng", type=float, help="经度。")
    parser.add_argument("--lat", type=float, help="纬度。")
    parser.add_argument("--location", help="位置名称，用于输出标题。")
    parser.add_argument("--tz", type=int, help="时区偏移小时，默认读 WEATHER_TZ 或 8。")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"彩云日级预报天数（1–{MAX_DAILY_STEPS}）。默认读 WEATHER_DAILY_STEPS，都未设置时用 {DEFAULT_DAILY_STEPS}。",
    )
    parser.add_argument("--check", action="store_true", help="诊断模式：仅验证彩云 Token 和高德 Key 是否可用。")
    parser.add_argument(
        "--save",
        nargs="?",
        const="__default__",
        default=None,
        help="把 stdin 的内容（通常是 LLM 渲染好的播报 Markdown）落盘。不传该参数时不写盘；传 --save 走默认路径；传 --save /path/to.md 使用指定路径。",
    )
    parser.add_argument(
        "--format",
        choices=["json", "brief", "short"],
        default="json",
        help="输出形式：json（完整数据，默认）；brief（结构化决策数据，供 LLM 生成完整播报）；short（3–6 行中文直接回答）。",
    )
    parser.add_argument(
        "--mock",
        choices=["sunny", "rain", "alert"],
        help="使用内置示例数据，不调用真实 API。便于离线演示/CI。",
    )
    parser.add_argument(
        "--cache-seconds",
        type=int,
        default=None,
        help="彩云响应缓存秒数。未指定则读 WEATHER_CACHE_SECONDS；其值缺省为 0（不缓存）。",
    )
    parser.add_argument("--no-cache", action="store_true", help="跳过缓存读取与写入。")
    return parser


def resolve_inputs(args: argparse.Namespace) -> dict[str, Any]:
    token = os.environ.get("CAIYUN_TOKEN")
    if not token:
        emit({
            "error": "缺少必填环境变量: CAIYUN_TOKEN",
            "hint": env_hint() + " 免费申请彩云 token：https://docs.caiyunapp.com。",
        }, exit_code=1)

    address = args.address or os.environ.get("WEATHER_ADDRESS")
    city = args.city or os.environ.get("WEATHER_CITY") or default_city()
    location = args.location or os.environ.get("WEATHER_LOCATION")
    lng_raw = args.lng if args.lng is not None else (
        os.environ.get("WEATHER_LNG") or os.environ.get("LONGITUDE")
    )
    lat_raw = args.lat if args.lat is not None else (
        os.environ.get("WEATHER_LAT") or os.environ.get("LATITUDE")
    )
    tz_raw = args.tz if args.tz is not None else os.environ.get("WEATHER_TZ", "8")

    try:
        tz_offset = int(tz_raw)
    except (TypeError, ValueError):
        emit({"error": "WEATHER_TZ/--tz 必须是整数小时偏移", "tz": tz_raw}, exit_code=1)
    if not (-12 <= tz_offset <= 14):
        emit({"error": "WEATHER_TZ/--tz 超出常见时区范围", "tz": tz_offset}, exit_code=1)

    # --days > WEATHER_DAILY_STEPS > DEFAULT_DAILY_STEPS，采集前夹到合法范围
    daily_raw = args.days if args.days is not None else os.environ.get("WEATHER_DAILY_STEPS")
    try:
        daily_steps = int(daily_raw) if daily_raw not in (None, "") else DEFAULT_DAILY_STEPS
    except (TypeError, ValueError):
        emit({"error": "WEATHER_DAILY_STEPS/--days 必须是整数", "value": daily_raw}, exit_code=1)
    if not (1 <= daily_steps <= MAX_DAILY_STEPS):
        emit({"error": f"预报天数需在 1–{MAX_DAILY_STEPS} 范围内", "value": daily_steps}, exit_code=1)

    geocode_meta = None
    if lng_raw is not None and lat_raw is not None:
        lng, lat = parse_lng_lat(lng_raw, lat_raw)
        key = amap_key()
        if key:
            geocode_meta = reverse_geocode(lng, lat, key)
    elif address:
        key = amap_key()
        if not key:
            emit({
                "error": "使用地址查询天气需要高德 Web 服务 Key",
                "hint": env_hint() + " 请注入 AMAP_KEY 或 GAODE_KEY，或改用 --lng/--lat。",
            }, exit_code=1)
        geocode_meta = geocode_address(address, city, key)
        lng = geocode_meta["lng"]
        lat = geocode_meta["lat"]
    else:
        emit({
            "error": "缺少定位信息",
            "hint": "请提供 --address，或 --lng/--lat，或注入 WEATHER_ADDRESS、WEATHER_LNG/WEATHER_LAT（也支持 LONGITUDE/LATITUDE）。" + env_hint(),
        }, exit_code=1)

    return {
        "token": token,
        "lng": lng,
        "lat": lat,
        "address": address,
        "city": city,
        "location": infer_location_name(location, geocode_meta, address),
        "tz": timezone(timedelta(hours=tz_offset)),
        "tz_offset": tz_offset,
        "daily_steps": daily_steps,
        "geocode": geocode_meta,
    }


def run_check() -> None:
    token = os.environ.get("CAIYUN_TOKEN")
    key = amap_key()
    report: dict[str, Any] = {
        "mode": "check",
        "env": {
            "CAIYUN_TOKEN": mask_token(token) if token else None,
            "AMAP_KEY": mask_token(key) if key else None,
            "WEATHER_DEFAULT_CITY": default_city(),
            "WEATHER_OUTPUT_DIR": str(default_output_dir()),
        },
        "caiyun": {"configured": bool(token), "ok": None, "detail": None},
        "amap": {"configured": bool(key), "ok": None, "detail": None},
    }

    if token:
        test_url = CAIYUN_ENDPOINT_TEMPLATE.format(token=token, lng=116.4075, lat=39.9040, dailysteps=DEFAULT_DAILY_STEPS)
        try:
            data = request_json(test_url, service="彩云天气自检", raise_errors=True)
            status = data.get("status")
            report["caiyun"]["ok"] = status == "ok"
            report["caiyun"]["detail"] = status if status != "ok" else "reachable"
        except RequestError as exc:
            report["caiyun"]["ok"] = False
            report["caiyun"]["detail"] = str(exc)

    if key:
        try:
            data = request_json(
                AMAP_GEO_ENDPOINT,
                {"key": key, "address": "北京市", "output": "JSON"},
                service="高德自检",
                raise_errors=True,
            )
            status = str(data.get("status"))
            report["amap"]["ok"] = status == "1"
            report["amap"]["detail"] = data.get("info") if status != "1" else "reachable"
        except RequestError as exc:
            report["amap"]["ok"] = False
            report["amap"]["detail"] = str(exc)

    emit(report)


def run_mock(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (彩云原始响应, resolved 元数据)。"""
    scenario = args.mock or "sunny"
    tz = timezone(timedelta(hours=args.tz if args.tz is not None else 8))
    # mock 下也尊重 --days 参数，其他路径需要校验时可在这里一起收紧。
    daily_steps = args.days if args.days is not None else DEFAULT_DAILY_STEPS
    if not (1 <= int(daily_steps) <= MAX_DAILY_STEPS):
        emit({"error": f"预报天数需在 1–{MAX_DAILY_STEPS} 范围内", "value": daily_steps}, exit_code=1)
    data = build_mock_weather_response(scenario, datetime.now(tz), daily_steps=int(daily_steps))
    resolved = {
        "token": "mock-token",
        "lng": 120.155100,
        "lat": 30.274100,
        "address": args.address,
        "city": args.city or default_city(),
        "location": args.location or "示例地点",
        "tz": tz,
        "tz_offset": tz.utcoffset(None).total_seconds() / 3600,
        "daily_steps": int(daily_steps),
        "geocode": {
            "source": "mock",
            "formatted_address": "示例数据（mock）",
            "quality": "high",
        },
    }
    return data, resolved


def main() -> None:
    loaded = load_env_file()
    _LOADED_ENV_FILES[:] = loaded
    args = build_parser().parse_args()

    if args.check:
        run_check()
        return

    if args.mock:
        data, resolved = run_mock(args)
        tz: timezone = resolved["tz"]
        now = datetime.now(tz)
        lng = resolved["lng"]
        lat = resolved["lat"]
        cache_used = False
    else:
        resolved = resolve_inputs(args)
        tz = resolved["tz"]
        now = datetime.now(tz)
        lng = resolved["lng"]
        lat = resolved["lat"]

        ttl = _cache_seconds_from_args(args)
        cached = cache_lookup(lng, lat, ttl)
        if cached is not None:
            data = cached
            cache_used = True
        else:
            url = CAIYUN_ENDPOINT_TEMPLATE.format(
                token=resolved["token"],
                lng=lng,
                lat=lat,
                dailysteps=resolved.get("daily_steps", DEFAULT_DAILY_STEPS),
            )
            data = request_json(url, service="彩云天气")
            if data.get("status") != "ok":
                emit({"error": f"彩云 API status: {data.get('status')}", "api_error": data.get("error")}, exit_code=1)
            cache_used = False
            if ttl > 0:
                cache_store(lng, lat, data)

    res = data.get("result", {})
    rt = res.get("realtime", {})
    hr = res.get("hourly", {})
    dl = res.get("daily", {})

    # ═══ alerts ═══
    alerts = []
    for alert in res.get("alert", {}).get("content", []):
        code = safe(alert, "code")
        level = safe(alert, "level")
        if not level and isinstance(code, str) and len(code) >= 2:
            level = code[-2:]
        alerts.append({
            "level": level,
            "code": code,
            "title": safe(alert, "title"),
            "desc": safe(alert, "description"),
            "pub_date": (safe(alert, "pubDate") or "")[:16].replace("T", " "),
        })

    # ═══ realtime ═══
    humidity = num(safe(rt, "humidity"))
    pressure = num(safe(rt, "pressure"))
    visibility = num(safe(rt, "visibility"), 1)
    life_raw = rt.get("life_index", {}) if isinstance(rt.get("life_index"), dict) else {}

    # 生活指数全量输出：LLM 可直接回答「今天适合洗车吗/会感冒吗/穿什么」。
    # 彩云常见子项：ultraviolet / comfort / coldRisk / dressing / carWashing 等
    life_index: dict[str, Any] = {}
    for key in ("ultraviolet", "comfort", "coldRisk", "dressing", "carWashing"):
        entry = life_raw.get(key)
        if not isinstance(entry, dict):
            continue
        # ultraviolet 有 index 值；其他指数只有 index 等级文本和 desc
        item = {"desc": entry.get("desc")}
        if "index" in entry:
            item["index"] = entry.get("index")
        life_index[key] = item

    # 实时降水：彩云在 realtime.precipitation 下给两个对象——
    #   local   = 脚下网格（约 1×1km）
    #   nearest = 最近的雨带：distance 单位 km，intensity 单位 mm/h
    # 免费 token 拿不到 minutely，但 local/nearest 都有 → 是判断「现在/附近在下雨吗」的主信号。
    nearest_raw = safe(rt, "precipitation", "nearest")
    if isinstance(nearest_raw, dict):
        nearest_block = {
            "status": nearest_raw.get("status"),
            "distance": num(nearest_raw.get("distance"), 1),
            "intensity": num(nearest_raw.get("intensity"), 3),
        }
    else:
        nearest_block = None
    local_raw = safe(rt, "precipitation", "local")
    local_status = local_raw.get("status") if isinstance(local_raw, dict) else None
    local_datasource = local_raw.get("datasource") if isinstance(local_raw, dict) else None

    realtime = {
        "skycon": safe(rt, "skycon"),
        "temp": num(safe(rt, "temperature"), 1),
        "app_temp": num(safe(rt, "apparent_temperature"), 1),
        "humidity": humidity,
        "humidity_pct": int(round(humidity * 100)) if isinstance(humidity, (int, float)) else None,
        "wind_dir": num(safe(rt, "wind", "direction")),
        "wind_spd": num(safe(rt, "wind", "speed"), 1),
        "precip": num(safe(rt, "precipitation", "local", "intensity"), 2),
        "precip_local_status": local_status,
        "precip_local_datasource": local_datasource,
        "precip_nearest": nearest_block,
        "visibility": visibility,
        "visibility_level": visibility_level(visibility),
        "pressure": pressure,
        "pressure_hpa": round(pressure / 100, 1) if isinstance(pressure, (int, float)) else None,
        "aqi": num(safe(rt, "air_quality", "aqi", "chn")),
        "pm25": num(safe(rt, "air_quality", "pm25")),
        # 保留旧字段兼容下游，LLM 优先用 life_index 可用集合
        "uv": safe(rt, "life_index", "ultraviolet", "index"),
        "uv_desc": safe(rt, "life_index", "ultraviolet", "desc"),
        "comfort": safe(rt, "life_index", "comfort", "desc"),
        "life_index": life_index,
    }

    # ═══ today ═══
    dtl = dl.get("temperature", [])
    dpcp = dl.get("precipitation", [])

    def daily_precip_probability(index: int) -> int | None:
        if index >= len(dpcp):
            return None
        probability = dpcp[index].get("probability")
        if probability is None and isinstance(dpcp[index].get("max"), dict):
            probability = dpcp[index]["max"].get("probability")
        return prob_percent(probability)

    def daily_precip_probability_from(arr: list[Any], index: int) -> int | None:
        """通用版本：从任意 daily.precipitation_* 数组里取第 index 天的概率。"""
        if not isinstance(arr, list) or index >= len(arr):
            return None
        item = arr[index]
        if not isinstance(item, dict):
            return None
        probability = item.get("probability")
        if probability is None and isinstance(item.get("max"), dict):
            probability = item["max"].get("probability")
        return prob_percent(probability)

    # ── 今日 daily 字段：除了既有 t_min/t_max/skycon/aqi/precip_prob，再加：
    # 1) 白天/夜间分时段 skycon（彩云 daily.skycon_08h_20h / skycon_20h_32h）：
    #    部分日子整天 skycon 是 LIGHT_RAIN，但白天可能只是 CLOUDY、夜里才下雨；
    #    给 LLM 这两个细分能力，回答「今晚下雨吗」「白天还会下吗」更准确。
    # 2) 今日累计雨强 max/avg/min：彩云 daily.precipitation[0] 同时给出概率、最大
    #    雨强、平均雨强；之前只取 probability 浪费了「今天会下多大雨」的关键数据。
    dl_skycon_day = dl.get("skycon_08h_20h", []) or []
    dl_skycon_night = dl.get("skycon_20h_32h", []) or []
    dl_precip_day = dl.get("precipitation_08h_20h", []) or []
    dl_precip_night = dl.get("precipitation_20h_32h", []) or []
    today_precip = dpcp[0] if dpcp else {}
    today = {
        "t_min": num(safe(dtl, 0, "min")),
        "t_max": num(safe(dtl, 0, "max")),
        "skycon": safe(dl.get("skycon", []), 0, "value"),
        "skycon_day": safe(dl_skycon_day, 0, "value"),
        "skycon_night": safe(dl_skycon_night, 0, "value"),
        "aqi": num(safe(dl.get("air_quality", {}).get("aqi", []), 0, "avg", "chn")),
        "precip_prob": daily_precip_probability(0),
        "precip_max": num(today_precip.get("max"), 2) if isinstance(today_precip, dict) else None,
        "precip_avg": num(today_precip.get("avg"), 2) if isinstance(today_precip, dict) else None,
        "precip_day_prob": daily_precip_probability_from(dl_precip_day, 0),
        "precip_day_max": num(safe(dl_precip_day, 0, "max"), 2),
        "precip_day_avg": num(safe(dl_precip_day, 0, "avg"), 2),
        "precip_night_prob": daily_precip_probability_from(dl_precip_night, 0),
        "precip_night_max": num(safe(dl_precip_night, 0, "max"), 2),
        "precip_night_avg": num(safe(dl_precip_night, 0, "avg"), 2),
    }

    # ═══ hourly ═══
    hr_temps = hr.get("temperature", [])
    hr_skycons = hr.get("skycon", [])
    hr_precips = hr.get("precipitation", [])
    hr_winds = hr.get("wind", [])

    hour_points = []
    for i, item in enumerate(hr_temps):
        dt = parse_api_time(item.get("datetime"), tz)
        if dt is None:
            try:
                hour = int(item["datetime"][11:13])
            except Exception:  # noqa: BLE001 - 跳过坏数据点
                continue
            dt_text = None
            offset = None
        else:
            hour = dt.hour
            dt_text = dt.strftime("%Y-%m-%d %H:00")
            offset = (dt.date() - now.date()).days
        hour_points.append({"idx": i, "hour": hour, "datetime": dt_text, "day_offset": offset})

    def find_hour(target: int) -> int | None:
        best: dict[str, Any] | None = None
        best_distance = 999
        for pos, point in enumerate(hour_points):
            distance = abs(point["hour"] - target)
            if distance < best_distance:
                best_distance = distance
                best = {"pos": pos, **point}
        return best["pos"] if best else None

    def slot(pos: int | None) -> dict[str, Any] | None:
        if pos is None or pos >= len(hour_points):
            return None
        point = hour_points[pos]
        idx = point["idx"]
        probability = safe(hr_precips, idx, "probability") if idx < len(hr_precips) else None
        prob_frac = prob_fraction(probability)
        return {
            "hour": point["hour"],
            "datetime": point["datetime"],
            "day_offset": point["day_offset"],
            "skycon": safe(hr_skycons, idx, "value") if idx < len(hr_skycons) else None,
            "temp": num(safe(hr_temps, idx, "value"), 1) if idx < len(hr_temps) else None,
            "wind_spd": num(safe(hr_winds, idx, "speed"), 1) if idx < len(hr_winds) else None,
            "wind_dir": num(safe(hr_winds, idx, "direction")) if idx < len(hr_winds) else None,
            "precip": num(safe(hr_precips, idx, "value"), 2) if idx < len(hr_precips) else 0,
            "precip_prob": prob_frac,
            "precip_prob_pct": int(round(prob_frac * 100)) if isinstance(prob_frac, (int, float)) else None,
        }

    slot_positions = {
        "morning_rush": find_hour(8),
        "noon": find_hour(13),
        "evening_rush": find_hour(18),
        "night": find_hour(23),
    }
    hourly_key: dict[str, Any] = {name: slot(pos) for name, pos in slot_positions.items()}

    # 异常时段：降雨 >0 或风力 >10m/s
    abnormal = []
    key_source_idxs = {hour_points[pos]["idx"] for pos in slot_positions.values() if pos is not None and pos < len(hour_points)}
    for point in hour_points:
        idx = point["idx"]
        if idx in key_source_idxs:
            continue
        pv = safe(hr_precips, idx, "value") if idx < len(hr_precips) else None
        wv = safe(hr_winds, idx, "speed") if idx < len(hr_winds) else None
        try:
            precip = float(pv or 0)
        except (TypeError, ValueError):
            precip = 0.0
        try:
            wind_spd = float(wv or 0)
        except (TypeError, ValueError):
            wind_spd = 0.0

        if precip > 0:
            abnormal.append({
                "type": "rain",
                "idx": idx,
                "hour": point["hour"],
                "datetime": point["datetime"],
                "day_offset": point["day_offset"],
                "precip": round(precip, 2),
            })
        elif wind_spd > 10:
            abnormal.append({
                "type": "wind",
                "idx": idx,
                "hour": point["hour"],
                "datetime": point["datetime"],
                "day_offset": point["day_offset"],
                "wind_spd": round(wind_spd, 1),
            })

    merged = []
    i = 0
    while i < len(abnormal):
        item = abnormal[i]
        if item["type"] != "rain":
            item.pop("idx", None)
            merged.append(item)
            i += 1
            continue

        j = i + 1
        peak = item["precip"]
        end_item = item
        while j < len(abnormal) and abnormal[j]["type"] == "rain" and abnormal[j]["idx"] == abnormal[j - 1]["idx"] + 1:
            peak = max(peak, abnormal[j]["precip"])
            end_item = abnormal[j]
            j += 1

        merged_item = dict(item)
        merged_item["precip"] = peak
        merged_item["duration_hours"] = j - i
        if end_item is not item:
            merged_item["end_hour"] = end_item["hour"]
            merged_item["end_datetime"] = end_item["datetime"]
            merged_item["end_day_offset"] = end_item["day_offset"]
        merged_item.pop("idx", None)
        merged.append(merged_item)
        i = j

    hourly_key["abnormal"] = merged[:2]

    rain_points = []
    for point in hour_points:
        idx = point["idx"]
        try:
            rainy = idx < len(hr_precips) and float(hr_precips[idx].get("value", 0) or 0) > 0
        except (TypeError, ValueError):
            rainy = False
        if rainy:
            rain_points.append(point)

    hourly_key["day_rain"] = any(6 <= point["hour"] < 24 for point in rain_points)
    hourly_key["night_rain"] = any(point["hour"] < 6 for point in rain_points)
    hourly_key["today_day_rain"] = any(point["day_offset"] == 0 and 6 <= point["hour"] < 24 for point in rain_points)
    hourly_key["next_early_morning_rain"] = any(point["day_offset"] == 1 and point["hour"] < 6 for point in rain_points)

    # ═══ 7-day outlook ═══
    # 按 resolved.daily_steps 导出 daily；所有天都取 AQI/UV，同时袠 astro 的日出日落。
    # 彩云 daily 的 astro 数组和 temperature/skycon 等常规字段一样，是一个 N 元素列表；
    # astro[i] = {"date": "...", "sunrise": {"time": "HH:MM"}, "sunset": {"time": "HH:MM"}}。
    # `num(safe(...))` 对越界/缺失返回 None，不存在时字段即为空，渲染层用 "—" 兜底。
    days_count = int(resolved.get("daily_steps") or DEFAULT_DAILY_STEPS)
    days = []
    astro_list = dl.get("astro", []) or []
    for i in range(min(len(dtl), days_count)):
        dobj = now + timedelta(days=i)
        astro_item = astro_list[i] if i < len(astro_list) and isinstance(astro_list[i], dict) else {}
        days.append({
            "date": dobj.strftime("%m-%d"),
            "offset": i,
            "skycon": safe(dl.get("skycon", []), i, "value"),
            "t_min": num(safe(dtl, i, "min")),
            "t_max": num(safe(dtl, i, "max")),
            "precip_prob": daily_precip_probability(i),
            "aqi": num(safe(dl.get("air_quality", {}).get("aqi", []), i, "avg", "chn")),
            "uv": num(safe(dl.get("life_index", {}).get("ultraviolet", []), i, "index")),
            "sunrise": safe(astro_item, "sunrise", "time"),
            "sunset": safe(astro_item, "sunset", "time"),
        })

    # 日出日落（今日）提到 realtime，方便 LLM 直接用而不必去 days[0] 里找
    realtime["sunrise_today"] = days[0]["sunrise"] if days else None
    realtime["sunset_today"] = days[0]["sunset"] if days else None
    realtime["is_daytime"] = is_daytime(now, realtime["sunrise_today"], realtime["sunset_today"])

    # ═══ minutely (逐分钟降雨，彩云的招牌能力) ═══
    # 彩云 v2.6 综合接口中 minutely 在 result.minutely，字段包含：
    #   precipitation_2h（未来 120 分钟逐分钟降雨值 mm/h）
    #   precipitation  （未来 60 分钟逐分钟降雨值 mm/h）
    #   description    （中文描述，和 forecast_keypoint 互为备份）
    # 下游只关心「几分钟后开始下雨 / 雨会下多久」，这里计算好摘要指标。
    minutely = summarize_minutely(res.get("minutely"))

    out = {
        "location": resolved["location"],
        "lng": round(lng, 6),
        "lat": round(lat, 6),
        "geocode": resolved["geocode"],
        "provider": PROVIDER_INFO,
        "units": UNITS_INFO,
        "timezone": {"offset_hours": resolved.get("tz_offset"), "name": f"UTC{resolved.get('tz_offset'):+g}"},
        "output_dir": str(default_output_dir()),
        "cache_used": cache_used,
        "mock": args.mock,
        "date": now.strftime("%Y-%m-%d"),
        "date_cn": f"{now.month}月{now.day}日",
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "generated": now.strftime("%Y-%m-%d %H:%M"),
        "keypoint": safe(res, "forecast_keypoint"),
        # hourly.description 比 forecast_keypoint 跨度更长、更稳定，是免费 token 也能拿到的
        # 高密度自然语言摘要（例如「小雨，今天下午16点钟后雨停，转阴，其后小雨」）。
        # LLM 在 minutely 不可用时应优先引用这个字段。
        "keypoint_hourly": safe(hr, "description"),
        "alerts": alerts,
        "realtime": realtime,
        "today": today,
        "minutely": minutely,
        "hourly": hourly_key,
        "days": days,
    }

    # --save 现在的语义：从 stdin 读取 LLM 渲染好的播报 Markdown 落盘；
    # 脚本自身不再负责生成播报，避免「脚本渲染 vs LLM 渲染」两处真相。
    save_info: dict[str, Any] | None = None
    if args.save is not None:
        target = resolve_save_path(args.save, now)
        if sys.stdin.isatty():
            save_info = {
                "path": str(target),
                "written": False,
                "error": "--save 需要从 stdin 读取播报 Markdown；请用管道传入，例如：echo \"$report\" | weather_data.py --save",
            }
        else:
            report_text = sys.stdin.read()
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(report_text, encoding="utf-8")
                save_info = {"path": str(target), "written": True, "bytes": len(report_text.encode("utf-8"))}
            except OSError as exc:
                save_info = {"path": str(target), "written": False, "error": str(exc)}

    if save_info is not None:
        out["save"] = save_info

    if args.format == "brief":
        emit(formatters.to_brief(out))
    elif args.format == "short":
        sys.stdout.write(formatters.to_short(out) + "\n")
    else:
        emit(out)

    record_log({
        "action": "weather",
        "format": args.format,
        "location": resolved.get("location"),
        "lng": round(lng, 6),
        "lat": round(lat, 6),
        "cache_used": cache_used,
        "mock": args.mock,
        "save_path": save_info.get("path") if save_info else None,
    })


if __name__ == "__main__":
    main()
