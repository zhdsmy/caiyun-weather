#!/usr/bin/env python3
"""高德地理编码/逆地理编码工具（caiyun-weather 内置）。

功能：
  - geo:   结构化地址 -> 经纬度
  - regeo: 经纬度 -> 结构化地址

输出：始终向 stdout 输出 JSON；错误也输出 JSON，便于 Skill/Cron/LLM 消费。

环境变量由调用方注入，脚本只读取：
  AMAP_KEY 或 GAODE_KEY  高德 Web 服务 Key
  WEATHER_DEFAULT_CITY    未显式传 --city 时使用的兜底城市

示例：
  python3 geocode.py geo --address "杭州市西湖风景名胜区" --city 杭州
  python3 geocode.py regeo --lng 120.155100 --lat 30.274100 --extensions base
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (
    amap_key,
    amap_quality,
    default_city,
    emit,
    env_hint,
    load_env_file,
    normalize_amap,
    parse_lng_lat,
    pick_first_string,
    request_json,
    validate_lng_lat,
)

GEOCODE_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
REGEOCODE_ENDPOINT = "https://restapi.amap.com/v3/geocode/regeo"


def split_lng_lat(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",", 1)]
    if len(parts) != 2:
        emit({"error": "location 必须是 '经度,纬度' 格式", "location": value}, exit_code=1)
    return parse_lng_lat(parts[0], parts[1])


def command_geo(args: argparse.Namespace, key: str) -> None:
    if not args.address:
        emit({"error": "地理编码缺少 address 参数"}, exit_code=1)

    city = args.city or default_city()
    data = request_json(GEOCODE_ENDPOINT, {
        "key": key,
        "address": args.address,
        "city": city,
        "output": "JSON",
    }, service="高德地理编码")
    normalize_amap(data)

    results: list[dict[str, Any]] = []
    for item in data.get("geocodes", []):
        location = item.get("location")
        if not isinstance(location, str) or "," not in location:
            continue
        lng, lat = split_lng_lat(location)
        level = item.get("level")
        results.append({
            "formatted_address": item.get("formatted_address"),
            "country": item.get("country"),
            "province": pick_first_string(item.get("province")),
            "city": pick_first_string(item.get("city")),
            "citycode": item.get("citycode"),
            "district": pick_first_string(item.get("district")),
            "street": pick_first_string(item.get("street")),
            "number": pick_first_string(item.get("number")),
            "adcode": item.get("adcode"),
            "level": level,
            "quality": amap_quality(level),
            "location": location,
            "lng": round(lng, 6),
            "lat": round(lat, 6),
        })

    emit({
        "mode": "geo",
        "query": {"address": args.address, "city": city},
        "count": len(results),
        "results": results,
        "best": results[0] if results else None,
    })


def command_regeo(args: argparse.Namespace, key: str) -> None:
    if args.location:
        lng, lat = split_lng_lat(args.location)
    else:
        if args.lng is None or args.lat is None:
            emit({"error": "逆地理编码需要 --location 或同时提供 --lng/--lat"}, exit_code=1)
        lng = float(args.lng)
        lat = float(args.lat)
        validate_lng_lat(lng, lat)

    location = f"{lng:.6f},{lat:.6f}"
    data = request_json(REGEOCODE_ENDPOINT, {
        "key": key,
        "location": location,
        "radius": args.radius,
        "extensions": args.extensions,
        "roadlevel": args.roadlevel,
        "output": "JSON",
    }, service="高德逆地理编码")
    normalize_amap(data)

    regeocode = data.get("regeocode", {})
    component = regeocode.get("addressComponent", {}) if isinstance(regeocode, dict) else {}
    street_number = component.get("streetNumber", {}) if isinstance(component, dict) else {}
    neighborhood = component.get("neighborhood", {}) if isinstance(component, dict) else {}
    building = component.get("building", {}) if isinstance(component, dict) else {}

    result = {
        "formatted_address": regeocode.get("formatted_address"),
        "country": component.get("country"),
        "province": pick_first_string(component.get("province")),
        "city": pick_first_string(component.get("city")),
        "citycode": component.get("citycode"),
        "district": pick_first_string(component.get("district")),
        "adcode": component.get("adcode"),
        "township": pick_first_string(component.get("township")),
        "towncode": component.get("towncode"),
        "neighborhood": neighborhood.get("name") if isinstance(neighborhood, dict) else None,
        "building": building.get("name") if isinstance(building, dict) else None,
        "street": street_number.get("street") if isinstance(street_number, dict) else None,
        "number": street_number.get("number") if isinstance(street_number, dict) else None,
        "location": location,
        "lng": round(lng, 6),
        "lat": round(lat, 6),
        "quality": "high" if street_number.get("number") else "medium",
    }

    if args.extensions == "all" and isinstance(regeocode, dict):
        result["pois"] = regeocode.get("pois", [])[:10]
        result["roads"] = regeocode.get("roads", [])[:10]
        result["aois"] = regeocode.get("aois", [])[:10]

    emit({
        "mode": "regeo",
        "query": {"location": location, "radius": args.radius, "extensions": args.extensions},
        "result": result,
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="高德地理编码/逆地理编码 JSON 工具")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    geo = subparsers.add_parser("geo", help="结构化地址转经纬度")
    geo.add_argument("--address", required=True, help="结构化地址，如 杭州市西湖风景名胜区")
    geo.add_argument("--city", help="限定城市，缺省读 WEATHER_DEFAULT_CITY")

    regeo = subparsers.add_parser("regeo", help="经纬度转结构化地址")
    regeo.add_argument("--location", help="经度,纬度，如 120.155100,30.274100")
    regeo.add_argument("--lng", type=float, help="经度")
    regeo.add_argument("--lat", type=float, help="纬度")
    regeo.add_argument("--radius", type=int, default=1000, help="搜索半径，0-3000 米，默认 1000")
    regeo.add_argument("--extensions", choices=["base", "all"], default="base", help="返回 base 或 all，默认 base")
    regeo.add_argument("--roadlevel", choices=["0", "1"], help="0=所有道路，1=仅主干道")

    return parser


def main() -> None:
    load_env_file()
    parser = build_parser()
    args = parser.parse_args()
    
    # 如果是帮助命令，不需要检查环境变量
    if not hasattr(args, 'mode') or args.mode is None:
        parser.print_help()
        return
    
    key = amap_key()
    if not key:
        emit({
            "error": "缺少高德 Web 服务 Key",
            "hint": env_hint() + " 请注入 AMAP_KEY 或 GAODE_KEY。",
        }, exit_code=1)

    if args.mode == "geo":
        command_geo(args, key)
    elif args.mode == "regeo":
        command_regeo(args, key)
    else:
        emit({"error": f"未知模式: {args.mode}"}, exit_code=1)


if __name__ == "__main__":
    main()
