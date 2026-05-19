#!/usr/bin/env python3
"""把 weather_data 原始 JSON 转成不同粒度的输出。

本模块只做**数据语义化**，不做中文播报渲染：

- to_brief(data):   结构化决策数据（供 LLM 生成自然语言 / Markdown 播报）
- to_short(data):   3–6 行自然语言直接回答（极简场景）

完整天气播报的措辞与排版由 LLM 按 SKILL.md 里的骨架规则渲染，不再由脚本硬编码。
函数全部不访问网络；所有规则来自 SKILL.md 的转换表和判断规则。
"""

from __future__ import annotations

from typing import Any

SKYCON_MAP: dict[str, tuple[str, str]] = {
    "CLEAR_DAY": ("晴", "☀️"),
    "CLEAR_NIGHT": ("晴", "🌙"),
    "PARTLY_CLOUDY_DAY": ("多云", "⛅"),
    "PARTLY_CLOUDY_NIGHT": ("多云", "🌥️"),
    "CLOUDY": ("阴", "☁️"),
    "LIGHT_RAIN": ("小雨", "🌦️"),
    "MODERATE_RAIN": ("中雨", "🌧️"),
    "HEAVY_RAIN": ("大雨", "⛈️"),
    "STORM_RAIN": ("暴雨", "⛈️"),
    "RAIN": ("雨", "🌧️"),
    "LIGHT_SNOW": ("小雪", "🌨️"),
    "MODERATE_SNOW": ("中雪", "❄️"),
    "HEAVY_SNOW": ("大雪", "❄️"),
    "STORM_SNOW": ("暴雪", "❄️"),
    "SLEET": ("雨夹雪", "🌨️"),
    "FOG": ("雾", "🌫️"),
    "WIND": ("大风", "💨"),
    "HAZE": ("霾", "😷"),
    "DUST": ("沙尘", "🌪️"),
    "SAND": ("扬沙", "🌪️"),
}

WIND_DIR_THRESHOLDS = [
    (0, "北"),
    (22.5, "东北"),
    (67.5, "东"),
    (112.5, "东南"),
    (157.5, "南"),
    (202.5, "西南"),
    (247.5, "西"),
    (292.5, "西北"),
    (337.5, "北"),
]

ALERT_COLOR = {"01": ("蓝", "🔵"), "02": ("黄", "🟡"), "03": ("橙", "🟠"), "04": ("红", "🔴")}

# ───────────────────────── 级别枚举（供下游做结构化判断） ─────────────────────────

# 风力级别枚举，顺序从弱到强，便于 LLM/下游按 severity 比较
WIND_LEVELS = ("风小", "有风", "风感明显", "风力较大", "强风", "未知")

# 伞具建议枚举；用 code 标识动作，label 用作默认中文描述
UMBRELLA_ACTIONS: dict[str, str] = {
    "carry_today": "建议带伞",
    "carry_tomorrow_morning": "今晚可不带，明早出门前备伞",
    "none": "今天无需带伞",
}

# 风险类型枚举（用于 risks[] 的 kind 字段）
RISK_KINDS = ("alert", "rain", "wind", "aqi", "uv", "humidity", "temperature", "visibility")

# 风险严重度枚举，顺序从低到高
RISK_SEVERITIES = ("info", "low", "medium", "high", "critical")


def describe_skycon(code: Any) -> tuple[str, str]:
    if isinstance(code, str) and code in SKYCON_MAP:
        return SKYCON_MAP[code]
    return ("阴", "☁️")


def describe_wind_dir(angle: Any) -> str | None:
    if not isinstance(angle, (int, float)):
        return None
    result = "北"
    for threshold, label in WIND_DIR_THRESHOLDS:
        if angle >= threshold:
            result = label
    return result


def wind_level(speed: Any) -> dict[str, Any]:
    """风力分级。level 枚举值来自 WIND_LEVELS。"""
    if not isinstance(speed, (int, float)):
        return {"level": "未知", "action": "无法判断风力"}
    if speed < 5:
        return {"level": "风小", "action": "正常出行"}
    if speed < 8:
        return {"level": "有风", "action": "通勤影响不大"}
    if speed < 11:
        return {"level": "风感明显", "action": "骑行注意横风"}
    if speed < 14:
        return {"level": "风力较大", "action": "不建议骑行"}
    return {"level": "强风", "action": "建议减少外出"}


