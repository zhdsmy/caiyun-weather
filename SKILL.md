---
name: caiyun-weather
description: "查询和播报彩云天气：支持地址或经纬度天气、降雨/带伞判断、现在是否下雨、雨是否变大、日出日落、能见度、生活指数、预警、地址与经纬度互转；脚本输出结构化 JSON/brief/bundle/short，完整 Markdown 播报由 LLM 按 reference 骨架渲染。"
---

# caiyun-weather

面向中文用户的天气查询 Skill。脚本负责 API 请求、定位转换、数据规整、风险预分级和结构化输出；LLM 负责根据用户意图组织自然语言。脚本返回 `error` 时必须停止，不编造天气。

## 何时使用

- 查询某地天气、温度、体感、空气质量、紫外线、能见度、日出日落、生活指数。
- 判断降雨、带伞、现在是否下雨、附近是否有雨带、雨是否变大、几点雨停。
- 生成天气播报、日报、出行报告、定时推送内容。
- 地址转经纬度，或经纬度转地址。
- 天气问题需要先把地点解析成坐标。

## 不支持

- 台风路径、热带气旋到达时间：本 Skill 只可汇报接口已返回的台风预警，不提供路径预报。
- 历史天气：接口只返回实时与未来预报。
- 卫星云图、雷达图或任意图像。
- 精确逐分钟降雨：仅当 `minutely.available=true` 时可回答分钟级问题；免费 token 常不可用。
- 海外地址地理编码：高德 Key 主要覆盖中国大陆；海外地点优先让用户提供经纬度。

## 脚本与环境

统一用 `${SKILL_DIR}` 表示 Skill 根目录，脚本位于 `${SKILL_DIR}/scripts/`：

- `weather_data.py`：天气查询、mock、healthcheck、保存 LLM 生成的 Markdown。
- `geocode.py`：地址与经纬度互转。
- `formatters.py` / `_common.py`：共享实现，不直接运行。

脚本不读取 `.env`。环境变量由调用方或 shell 注入：

| 环境变量 | 用途 |
|---|---|
| `CAIYUN_TOKEN` | 真实天气查询必需 |
| `AMAP_KEY` / `GAODE_KEY` | 地址查天气、地理编码、逆地理编码必需 |
| `WEATHER_LNG` / `LONGITUDE`、`WEATHER_LAT` / `LATITUDE` | 默认经纬度 |
| `WEATHER_ADDRESS` | 默认地址 |
| `WEATHER_LOCATION` | 展示名覆盖 |
| `WEATHER_CITY`、`WEATHER_DEFAULT_CITY` / `DEFAULT_CITY` | 地理编码城市提示 |
| `WEATHER_TZ` | 时区偏移小时，默认 `8` |
| `WEATHER_DAILY_STEPS` | 日级预报天数，默认 `7`，范围 1-15 |
| `WEATHER_OUTPUT_DIR` | `--save` 默认输出目录 |
| `WEATHER_CACHE_DIR`、`WEATHER_CACHE_SECONDS` | 缓存目录和秒数 |
| `WEATHER_LOG_PATH` | JSONL 调用日志路径 |

定位优先级：用户显式地址/坐标 > `WEATHER_LNG/WEATHER_LAT` > `WEATHER_ADDRESS`。地址查天气需要高德 Key；经纬度查天气只需要彩云 token。有高德 Key 时，经纬度模式会尝试逆地理编码补齐 `geocode.quality`。

## 最小命令

自检：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
```

地址转经纬度：

```bash
python3 ${SKILL_DIR}/scripts/geocode.py geo --address "杭州市西湖风景名胜区" --city 杭州
```

经纬度转地址：

```bash
python3 ${SKILL_DIR}/scripts/geocode.py regeo --lng 120.155100 --lat 30.274100 --extensions base
```

按地址查天气：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖风景名胜区" --city 杭州 --format short
```

按经纬度查天气：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --lng 120.155100 --lat 30.274100 --location 西湖 --format short
```

离线测试数据：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format bundle
```

保存 LLM 已渲染的 Markdown：

```bash
echo "$rendered_md" | python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --save
```

`--save` 只读取 stdin 并保存 Markdown；不负责生成天气播报正文。

## 输出格式

| `--format` | 内容 | 场景 |
|---|---|---|
| `json` | 完整事实层：位置、实时、今日、小时、日级、预警、provider、units | 下游解析、细粒度回答 |
| `brief` | 决策层：headline、keywords、risks、rain、rain_now、umbrella、life_index 等 | 播报、卡片、轻量总结 |
| `bundle` | 同一次查询的 `{json, brief}` | 完整播报、Cron、Workflow；避免重复请求造成数据不一致 |
| `short` | 3-6 行中文直接回答 | 普通聊天、IM 推送 |

完整字段以 `schemas/*.json` 为准。回答中不要输出 API 字段名或天气代码，除非用户明确要求原始数据。

