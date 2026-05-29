# Pitfalls & Architecture Notes

## Table of Contents

- [Architecture evolution](#architecture-evolution)
- [Why current layout (v6.4)](#why-current-layout-v64)
- [雨问题的三个渠道](#雨问题的三个渠道v64-后)
- [Environment requirements](#environment-requirements)
- [Geocoding notes from Amap docs](#geocoding-notes-from-amap-docs)
- [Format rules from user feedback](#format-rules-from-user-feedback)
- [String traps now avoided by data booleans](#string-traps-now-avoided-by-data-booleans)
- [Cron / Automation notes](#cron--automation-notes)
- [Current script paths](#current-script-paths)
- [Non-negotiables](#non-negotiables)

## Architecture evolution

| Version | Data | Text | Verdict |
|---|---|---|---|
| v1 | Python template script | Fixed template | ❌ 话术固定，无 AI 味 |
| v2 | weather_data.py → JSON | SKILL.md + LLM | ❌ 脚本有文字转换 |
| v3 | MCP tools (mcp-caiyun-weather) | SKILL.md + LLM | ❌ 数据比综合 API 少 |
| v4 | curl 综合 API | SKILL.md + LLM | ⚠️ LLM 解析原始 JSON 负担重 |
| v5 | weather_data.py → JSON | SKILL.md + LLM | ✅ 出行播报稳定 |
| v6 | geocode.py + weather_data.py | SKILL.md + LLM | ✅ 全能天气查询 |
| v6.1 | + _common.py + --check + quality/humidity_pct/pressure_hpa | SKILL.md + LLM | ✅ 内部友好 |
| v6.2 | + formatters.py + manifest + schemas + log + cache + mock | SKILL.md + LLM | ✅ 通用化对接任意 AI 工具 |
| v6.3 | 移除脚本侧 .env 加载，环境变量交由调用方注入 | SKILL.md + LLM | ✅ |
| v6.4 | + rain_now（realtime.precip + nearest 的「现在/附近雨」主信号） / today_split（今日白夜分时雨强） / keypoint_hourly（高密度小时描述）；minutely 增加 available 标记、明确免费 token 不可用 | SKILL.md + LLM | ✅ 当前 |

## Why current layout (v6.4)

- 脚本拆四层：
  - `_common.py`：stdout JSON、HTTP 请求、概率/单位、quality 归一、env_hint、可选 JSONL 日志。
  - `formatters.py`：纯函数把数据 JSON 渲染成 brief / short / report 三档输出，不访问网络。
  - `weather_data.py`：天气查询入口，支持 `--format`、`--mock`、`--save`、缓存、`--check`。
  - `geocode.py`：高德地理编码入口。
- 元数据：`manifest.json` + `schemas/*.json` 让任意 AI 工具直接注册能力。
- 默认输出目录 `~/.cache/caiyun-weather/outputs`；可通过 `WEATHER_OUTPUT_DIR` 覆盖。
- 写盘改为可选（`--save`），无磁盘写权限的 AI 工具直接使用 stdout。
- 脚本不主动读取任何 `.env`，环境变量由调用方所在的 AI 工具或 shell 注入。`--check` 仅做存在性 + 可达性检查。

## 「雨」问题的三个渠道（v6.4 后）

免费 token 拿不到「逐分钟雨量」，所以「雨」问题要拆成三个互补的数据渠道。LLM 需要学会按优先级选用：

| 问题类型 | 优先渠道 | 字段 | 付费 | 免费 |
|---|---|---|---|---|
| 「现在在下雨吗 / 附近有雨带吗 / 雨会变大吗」 | rain_now | `is_raining` / `approaching` / `intensifying` / `nearest_distance_km` | ✅ | ✅ |
| 「雨会下多久 / 几点雨停 / 下多久转成什么」 | keypoint_hourly | hourly.description 中文描述 | ✅ | ✅ |
| 「再过几分钟开始下雨」（需分钟精度） | minutely | `available` / `starts_in_minutes` / `peak_intensity` | ✅ | ❌ |
| 「今天白天/夜里会下多大雨」 | today_split | `day.precip_max` / `night.precip_max` / `day_night_peak_precip` | ✅ | ✅ |
| 「今天起未来 24h 要不要带伞」 | rain.windows / today_day_rain | brief.rain.peak_window / first_window / umbrella.code | ✅ | ✅ |

**付费 token 检测**：`minutely.available == true` 只在付费 token 上出现。LLM 不要纠结「为什么今天 minutely 拿不到」——该字段与是否正在下雨无关。

## Environment requirements

| Capability | Required env |
|---|---|
| 经纬度查天气 | `CAIYUN_TOKEN` |
| 地址查天气 | `CAIYUN_TOKEN` + `AMAP_KEY` or `GAODE_KEY` |
| 地址转经纬度 | `AMAP_KEY` or `GAODE_KEY` |
| 经纬度转地址 | `AMAP_KEY` or `GAODE_KEY` |
| 默认地点查询（经纬度模式） | `WEATHER_LNG` + `WEATHER_LAT`（可选 `WEATHER_LOCATION` 作展示名；有高德 Key 时仍会 regeo 补 geocode quality） |
| 默认地点查询（地址模式） | `WEATHER_ADDRESS`（展示名由高德自动推导；`WEATHER_LOCATION` 可选覆盖） |
| 默认城市兜底 | `WEATHER_DEFAULT_CITY` |
| 输出目录自定义 | `WEATHER_OUTPUT_DIR` |

Do not print token values or any environment file contents. `--check` 已自动掩码，遵循相同原则。

## Geocoding notes from Amap docs

- Geo endpoint: `https://restapi.amap.com/v3/geocode/geo`
- Regeo endpoint: `https://restapi.amap.com/v3/geocode/regeo`
- Geo required params: `key`, `address`; optional `city`, `output`.
- Regeo required params: `key`, `location`; optional `radius`, `extensions`, `roadlevel`, `output`.
- Amap `status=1` means success; `status=0` means failure. Read `info` and `infocode` on failure.
- Regeo `location` format is `lng,lat`; use at most 6 decimals.
- Amap `level` 粒度不定，脚本里归一到 `quality=high/medium/low/unknown`，回答时必须带上该信息。
- Some fields may be arrays instead of strings when empty; scripts normalize common string fields.

## Format rules from user feedback

### Time labels

- ✅ `day_offset=1` 且 `hour < 6`：写“明晨 02:00”。
- ❌ 永远不要写“今晚 02:00”。

### Wind descriptions

- ✅ 使用 m/s 阈值：`<5` 风小，`5-8` 有风，`8-11` 风感明显，`11-14` 不建议骑行，`>=14` 强风减少外出。
- ❌ 不要写“阵风”，因为 API 没有 gust 数据。

### UV wording

- ✅ 弱→无需；中等→“短时问题不大，长时间户外建议”；强→“外出建议”；很强→“必须”。
- ❌ 中等 UV 不要写“外出需防晒”或“必须防晒”。

### Rain wording

- ✅ 今天日间雨：今天出门带伞。
- ✅ 仅明晨雨：今天白天无明显降雨，今晚可不带伞，明早备伞。
- ✅ 「今晚/白天会不会下雨」优先用 today_split.day/night，而不是全天 skycon。
- ✅ 「今天自然日雨多大」优先用 today.precip_max / today_split.full_day_precip_max（mm/h）。
- ✅ 「今晚到明早雨多大」用 today_split.night.precip_max 或 day_night_peak_precip。
- ✅ `daily.precipitation_20h_32h` 是今日 20:00 到次日 08:00，不是自然日 20:00 到 24:00。
- ✅ 播报时间线用 rain.first_window / windows_chronological；风险主提示用 rain.peak_window。
- ❌ 明晨有雨时，不要笼统写「未来 24 小时无降雨」。

### Daily API window semantics

- `daily.precipitation[i]`：自然日全天。
- `daily.precipitation_08h_20h[i]`：当天 08:00-20:00。
- `daily.precipitation_20h_32h[i]`：当天 20:00 到次日 08:00，可能包含次日清晨降雨。
- `forecast_keypoint`：短时关键点；`hourly.description`：未来 24 小时高密度中文描述。

### Alerts

- 彩云预警有些响应提供 `level`，有些需要从 `code` 末两位解析颜色等级。
- 当前脚本使用 `level = alert.level or alert.code[-2:]`。

### Real-time rain (now / nearby / intensifying)

v6.4 新增 `rain_now`，由 realtime.precipitation 里的 local + nearest 两块汇总出来。用法：

- `is_raining=true` + 「现在在下{雨强描述}」。
- `approaching=true` + 「附近 {nearest_distance_km}km 有雨带」。
- `intensifying=true` + 「雨在变大」。
- 三项都不成立 → 脚下与附近都没雨。

这里不要用 `realtime.skycon` 猜（例如 `LIGHT_RAIN` skycon 不一定代表脚下有雨，也可能是 1km 以外的雨带）。

### Minutely vs keypoint_hourly vs rain_now

- ✅ 付费 token 且需要「几分钟后」这种分钟精度 → 优先用 minutely。
- ✅ minutely.available=false 且需要「还要下多久 / 几点停」→ 用 keypoint_hourly。
- ✅ minutely.available=false 且需要「现在/附近/雨变大吗」→ 用 rain_now。
- ❌ 不要用 hourly.precipitation 拼「还有 23 分钟下雨」这种虚拟精度。
- ❌ 不要被 `keypoint_hourly == forecast_keypoint` 迷惑，二者在跨度上一般一致，但 keypoint_hourly 是官方推荐的高密度出口，优先选它。

### Probability units

- ✅ `today/days.precip_prob` 已是 `0-100`，直接输出 `%`。
- ✅ 逐小时优先用 `precip_prob_pct`（`0-100`）。
- ✅ 需要原值时可用 `precip_prob`（`0-1`），但避免再乘 100 两次。
- ❌ 不要把日级概率再乘 100，也不要把逐小时 `precip_prob` 直接当百分数。

### Humidity / pressure

- ✅ 使用 `humidity_pct`（0–100）和 `pressure_hpa`（hPa）直接写结论。
- ❌ 不要手动再把 `humidity` 乘 100 或把 `pressure` 除 100，容易一次没乘一次乘两次。

### Umbrella consistency

- ✅ 雨具必须匹配降雨标记：今天日间雨→带伞；仅明晨雨→今晚可不带明早备；无雨→无需。
- ❌ 不要在 `next_early_morning_rain=true` 时简单写“无需带伞”，必须补“明早备伞”。

### General query vs report

- ✅ “今天天气怎么样 / 会下雨吗 / 要带伞吗”直接回答，不写文件。
- ✅ “生成播报 / 写日报 / 定时任务”使用完整播报格式并写文件。
- ❌ 不要把所有天气查询都变成长篇播报。

### keypoint 使用边界

- ✅ 仅作短时提示（未来两小时类描述）。
- ❌ 不要用 keypoint 决定“今天要不要带伞”“明天穿什么”这种跨时段结论，全部以 `hourly/days` 字段为准。

### Geocoding quality reminder

- ✅ `quality=high/medium`：可直接使用 `best` 坐标，不需特别说明。
- ✅ `quality=low/unknown`：必须显式提示“定位可能偏粗，结果是区域中心”。
- ❌ 不要在区县级结果上假装是具体地点的天气。

## String traps now avoided by data booleans

- 旧坑：`"无降雨".contains("雨") == true`。
- 当前做法：使用 `day_rain`、`night_rain`、`today_day_rain`、`next_early_morning_rain` 布尔字段，不从中文句子反推天气。

## Cron / Automation notes

完整播报保存到：

```text
${WEATHER_OUTPUT_DIR:-~/.cache/caiyun-weather/outputs}/weather_{YYYYMMDD}_{HHMM}.md
```

默认文件名精确到分钟，避免同一天内多次手动或自动触发时覆盖旧报告。

- Cron/Automation 任务必须具备 terminal + file 写入权限，否则只能生成内容、无法保存文件。
- 改名后老定时任务如果还引用 `@skill:weather-report`，必须同步更新成 `@skill:caiyun-weather`，否则静默失败不会触发。
- 自动任务启动前建议先跑 `weather_data.py --check`，确认 `CAIYUN_TOKEN` / `AMAP_KEY` 已注入且可达，避免整夜写空报告。

## Current script paths

- Skill root: `${SKILL_DIR}`
- Shared utils: `${SKILL_DIR}/scripts/_common.py`
- Formatters: `${SKILL_DIR}/scripts/formatters.py`
- Weather: `${SKILL_DIR}/scripts/weather_data.py`
- Geocode: `${SKILL_DIR}/scripts/geocode.py`

## Non-negotiables

- If any script returns `error`, stop and report it. Do not fabricate weather.
- No hardcoded location names; always use JSON `location` or geocoding result.
- No political/current affairs content; this Skill is weather/location/weather-query only.
- Do not expose tokens, environment file contents, or full API URLs.