def uv_level(uv: Any, uv_desc: Any) -> dict[str, Any]:
    """UV 分级。level ∈ {弱, 中等, 强, 很强, 未知}。"""
    if isinstance(uv_desc, str) and uv_desc:
        text = uv_desc
    else:
        text = None
    try:
        value = float(uv) if uv is not None else None
    except (ValueError, TypeError):
        value = None
    if text is None and value is not None:
        if value <= 2:
            text = "弱"
        elif value <= 5:
            text = "中等"
        elif value <= 7:
            text = "强"
        else:
            text = "很强"
    advice_map = {
        "弱": "无需特别防晒",
        "中等": "短时户外问题不大，长时间外出建议防晒",
        "强": "外出建议防晒",
        "很强": "外出必须防晒",
    }
    return {"level": text or "未知", "value": value, "advice": advice_map.get(text, "")}


def aqi_level(aqi: Any) -> dict[str, Any]:
    """AQI 分级。level ∈ {优, 良, 轻度污染, 中度污染, 重度污染, 未知}。"""
    try:
        value = int(float(aqi)) if aqi is not None else None
    except (ValueError, TypeError):
        value = None
    if value is None:
        return {"level": "未知", "value": None}
    if value <= 50:
        label = "优"
    elif value <= 100:
        label = "良"
    elif value <= 150:
        label = "轻度污染"
    elif value <= 200:
        label = "中度污染"
    else:
        label = "重度污染"
    return {"level": label, "value": value}


def _rain_now(data: dict[str, Any]) -> dict[str, Any]:
    """「现在/附近是否在下雨」的主信号。

    免费 token 拿不到 minutely（120 分钟逐分钟雨量），但 realtime 里两个雨量总能拿到：
      - local.intensity：脚下网格（1×1km）雨强
      - nearest.intensity + nearest.distance：最近雨带雨强与距离（km）
    合起来足以回答「现在在下雨吗」「附近有雨带吗」「雨会不会变大」。

    返回：
      - is_raining: bool | None。local.intensity > 0.03 认为脚下在下雨
      - local_intensity: float | None。mm/h
      - nearest_intensity: float | None。mm/h；附近雨带的峰值雨强
      - nearest_distance_km: float | None。单位 km；0 = 脚下就是雨中心
      - approaching: bool。local 不在下但 nearest <= 5 km 且 intensity > 0.1 → 雨带在靠近
      - intensifying: bool。local 在下且 nearest.intensity > local.intensity → 雨在变大
      - source: "realtime"（表明这个结论来自 realtime 而非 minutely）
    这些字段足以让 LLM 在 minutely.available=False 时仍能给出「现在在下小雨、雨中心就在脚下」这种准确表述。
    """
    realtime = data.get("realtime") or {}
    local_intensity = realtime.get("precip")  # 已是 local.intensity
    nearest = realtime.get("precip_nearest") or {}
    nearest_intensity = nearest.get("intensity") if isinstance(nearest, dict) else None
    nearest_distance = nearest.get("distance") if isinstance(nearest, dict) else None
    threshold = 0.03
    try:
        local_v = float(local_intensity) if local_intensity is not None else 0.0
    except (TypeError, ValueError):
        local_v = 0.0
    try:
        nearest_v = float(nearest_intensity) if nearest_intensity is not None else 0.0
    except (TypeError, ValueError):
        nearest_v = 0.0
    try:
        nearest_d = float(nearest_distance) if nearest_distance is not None else None
    except (TypeError, ValueError):
        nearest_d = None

    is_raining = local_v > threshold if local_intensity is not None else None
    approaching = (
        not bool(is_raining)
        and nearest_d is not None
        and nearest_d <= 5.0
        and nearest_v > 0.1
    )
    intensifying = bool(is_raining) and nearest_v > local_v + 0.1
    return {
        "is_raining": is_raining,
        "local_intensity": local_intensity,
        "nearest_intensity": nearest_intensity,
        "nearest_distance_km": nearest_distance,
        "approaching": bool(approaching),
        "intensifying": bool(intensifying),
        "source": "realtime",
    }