## 执行策略

先判断意图，再选择最小动作：

| 用户意图 | 动作 |
|---|---|
| 地址转坐标 | `geocode.py geo` |
| 坐标转地址 | `geocode.py regeo` |
| 普通天气问答 | `weather_data.py --format short` 或 `--format brief` 后自行回答 |
| 降雨/带伞/现在下雨 | `weather_data.py --format brief`，读决策字段 |
| 完整播报/日报/定时任务 | `weather_data.py --format bundle`，再读 `references/reporting.md` 渲染 Markdown |
| 配置检查 | `weather_data.py --check` |

普通问答不写文件。只有用户要求保存、日报、归档或自动化产物时才用 `--save`。

## 关键判断规则

- 脚本返回顶层 `error`：报告错误并停止。
- `geocode.quality` 为 `low` 或 `unknown`：必须提示“定位可能偏粗，结果可能是区域中心”。
- “现在在下雨吗 / 附近有雨带吗 / 雨会变大吗”：优先读 `brief.rain_now`，不要用 `realtime.skycon` 猜。
- “雨会下多久 / 几点雨停”：优先引用 `brief.keypoint_hourly`；分钟级问题只有 `brief.minutely.available=true` 时才给具体分钟数。
- “今晚会下雨吗 / 白天还下吗”：用 `brief.today_split.day/night`，不要只看全天 `today.skycon`。
- “今天自然日会下多大雨”：用 `today.precip_max` 或 `brief.today_split.full_day_precip_max`；“今晚到明早”用 `today_split.night.precip_max` 或 `day_night_peak_precip`。
- 雨具建议用 `brief.umbrella.code`：`carry_today` 今天带伞；`carry_tomorrow_morning` 今晚可不带、明早备伞；`none` 无需。
- 时间标签按 `day_offset`：`0` 今天，`1` 且 `hour < 6` 明晨，`1` 且 `hour >= 6` 明天。
- 逐小时概率用 `precip_prob_pct`，日级概率用 `precip_prob`；两者都是 0-100，直接加 `%`。
- 风速字段不是阵风，严禁写“阵风”。
- `humidity_pct`、`pressure_hpa` 已换算好，直接使用。
- 日出日落、是否白天读 `brief.daylight` 或 `realtime.sunrise_today/sunset_today/is_daytime`，不要估算。
- 洗车、感冒、穿衣、防晒等读 `brief.life_index`，接口未返回时说明暂无，不自行推导成确定结论。
- 台风路径/到达时间问题直接说明不支持；只有 `alerts[]` 已含台风预警时汇报现有预警。

更多坑点见 `references/pitfalls.md`。

## 完整播报

仅当用户明确要求“播报 / 报告 / 日报 / 富文本通知 / 定时任务”时生成完整 Markdown。

流程：

1. 调用 `weather_data.py --format bundle`，拿同源事实层和决策层。
2. 读取 `references/reporting.md`，按其中骨架渲染 Markdown。
3. 用 `bundle.brief` 做判断，用 `bundle.json` 填表和引用事实。
4. 需要落盘时，把最终 Markdown 通过 stdin 传给 `--save`。

不要分别请求 `json` 和 `brief` 来拼同一份播报。

## 错误处理

- 缺 `CAIYUN_TOKEN`：提示注入彩云 token。
- 地址查天气或地理编码缺高德 Key：提示注入 `AMAP_KEY` / `GAODE_KEY`，或改用 `--lng/--lat`。
- 字段为 `null`：跳过或说明暂无数据。
- 数据冲突：以 brief 的布尔/枚举决策字段优先；长时段结论以 `hourly/days/today_split` 优先，不让短时 `keypoint` 覆盖全天判断。
- 不输出 token、`.env` 内容或完整 API URL；`--check` 的掩码结果也不要反推或复原。

## 引用文件

- `manifest.json`：entrypoints、运行时、环境变量和 schema 映射。
- `schemas/*.json`：输出契约。
- `references/reporting.md`：完整播报骨架、填充规则、轻量回答模板。
- `references/pitfalls.md`：易错点、API 语义、历史设计原因。
- `tests/smoke.py`：离线 smoke test，不访问真实 API。
- `tests/live_check.py`：可选真实 API contract check，只有配置 `CAIYUN_TOKEN` 时运行。

## 自检

```bash
python3 ${SKILL_DIR}/tests/smoke.py
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format bundle
```

有真实 Key 时可选：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
python3 ${SKILL_DIR}/tests/live_check.py
```

输出前检查：

1. 已选择最小必要动作。
2. 地点来自用户、环境变量或脚本返回，不硬编码。
3. 未输出 token、完整 URL、字段名或天气代码。
4. 降雨、雨具、时间标签、概率单位与 brief 字段一致。
5. 普通查询未写文件；完整播报只在用户要求时保存。
