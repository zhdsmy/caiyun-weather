# caiyun-weather

[中文版本](README.zh-CN.md) | [English Version](README.md)

`caiyun-weather` is a weather-query Skill designed primarily for Chinese-language users. It provides structured weather data, geocoding, risk pre-classification, and lightweight answer data through CLI scripts. Full Markdown weather reports are rendered by an AI agent according to the report skeleton in `SKILL.md`.

## Features

- **Weather queries**: Query realtime weather, today's summary, hourly forecasts, and daily forecasts by address or coordinates.
- **Geocoding**: Convert structured addresses to coordinates, and reverse coordinates back to address information.
- **Decision fields**: Pre-classified rain, umbrella, wind, UV, AQI, humidity, visibility, and life-index signals.
- **Full reports**: Output `brief` decision JSON for an AI agent to render complete Markdown weather reports using `SKILL.md`.
- **Offline demos**: Built-in `sunny`, `rain`, and `alert` mock scenarios for demos, debugging, and CI.
- **Safety boundary**: Scripts do not read `.env` files and do not print raw tokens; environment variables are injected by the caller.

## Requirements

- Python 3.10+
- No third-party Python dependencies
- Caiyun API token for real weather queries
- Amap Web Service Key for address-based queries and geocoding

## Environment variables

| Variable | Description | Required for |
|---|---|---|
| `CAIYUN_TOKEN` | Caiyun API token | All real weather queries |
| `AMAP_KEY` / `GAODE_KEY` | Amap Web Service Key | Address weather queries, geocoding, reverse geocoding |
| `WEATHER_LNG` / `LONGITUDE` | Default longitude | Default coordinate-based location |
| `WEATHER_LAT` / `LATITUDE` | Default latitude | Default coordinate-based location |
| `WEATHER_ADDRESS` | Default address | Default address-based location |
| `WEATHER_LOCATION` | Display name in output | Optional |
| `WEATHER_CITY` | City hint for geocoding | Optional |
| `WEATHER_DEFAULT_CITY` / `DEFAULT_CITY` | Fallback default city | Optional |
| `WEATHER_TZ` | Timezone offset in hours, default `8` | Optional |
| `WEATHER_DAILY_STEPS` | Number of daily forecast steps, default `7` | Optional |
| `WEATHER_OUTPUT_DIR` | Default output directory for `--save` | Optional |
| `WEATHER_CACHE_DIR` | Cache directory | Optional |
| `WEATHER_CACHE_SECONDS` | Cache TTL in seconds, default `0` | Optional |
| `WEATHER_LOG_PATH` | JSONL call log path | Optional |

Location priority: explicit address or coordinates win; without explicit input, `WEATHER_LNG/WEATHER_LAT` are used first, then `WEATHER_ADDRESS`.

## Quick start

The examples below use `${SKILL_DIR}` as the Skill root directory.

### Health check

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
```

### Query weather by address

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区西湖风景名胜区" --format short
```

### Query weather by coordinates

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --lng 120.155100 --lat 30.274100 --location 西湖 --format short
```

### Geocode an address

```bash
python3 ${SKILL_DIR}/scripts/geocode.py geo --address "杭州市西湖风景名胜区" --city 杭州
```

### Reverse geocode coordinates

```bash
python3 ${SKILL_DIR}/scripts/geocode.py regeo --lng 120.155100 --lat 30.274100 --extensions base
```

## Output formats

`weather_data.py` supports these output formats:

| Format | Description | Best for |
|---|---|---|
| `json` | Full structured weather data | Downstream parsing, AI-composed answers |
| `brief` | Decision fields for risks, rain, umbrella, and life suggestions | Full Markdown reports, compact cards |
| `short` | A direct 3–6 line Chinese answer | Chat replies, IM pushes |

Example:

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --format brief
```

## Generate a full Markdown report

Full reports are not hard-coded by the script. They are rendered by an AI agent according to the “Full Weather Report” skeleton and filling rules in `SKILL.md`.

Recommended flow:

1. Run `weather_data.py --format brief` to get structured decision data.
2. If hourly or 7-day tables need more detail, run `weather_data.py --format json` as well.
3. Let the AI agent render Markdown according to the report skeleton in `SKILL.md`.
4. If persistence is needed, pass the final Markdown to `--save` through stdin.

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --format brief
```

Save the AI-rendered Markdown:

```bash
echo "$rendered_md" | python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --save
```

Note: `--save` only saves Markdown received from stdin. It does not fetch weather JSON or render a report by itself.

## Mock / offline demos

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --mock sunny --format short
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format brief
python3 ${SKILL_DIR}/scripts/weather_data.py --mock alert --format json
```

## Integration

- **AI Skill**: Load this directory and follow the execution strategy in `SKILL.md`.
- **Workflow / Cron**: Inject environment variables in the runtime and call `weather_data.py --format brief` or `--format short`.
- **IM bots**: Send `--format short` output directly for compact messages; for rich messages, let an AI agent render Markdown first.
- **MCP / tool wrappers**: Register commands from the entrypoints in `manifest.json`.

## Known limitations

- Historical weather queries are not supported.
- Satellite maps, radar maps, and other images are not provided.
- Typhoon path forecasts are not supported; the Skill can only report typhoon alerts returned by the weather API.
- Free Caiyun tokens usually do not provide minute-level precipitation data; the script falls back to hourly descriptions and nearby-rain signals.
- Amap geocoding mainly covers mainland China; for overseas locations, pass coordinates directly.

## Privacy and safety

- Scripts do not load `.env` files automatically.
- Health-check output masks tokens and keys.
- Answers and logs should not contain full API URLs, tokens, or raw secrets.
- Local caches, outputs, and logs should be stored in user-owned or controlled directories.

## License

This project is licensed for personal, non-commercial use only. Personal learning, research, testing, and self-use are allowed; commercial use, resale, commercial integration, or use as part of a paid service is prohibited. See `LICENSE` for details.