def _today_split(data: dict[str, Any]) -> dict[str, Any]:
    """今日白天/夜间分时段汇总。供 LLM 回答「今晚会下雨吗/白天还有雨吗」。

    彩云 daily 里 precipitation 是自然日全天；precipitation_20h_32h 是今日 20:00 到
    次日 08:00。两者不是同一时间窗口，不能直接互相覆盖。
    """
    today = data.get("today") or {}
    day_precip_max = today.get("precip_day_max")
    night_precip_max = today.get("precip_night_max")
    day_night_candidates: list[tuple[str, Any]] = [
        ("day", day_precip_max),
        ("night", night_precip_max),
    ]
    numeric_candidates: list[tuple[str, float]] = []
    for period, value in day_night_candidates:
        try:
            if value is not None:
                numeric_candidates.append((period, float(value)))
        except (TypeError, ValueError):
            continue
    if numeric_candidates:
        day_night_peak_period, day_night_peak_precip = max(numeric_candidates, key=lambda item: item[1])
    else:
        day_night_peak_period, day_night_peak_precip = None, None
    return {
        "day": {
            "skycon": today.get("skycon_day"),
            "label": describe_skycon(today.get("skycon_day"))[0] if today.get("skycon_day") else None,
            "emoji": describe_skycon(today.get("skycon_day"))[1] if today.get("skycon_day") else None,
            "precip_prob": today.get("precip_day_prob"),
            "precip_max": today.get("precip_day_max"),
            "precip_avg": today.get("precip_day_avg"),
        },
        "night": {
            "skycon": today.get("skycon_night"),
            "label": describe_skycon(today.get("skycon_night"))[0] if today.get("skycon_night") else None,
            "emoji": describe_skycon(today.get("skycon_night"))[1] if today.get("skycon_night") else None,
            "precip_prob": today.get("precip_night_prob"),
            "precip_max": today.get("precip_night_max"),
            "precip_avg": today.get("precip_night_avg"),
        },
        "full_day_precip_max": today.get("precip_max"),
        "full_day_precip_avg": today.get("precip_avg"),
        "day_night_peak_precip": round(day_night_peak_precip, 2) if day_night_peak_precip is not None else None,
        "day_night_peak_period": day_night_peak_period,
        # Backward-compatible aliases. These are natural-day values, not max(day, night).
        "precip_max": today.get("precip_max"),
        "precip_avg": today.get("precip_avg"),
    }


def humidity_feel(humidity_pct: Any, temp: Any = None) -> str | None:
    """湿度体感分级。结合气温避免把冷雨天写成「闷热」。

    返回枚举：湿冷 / 潮湿 / 闷热 / 略闷 / 偏潮 / 偏干 / 适宜 / None
    """
    if not isinstance(humidity_pct, (int, float)):
        return None
    try:
        temp_value = float(temp) if temp is not None else None
    except (TypeError, ValueError):
        temp_value = None
    if humidity_pct > 85:
        if temp_value is not None and temp_value < 24:
            return "湿冷"
        if temp_value is not None and temp_value < 27:
            return "潮湿"
        return "闷热"
    if humidity_pct >= 75:
        if temp_value is None or temp_value >= 27:
            return "略闷"
        return "偏潮"
    if humidity_pct < 40:
        return "偏干"
    return "适宜"


def _slot_label(day_offset: Any, hour: Any) -> str:
    """根据 day_offset + hour 返回「今天 HH:00 / 明晨 HH:00 / 明天 HH:00 / 后天 HH:00」。"""
    if not isinstance(hour, int):
        return ""
    if day_offset == 0:
        return f"今天 {hour:02d}:00"
    if day_offset == 1:
        if hour < 6:
            return f"明晨 {hour:02d}:00"
        return f"明天 {hour:02d}:00"
    if day_offset == 2:
        return f"后天 {hour:02d}:00"
    return f"{hour:02d}:00"


def _precip_intensity(precip: Any) -> str:
    """按峰值 mm/h 返回中文降雨强度措辞。

    分级参考气象行业常用的逐小时雨量等级：
    - < 0.25：零星小雨
    - 0.25–2.5：小雨
    - 2.5–8：中雨
    - 8–16：大雨
    - >= 16：暴雨
    """
    try:
        val = float(precip) if precip is not None else 0.0
    except (TypeError, ValueError):
        val = 0.0
    if val < 0.25:
        return "零星小雨"
    if val < 2.5:
        return "小雨"
    if val < 8:
        return "中雨"
    if val < 16:
        return "大雨"
    return "暴雨"


def _rain_windows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """把 4 个关键时段 + 异常降雨事件合并为按"严重度"排好序的降雨窗口数组。

    排序规则：精确到 precip 降序 + prob_pct 降序，便于 LLM 直接取 windows[0] 作为主提示。
    """
    hourly = data.get("hourly") or {}
    windows: list[dict[str, Any]] = []
    seen: set[str] = set()
    slot_names = ("morning_rush", "noon", "evening_rush", "night")
    for name in slot_names:
        slot = hourly.get(name)
        if not isinstance(slot, dict):
            continue
        try:
            precip = float(slot.get("precip") or 0)
        except (TypeError, ValueError):
            precip = 0.0
        if precip <= 0:
            continue
        key = slot.get("datetime") or f"{slot.get('day_offset')}-{slot.get('hour')}"
        if key in seen:
            continue
        seen.add(key)
        skycon_cn, _ = describe_skycon(slot.get("skycon"))
        windows.append({
            "label": _slot_label(slot.get("day_offset"), slot.get("hour")),
            "datetime": slot.get("datetime"),
            "day_offset": slot.get("day_offset"),
            "hour": slot.get("hour"),
            "intensity": skycon_cn or _precip_intensity(precip),
            "intensity_by_precip": _precip_intensity(precip),
            "precip": round(precip, 2),
            "prob_pct": slot.get("precip_prob_pct"),
            "source": "slot",
        })
    for item in hourly.get("abnormal", []) or []:
        if item.get("type") != "rain":
            continue
        key = item.get("datetime") or f"{item.get('day_offset')}-{item.get('hour')}"
        if key in seen:
            continue
        seen.add(key)
        windows.append({
            "label": _slot_label(item.get("day_offset"), item.get("hour")),
            "datetime": item.get("datetime"),
            "day_offset": item.get("day_offset"),
            "hour": item.get("hour"),
            "intensity": _precip_intensity(item.get("precip")),
            "intensity_by_precip": _precip_intensity(item.get("precip")),
            "precip": item.get("precip"),
            "prob_pct": None,
            "duration_hours": item.get("duration_hours"),
            "end_hour": item.get("end_hour"),
            "end_datetime": item.get("end_datetime"),
            "source": "abnormal",
        })
    # 按"严重度"排序：precip 峰值优先，概率次之；都缺则保持原时序
    windows.sort(
        key=lambda w: (
            -(float(w.get("precip") or 0.0)),
            -(int(w.get("prob_pct") or 0)),
        )
    )
    return windows


def _umbrella(data: dict[str, Any]) -> dict[str, Any]:
    """返回结构化伞具建议：code（动作枚举） + text（默认中文）。"""
    hourly = data.get("hourly") or {}
    if hourly.get("today_day_rain"):
        code = "carry_today"
    elif hourly.get("next_early_morning_rain"):
        code = "carry_tomorrow_morning"
    else:
        code = "none"
    return {"code": code, "text": UMBRELLA_ACTIONS[code]}


def _rain_summary(data: dict[str, Any]) -> dict[str, Any]:
    hourly = data.get("hourly") or {}
    windows = _rain_windows(data)
    windows_chronological = sorted(
        windows,
        key=lambda w: (
            99 if w.get("day_offset") is None else int(w.get("day_offset") or 0),
            99 if w.get("hour") is None else int(w.get("hour") or 0),
            w.get("datetime") or "",
        ),
    )
    peak_precip = max((float(w.get("precip") or 0) for w in windows), default=0.0)
    # peak_intensity 与 windows[0].intensity 对齐，避免「STORM_RAIN 6mm/h 被 _precip_intensity 归为中雨」的措辞分裂。
    peak_intensity = windows[0].get("intensity") if windows else None
    return {
        "will_rain_today": bool(hourly.get("today_day_rain")),
        "next_early_morning_rain": bool(hourly.get("next_early_morning_rain")),
        "day_rain": bool(hourly.get("day_rain")),
        "night_rain": bool(hourly.get("night_rain")),
        "peak_precip": round(peak_precip, 2) if peak_precip > 0 else 0,
        "peak_intensity": peak_intensity,
        "first_window": windows_chronological[0] if windows_chronological else None,
        "peak_window": windows[0] if windows else None,
        "windows": windows,
        "windows_chronological": windows_chronological,
    }


def _alerts_brief(data: dict[str, Any]) -> list[dict[str, Any]]:
    brief: list[dict[str, Any]] = []
    for alert in data.get("alerts", []) or []:
        level = alert.get("level")
        color, emoji = ALERT_COLOR.get(level, ("", "⚪"))
        brief.append({
            "title": alert.get("title"),
            "desc": alert.get("desc"),
            "level": level,
            "code": alert.get("code"),
            "color": color,
            "emoji": emoji,
            "pub_date": alert.get("pub_date"),
        })
    return brief


def _abnormal_wind(data: dict[str, Any]) -> list[dict[str, Any]]:
    """从 hourly.abnormal 里挑出 wind 类型，用于风险聚合。"""
    result: list[dict[str, Any]] = []
    hourly = data.get("hourly") or {}
    for item in hourly.get("abnormal", []) or []:
        if item.get("type") != "wind":
            continue
        result.append({
            "label": _slot_label(item.get("day_offset"), item.get("hour")),
            "datetime": item.get("datetime"),
            "day_offset": item.get("day_offset"),
            "hour": item.get("hour"),
            "wind_spd": item.get("wind_spd"),
        })
    return result


def _build_risks(
    data: dict[str, Any],
    alerts: list[dict[str, Any]],
    rain: dict[str, Any],
    wind: dict[str, Any],
    aqi: dict[str, Any],
    uv: dict[str, Any],
    humidity_feel_text: str | None,
) -> list[dict[str, Any]]:
    """把所有风险归一化到一个数组：kind / severity / headline / detail。

    - severity 使用 RISK_SEVERITIES 枚举（info < low < medium < high < critical）
    - 已按 severity 从高到低排好序，LLM 可直接按顺序展开
    - headline 是一句简短判断（可直接用），detail 保留结构化上下文（供 LLM 展开细节）

    该数组是"需要特别注意"章节的唯一数据源，避免 LLM 漏条目或重复。
    """
    realtime = data.get("realtime") or {}
    risks: list[dict[str, Any]] = []

    # 预警：级别越高 severity 越高。彩云 level: 01 蓝 / 02 黄 / 03 橙 / 04 红
    level_severity = {"01": "medium", "02": "high", "03": "high", "04": "critical"}
    for alert in alerts:
        risks.append({
            "kind": "alert",
            "severity": level_severity.get(alert.get("level"), "high"),
            "headline": f"{alert.get('emoji', '⚠️')} {alert.get('title') or '天气预警'}",
            "detail": {
                "title": alert.get("title"),
                "desc": alert.get("desc"),
                "level_code": alert.get("level"),
                "raw_code": alert.get("code"),
                "color": alert.get("color"),
                "pub_date": alert.get("pub_date"),
            },
        })

    # 降雨：按峰值雨量分级
    peak = rain.get("peak_precip") or 0
    first_win = rain["windows"][0] if rain.get("windows") else None
    if rain.get("will_rain_today"):
        if peak >= 16:
            severity = "critical"
        elif peak >= 8:
            severity = "high"
        elif peak >= 2.5:
            severity = "medium"
        else:
            severity = "low"
        if first_win:
            intensity = first_win.get("intensity") or "有雨"
            headline = f"🌧️ {first_win.get('label')} {intensity}"
        else:
            headline = "🌧️ 今天有雨"
        risks.append({
            "kind": "rain",
            "severity": severity,
            "headline": headline,
            "detail": {
                "peak_precip": peak,
                "peak_intensity": rain.get("peak_intensity"),
                "windows": rain.get("windows"),
                "will_rain_today": True,
                "next_early_morning_rain": rain.get("next_early_morning_rain"),
            },
        })
    elif rain.get("next_early_morning_rain"):
        risks.append({
            "kind": "rain",
            "severity": "low",
            "headline": "🌦️ 雨主要在明晨",
            "detail": {
                "will_rain_today": False,
                "next_early_morning_rain": True,
                "windows": rain.get("windows"),
            },
        })

    # 风：基于 wind level 与 abnormal wind 事件
    wind_level_text = wind.get("level")
    wind_severity_map = {
        "风感明显": "low",
        "风力较大": "medium",
        "强风": "high",
    }
    if wind_level_text in wind_severity_map:
        abnormal = _abnormal_wind(data)
        risks.append({
            "kind": "wind",
            "severity": wind_severity_map[wind_level_text],
            "headline": f"🌬️ {wind_level_text}",
            "detail": {
                "speed_now": realtime.get("wind_spd"),
                "direction": wind.get("direction"),
                "level": wind_level_text,
                "action": wind.get("action"),
                "abnormal": abnormal,
            },
        })

    # 空气
    aqi_label = aqi.get("level")
    aqi_severity_map = {
        "轻度污染": "low",
        "中度污染": "medium",
        "重度污染": "high",
    }
    if aqi_label in aqi_severity_map:
        risks.append({
            "kind": "aqi",
            "severity": aqi_severity_map[aqi_label],
            "headline": f"😷 空气{aqi_label}",
            "detail": {
                "aqi": aqi.get("value"),
                "level": aqi_label,
                "pm25": realtime.get("pm25"),
            },
        })

    # 紫外线
    uv_level_text = uv.get("level")
    uv_severity_map = {"强": "low", "很强": "medium"}
    if uv_level_text in uv_severity_map:
        risks.append({
            "kind": "uv",
            "severity": uv_severity_map[uv_level_text],
            "headline": f"☀️ 紫外线{uv_level_text}",
            "detail": {
                "level": uv_level_text,
                "value": uv.get("value"),
                "advice": uv.get("advice"),
            },
        })

    # 能见度（雾/霾）：<1km 直接拉到 high，<4km 归为 medium
    vis_level = realtime.get("visibility_level")
    vis_km = realtime.get("visibility")
    vis_severity_map = {"极差": "high", "差": "medium"}
    if vis_level in vis_severity_map:
        risks.append({
            "kind": "visibility",
            "severity": vis_severity_map[vis_level],
            "headline": f"🌫️ 能见度{vis_level}",
            "detail": {
                "level": vis_level,
                "km": vis_km,
                "action": "出行留出充裕时间，注意驾驶安全",
            },
        })

    # 逐分钟降雨：未来 30 分钟内将开始下雨是高优先提醒
    # minutely.available 只有付费 token 才为 true；免费 token 永远拿不到逐分钟数据。
    minutely = data.get("minutely") or {}
    if isinstance(minutely, dict) and minutely.get("available") and minutely.get("has_rain_in_2h"):
        starts = minutely.get("starts_in_minutes")
        if isinstance(starts, int) and 0 <= starts <= 30:
            risks.append({
                "kind": "rain",
                "severity": "medium" if starts <= 15 else "low",
                "headline": f"🌦️ 约 {starts} 分钟后开始下雨",
                "detail": {
                    "source": "minutely",
                    "starts_in_minutes": starts,
                    "peak_intensity": minutely.get("peak_intensity"),
                    "description": minutely.get("description"),
                },
            })

    # 雨带接近/雨在变大：在 minutely 不可用（免费 token）时依靠 realtime 两个 precipitation 推断。
    rain_now = data.get("rain_now") or {}
    if rain_now.get("approaching"):
        nearest_d = rain_now.get("nearest_distance_km")
        nearest_i = rain_now.get("nearest_intensity")
        risks.append({
            "kind": "rain",
            "severity": "low",
            "headline": f"🌦️ 附近 {nearest_d} km 有雨带靠近" if isinstance(nearest_d, (int, float)) else "🌦️ 附近有雨带靠近",
            "detail": {
                "source": "realtime_nearest",
                "nearest_distance_km": nearest_d,
                "nearest_intensity": nearest_i,
            },
        })
    elif rain_now.get("intensifying"):
        risks.append({
            "kind": "rain",
            "severity": "low",
            "headline": "🌧️ 雨在变大",
            "detail": {
                "source": "realtime_nearest",
                "local_intensity": rain_now.get("local_intensity"),
                "nearest_intensity": rain_now.get("nearest_intensity"),
                "nearest_distance_km": rain_now.get("nearest_distance_km"),
            },
        })

    # 湿度体感（仅极端值）
    humidity_pct = realtime.get("humidity_pct")
    if humidity_feel_text in ("闷热", "湿冷"):
        risks.append({
            "kind": "humidity",
            "severity": "low",
            "headline": f"💧 体感{humidity_feel_text}",
            "detail": {
                "humidity_pct": humidity_pct,
                "feel": humidity_feel_text,
                "temp": realtime.get("temp"),
            },
        })

    severity_rank = {s: i for i, s in enumerate(RISK_SEVERITIES)}
    risks.sort(key=lambda r: -severity_rank.get(r.get("severity"), 0))
    return risks


def _keywords(
    rain: dict[str, Any],
    wind: dict[str, Any],
    uv: dict[str, Any],
    aqi: dict[str, Any],
    humidity_feel_text: str | None,
    minutely: dict[str, Any] | None = None,
    rain_now: dict[str, Any] | None = None,
) -> list[str]:
    """生成 2–4 个出门关键词，供速览/卡片顶部直接展示。"""
    keywords: list[str] = []
    # 逐分钟降雨优先级最高（即将下雨）；minutely 可用时优先用它
    if minutely and isinstance(minutely, dict) and minutely.get("available") and minutely.get("has_rain_in_2h"):
        starts = minutely.get("starts_in_minutes")
        if isinstance(starts, int) and 0 <= starts <= 30:
            keywords.append(f"约 {starts} 分钟后下雨")
    elif rain_now and isinstance(rain_now, dict):
        # minutely 不可用（免费 token）时依靠 realtime nearest 补位
        if rain_now.get("is_raining"):
            keywords.append("在下雨")
        elif rain_now.get("approaching"):
            keywords.append("雨带靠近")
    if rain.get("will_rain_today"):
        keywords.append("带伞")
    elif rain.get("next_early_morning_rain"):
        keywords.append("明早备伞")
    if wind.get("level") in ("风感明显", "风力较大", "强风"):
        keywords.append(wind.get("action") or "注意风力")
    if uv.get("level") in ("强", "很强"):
        keywords.append("防晒")
    if aqi.get("level") in ("轻度污染", "中度污染", "重度污染"):
        keywords.append("敏感人群少外出")
    if humidity_feel_text in ("闷热", "湿冷", "潮湿"):
        keywords.append(f"体感{humidity_feel_text}")
    if not keywords:
        keywords.append("正常出行")
    return keywords[:4]


def to_brief(data: dict[str, Any]) -> dict[str, Any]:
    """生成结构化决策数据。

    这是 LLM 生成完整播报的**主要数据源**——所有"该说什么"的判断已经预先做好，
    LLM 只负责按 SKILL.md 的骨架渲染"怎么说"。字段契约：

    - `headline` / `one_liner`：可直接使用的极简摘要
    - `keywords`：2–4 个出门关键词
    - `temp` / `sky` / `wind` / `humidity` / `uv` / `aqi`：各项已分级（带 level 枚举）
    - `rain`：含 `will_rain_today` / `next_early_morning_rain` / `peak_precip` / `windows[]`
    - `umbrella`：结构化伞具建议（code 枚举 + 默认中文）
    - `risks[]`：按 severity 排好序的统一风险数组——"需要特别注意"章节的唯一数据源
    - `alerts`：原始预警列表（和 risks 可能重复，保留原字段供细节展开）
    - `keypoint`：彩云短时提示，仅供参考，不可作为长时结论
    """
    realtime = data.get("realtime") or {}
    today = data.get("today") or {}
    geocode = data.get("geocode") or {}
    skycon_cn, emoji = describe_skycon(realtime.get("skycon"))
    wind = wind_level(realtime.get("wind_spd"))
    wind["direction"] = describe_wind_dir(realtime.get("wind_dir"))
    wind["speed"] = realtime.get("wind_spd")
    uv = uv_level(realtime.get("uv"), realtime.get("uv_desc"))
    aqi = aqi_level(realtime.get("aqi"))
    rain = _rain_summary(data)
    rain_now = _rain_now(data)
    humidity_pct = realtime.get("humidity_pct")
    humidity_feel_text = humidity_feel(humidity_pct, realtime.get("temp"))
    alerts = _alerts_brief(data)

    headline_bits = [data.get("location") or "当前位置", f"{skycon_cn}{emoji}"]
    if isinstance(realtime.get("temp"), (int, float)):
        headline_bits.append(f"{realtime['temp']}℃")
    headline = " ".join(bit for bit in headline_bits if bit)

    one_liner_bits = [f"{data.get('location') or '当前位置'}现在{skycon_cn}"]
    if isinstance(realtime.get("temp"), (int, float)):
        one_liner_bits.append(f"{realtime['temp']}℃")
    if isinstance(realtime.get("app_temp"), (int, float)):
        one_liner_bits.append(f"体感{realtime['app_temp']}℃")
    if rain["will_rain_today"]:
        one_liner_bits.append("今天有雨")
    elif rain["next_early_morning_rain"]:
        one_liner_bits.append("明晨可能下雨")
    else:
        one_liner_bits.append("今天无明显降雨")
    one_liner = "，".join(one_liner_bits)

    # rain_now 要被 _build_risks 读到，需要在调用前先放进一份 data。
    data_with_now = dict(data)
    data_with_now["rain_now"] = rain_now
    risks = _build_risks(data_with_now, alerts, rain, wind, aqi, uv, humidity_feel_text)
    keywords = _keywords(rain, wind, uv, aqi, humidity_feel_text, data.get("minutely"), rain_now)

    # daylight / minutely / life_index / visibility 全部暴露到 brief 顶层，
    # LLM 渲染「几点日出 / 几分钟后下雨 / 洗车指数 / 雾天注意」时不必再回头挖原始 JSON。
    daylight = None
    if realtime.get("sunrise_today") or realtime.get("sunset_today"):
        daylight = {
            "sunrise": realtime.get("sunrise_today"),
            "sunset": realtime.get("sunset_today"),
            "is_daytime": realtime.get("is_daytime"),
        }

    return {
        "schema_version": data.get("schema_version"),
        "location": data.get("location"),
        "location_quality": geocode.get("quality") if isinstance(geocode, dict) else None,
        "headline": headline,
        "one_liner": one_liner,
        "keywords": keywords,
        "temp": {
            "now": realtime.get("temp"),
            "apparent": realtime.get("app_temp"),
            "today_min": today.get("t_min"),
            "today_max": today.get("t_max"),
        },
        "sky": {"code": realtime.get("skycon"), "label": skycon_cn, "emoji": emoji},
        "wind": wind,
        "humidity": {"pct": humidity_pct, "feel": humidity_feel_text},
        "uv": uv,
        "aqi": aqi,
        "visibility": {
            "km": realtime.get("visibility"),
            "level": realtime.get("visibility_level"),
        },
        "rain": rain,
        "rain_now": rain_now,
        "today_split": _today_split(data),
        "umbrella": _umbrella(data),
        "alerts": alerts,
        "risks": risks,
        "daylight": daylight,
        "minutely": data.get("minutely"),
        "life_index": realtime.get("life_index") or {},
        "keypoint": data.get("keypoint"),
        "keypoint_hourly": data.get("keypoint_hourly"),
    }


def to_short(data: dict[str, Any]) -> str:
    """3–6 行直接回答，适合即时聊天回复 / IM 推送场景。

    这是唯一脚本直接拼装文案的输出形态——故意保持极简模板；
    真正的"完整天气播报"由 LLM 基于 to_brief() 的结构化数据自行渲染。
    """
    brief = to_brief(data)
    loc = brief["location"] or "当前位置"
    sky = brief["sky"]["label"]
    emoji = brief["sky"]["emoji"]
    temp = brief["temp"]["now"]
    app_temp = brief["temp"]["apparent"]
    t_min = brief["temp"]["today_min"]
    t_max = brief["temp"]["today_max"]

    lines: list[str] = []
    lines.append(f"{loc}今天整体是{sky}{emoji}，现在{temp}℃，体感{app_temp}℃。")

    range_bits = []
    if t_min is not None and t_max is not None:
        range_bits.append(f"今天大约 {t_min}–{t_max}℃")
    humidity = brief["humidity"]
    if humidity.get("feel") in ("闷热", "湿冷", "潮湿", "略闷", "偏潮", "偏干"):
        range_bits.append(humidity["feel"])
    range_bits.append(f"{brief['wind']['level']}（{brief['wind']['action']}）")
    if brief["aqi"].get("level") not in (None, "未知"):
        range_bits.append(f"空气{brief['aqi']['level']}")
    lines.append("，".join(range_bits) + "。")

    rain = brief["rain"]
    if rain["will_rain_today"]:
        rain_line = "今天有雨，出门带伞。"
        if rain["windows"]:
            # windows 已按严重度排好序，直接取前 2 个提示
            rain_line = "今天有雨，主要时段：" + "、".join(
                f"{w['label']} {w['intensity']}" for w in rain["windows"][:2]
            ) + "。"
        lines.append(rain_line)
    elif rain["next_early_morning_rain"]:
        lines.append("今天白天无明显降雨，明晨可能有雨，今晚可不带伞，明早备伞。")
    else:
        lines.append("未来 24 小时无明显降雨。")

    if brief["alerts"]:
        first = brief["alerts"][0]
        lines.append(f"{first['emoji']} 预警：{first['title']}。")

    advice_bits: list[str] = []
    advice_bits.append(f"雨具：{brief['umbrella']['text']}")
    uv = brief["uv"]
    if uv.get("advice"):
        advice_bits.append(f"防晒：{uv['advice']}")
    if brief["wind"]["level"] in ("风力较大", "强风"):
        advice_bits.append(f"骑行：{brief['wind']['action']}")
    lines.append("出门建议：" + "；".join(advice_bits) + "。")

    return "\n".join(lines)
