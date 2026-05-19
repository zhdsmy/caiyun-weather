---
name: caiyun-weather
description: "通用天气查询 Skill：脚本吐结构化天气 JSON 与决策字段（含「现在在下雨吗/雨在变大吗」主信号、今日白夜分时雨强、高密度小时描述、生活指数），完整播报由 LLM 按骨架渲染。另提供结构化地址 ↔ 经纬度互转。"
version: 6.4.0
agent_created: true
---

# caiyun-weather

提供一套面向中文用户的天气查询能力：

- 结构化地址 → 经纬度。
- 经纬度 → 结构化地址。
- 地址或经纬度 → 彩云天气结构化 JSON（含预分级的决策字段）。
- 轻量天气问答：今天天气怎么样、会下雨吗、要带伞吗。
- 生成完整出行天气播报：由 LLM 基于 `brief` 与骨架模板自行渲染，不由脚本硬编码。

核心边界：**脚本只给能力和数据，LLM 给语言和判断**。脚本负责 API 请求、定位转换、数据规整、风险预分级；措辞、结构组织、场景化表达由 LLM 完成。脚本失败或数据缺失时，停止生成并报告错误，不编造天气。

## 触发场景

当用户提出以下任一需求时使用本 Skill：

1. 查询某地天气：`今天杭州天气怎么样`、`西湖现在热吗`。
2. 查询降雨：`什么时候下雨`、`西湖今天会下雨吗`、`今晚要带伞吗`、`现在在下雨吗`、`附近有雨带吗`、`雨会变大吗`（现在/附近主信号）、`再过几分钟会下雨`（仅付费 token）。
3. 生成播报：`生成天气播报`、`发一份出行天气报告`。
4. 地址与坐标转换：`杭州市西湖风景名胜区经纬度是多少`、`120.15,30.27 是哪里`。
5. 日出日落、能见度、生活指数：`明天几点日出`、`今天能见度好吗`、`今天适合洗车吗`、`穿什么衣服`、`会感冒吗`。
6. 需要先把地点转成坐标再查天气的任何天气问题。

### 已知不支持的查询（遇到直接拒答，不要编造）

- **台风路径预报 / 热带气旋到达时间**（如「杭州最近会有台风么」「台风影响杭州是几号」）。彩云 `/v2.6/weather` 接口不返回台风路径；仅当与当前位置相关的台风预警已发布时，`alerts` 里会有台风蓝/黄/橙/红预警——这只能告知「已经迫近」，不能告知「几号到达」。遇到这类查询应明确告诉用户「本 Skill 不提供台风路径预报，建议查阅中央气象台台风网、香港天文台等专业站点」，再结合 `alerts` 里已有的台风预警汇报现状。
- **逐分钟降雨在免费 token 上永远不可用**。彩云 v2.6 综合接口对免费 token（`result.primary == 0`）返回 `minutely: {}`，脚本会设 `minutely.available=false`。遇到「再过几分钟会下雨」「雨还要下多久」这类**需要逐分钟精度**的问题时：优先试 `keypoint_hourly`（包含「16 点钟后雨停」这种高密度描述）；再不行用 `rain_now`（脚下是否在下雨、附近有没有雨带）；都不够准时应如实告知「当前 token 不支持逐分钟降雨预报」。不要用 hourly.precipitation 凑一个假装精确到分钟的答案。
- **历史天气查询**（如「昨天多少度」「上周的天气」）。接口只返回实时与未来预报。
- **卫星云图 / 雷达图 / 任意图像**：本 Skill 只输出文本数据，不提供图像。
- **全球任意地点**：高德 Web 服务 Key 主要覆盖中国大陆；国外地址需走 `--lng/--lat`，且彩云在大陆以外的精度较低。

## 执行策略

先判断用户意图，再选择最小必要动作：

| 意图 | 动作 | 输出 |
|---|---|---|
| 地址转经纬度 | `geocode.py geo` | 结构化地址、经纬度、匹配级别、quality |
| 经纬度转地址 | `geocode.py regeo` | 结构化地址、行政区划、quality |
| 轻量天气问答 | `weather_data.py` | 3–6 行直接结论，不写文件 |
| 降雨/带伞判断 | `weather_data.py` | 聚焦降雨时段、概率、雨具建议，不写文件 |
| 完整天气播报 | `weather_data.py --format brief`，LLM 按骨架渲染 Markdown | 完整 Markdown；需要落盘时把成品通过 stdin 传给 `--save` |
| 配置自检 | `weather_data.py --check` | 彩云/高德 Key 可用性报告，不请求天气 |

## 脚本路径约定

统一使用 `${SKILL_DIR}` 指代本 Skill 的根目录，由调用方解析为实际安装路径。

所有脚本都在 `${SKILL_DIR}/scripts/`：

- `${SKILL_DIR}/scripts/weather_data.py`
- `${SKILL_DIR}/scripts/geocode.py`
- `${SKILL_DIR}/scripts/formatters.py`（被 `weather_data.py` 复用，不直接运行）
- `${SKILL_DIR}/scripts/_common.py`（共享工具层，不直接运行）

如果 Skill 被迁移或重命名，调用方只需更新 `${SKILL_DIR}` 的解析方式，文档无需改动。

## 环境变量

脚本本身不加载任何 `.env` 文件，环境变量由调用方所在的 AI 工具运行时或 shell 注入即可。下表列出脚本会读取的所有变量。

| 环境变量 | 说明 | 必填场景 |
|---|---|---|
| `CAIYUN_TOKEN` | 彩云 API token | 所有天气查询 |
| `AMAP_KEY` / `GAODE_KEY` | 高德 Web 服务 Key | 地址查天气、地理编码、逆地理编码 |
| `WEATHER_LNG` / `LONGITUDE` | 默认经度 | 经纬度模式：默认地点天气查询 |
| `WEATHER_LAT` / `LATITUDE` | 默认纬度 | 经纬度模式：默认地点天气查询 |
| `WEATHER_ADDRESS` | 默认结构化地址（如「杭州市西湖区西湖风景名胜区」） | 地址模式：默认地点天气查询，需高德 Key |
| `WEATHER_LOCATION` | 输出用位置名（如「西湖」） | 仅当想覆盖自动推导的标题时使用，可选 |
| `WEATHER_CITY` | 地理编码限定城市 | 可选 |
| `WEATHER_DEFAULT_CITY` / `DEFAULT_CITY` | 未显式传城市时的兜底 | 可选 |
| `WEATHER_TZ` | 时区偏移小时 | 可选，默认 `8` |
| `WEATHER_DAILY_STEPS` | 日级预报天数（1–15） | 可选，默认 `7`；也可用 `--days N` 覆盖 |
| `WEATHER_OUTPUT_DIR` | 完整播报输出目录 | 可选，默认 `~/.cache/caiyun-weather/outputs` |
| `WEATHER_CACHE_DIR` | 缓存目录 | 可选，默认 `~/.cache/caiyun-weather` |
| `WEATHER_CACHE_SECONDS` | 缓存秒数 | 可选，默认 `0`（不缓存） |
| `WEATHER_LOG_PATH` | JSONL 调用日志路径 | 可选，未配置则不写日志 |

不同 AI 工具有自己的环境变量加载机制，遵循各自约定即可；脚本只检查变量是否到位，缺什么由 `--check` 直接报告。

**职责区分**：`WEATHER_ADDRESS` 和 `WEATHER_LNG/LAT` 是**二选一的定位输入**，脚本用它们去查天气；`WEATHER_LOCATION` 只是**输出标题的展示名**。地址模式下脚本会自动把高德返回的 `formatted_address` 作为 location，通常不必再设 `WEATHER_LOCATION`；经纬度模式下如有高德 Key，脚本仍会逆地理编码补齐 `geocode.quality`，但展示名可继续由 `WEATHER_LOCATION` 覆盖。

定位规则：

1. 用户给出明确地址 → 优先 `geo` 高德地理编码。
2. 用户给出经纬度 → 直接查天气；若有高德 Key，会用 `regeo` 补齐结构化地址与 `quality`（`WEATHER_LOCATION` 仅覆盖展示名）。
3. 用户未给地点 → 按优先级使用环境变量：
   - 有 `WEATHER_LNG/WEATHER_LAT` → 走经纬度模式，`WEATHER_LOCATION` 作为展示名；
   - 否则有 `WEATHER_ADDRESS` → 走地址模式，展示名由高德 `formatted_address` 自动推导（`WEATHER_LOCATION` 可选覆盖）。
4. 地址查天气需要 `AMAP_KEY` 或 `GAODE_KEY`；经纬度查天气只要 `CAIYUN_TOKEN`。

## 脚本用法

统一使用 `python3` 调用，跨 macOS / Linux / WSL 通用；具体解释器路径由调用环境决定。

### 自检

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
```

返回 JSON：

```js
{
  mode: "check",
  env: {
    CAIYUN_TOKEN: "ab***yz" | null,    // 掩码显示，不泄露原值
    AMAP_KEY:     "gh***01" | null,
WEATHER_DEFAULT_CITY: "杭州" | null,
    WEATHER_OUTPUT_DIR:   "~/.cache/caiyun-weather/outputs"
  },
  caiyun: { configured: true, ok: true,  detail: "reachable" },
  amap:   { configured: true, ok: false, detail: "INVALID_USER_KEY" }
}
```

用途：注入完环境变量后先跑一次自检，确认所需 Key 已到位且可用，再做真实查询。

### 地址转经纬度

```bash
python3 ${SKILL_DIR}/scripts/geocode.py geo --address "杭州市西湖风景名胜区" --city 杭州
```

重点字段：

```js
{
  mode: "geo",
  count: 1,
  best: {
    formatted_address,
    province, city, district, adcode,
    level,
    quality: "high" | "medium" | "low" | "unknown",
    location: "经度,纬度",
    lng, lat
  }
}
```

`quality` 归一自高德 `level`：
- `high`：门牌号、楼栋、兴趣点、商圈。
- `medium`：街道、乡镇、道路、交叉口。
- `low`：区县、城市、省。
- `unknown`：无法识别。

回答里 `quality` 为 `low` 或 `unknown` 时，必须提示“定位偏粗，结果可能是区域中心”。

### 经纬度转地址

```bash
python3 ${SKILL_DIR}/scripts/geocode.py regeo --lng 120.155100 --lat 30.274100 --extensions base
```

需要附近 POI 时使用 `--extensions all`；日常天气回答优先用 `base`，减少噪音。

### 按地址查天气

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖风景名胜区" --city 杭州
```

### 按经纬度查天气

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --lng 120.155100 --lat 30.274100 --location 西湖
```

### 可选写盘

```bash
# LLM 先按骨架渲染出完整 Markdown，再通过管道交给 --save 落盘
echo "$report" | python3 ${SKILL_DIR}/scripts/weather_data.py --lng 120.155100 --lat 30.274100 --save
echo "$report" | python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --save /tmp/report.md
```

`--save` 只在显式传入时生效，且从 **stdin** 读取落盘内容：不传不写盘；传 `--save` 走默认路径；传 `--save /path` 写到指定文件。脚本不再自己渲染播报，交给 LLM 按骨架自由渲染后再回写，避免「脚本模板腔」的死板表达。

### 输出形式 `--format`

`weather_data.py` 支持 4 种输出：

| 值 | 内容 | 适合场景 |
|---|---|---|
| `json`（默认） | 完整数据 JSON，包含 `provider/units/timezone/realtime/today/hourly/days/output_dir/cache_used/save` 等字段 | LLM 自己拼回答；自动化下游解析 |
| `brief` | 结构化决策 JSON：headline、keywords、temp、wind、uv、aqi、rain（已按严重度排序的 windows）、umbrella、alerts、**risks（统一风险数组）** | **渲染完整天气播报的主要数据源**；移动端卡片；轻量摘要 |
| `bundle` | 同一次查询派生出的 `{json, brief}`，`json` 是完整事实层，`brief` 是从它派生的决策层 | 完整播报、Cron、Workflow；避免重复请求导致数据不一致 |
| `short` | 3–6 行中文直接回答 | 即时聊天回复；Telegram/IM 推送 |

### 离线 / 演示模式 `--mock`

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --mock sunny --format short
python3 ${SKILL_DIR}/scripts/weather_data.py --mock alert --format brief
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format bundle
```

不调用任何外部 API，按当前时区动态生成内置示例（`sunny / rain / alert`），供 CI、Demo、单元测试使用。mock 会生成未来 24 小时关键时段和 7 天趋势，避免演示报告出现旧日期或重复时段。

### 缓存与日志

- 缓存：`WEATHER_CACHE_SECONDS=600` 或 `--cache-seconds 600`，按经纬度 + hourlysteps + dailysteps + alert 参数缓存彩云原始响应。`--no-cache` 强制刷新。缓存目录默认 `~/.cache/caiyun-weather`，可用 `WEATHER_CACHE_DIR` 自定义。
- 日志：未配置 `WEATHER_LOG_PATH` 时不写日志；配置后追加 JSONL，每条记录 `action/format/location/lng/lat/cache_used/mock/save_path/ts`。

## 跨 AI 工具集成 Quick Start

本 Skill 不依赖任何特定 AI 框架。下面给最小集成示例，环境变量由各自工具自行注入。

### 1. 通用 Skill 触发

支持 `@skill:caiyun-weather` 这类入口的 AI 工具直接触发；生成完整天气播报的标准流程是：**先拿 `--format bundle`→使用 `bundle.brief` 做判断、`bundle.json` 填表→LLM 按骨架渲染 Markdown→需要落盘时把 Markdown 通过管道传给 `--save`**。

```bash
# 1）拿同源事实层 + 决策层
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --format bundle
# 2）LLM 自行渲染完整播报后，需要落盘时：
echo "$rendered_md" | python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --save
```

### 2. OpenClaw / Workflow runner

读取 `${SKILL_DIR}/manifest.json` 注册 entrypoints，常用三条命令：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
python3 ${SKILL_DIR}/scripts/weather_data.py --address "{{address}}" --format bundle
python3 ${SKILL_DIR}/scripts/geocode.py geo --address "{{address}}"
```

### 3. ClaudeDesktop / MCP / Cursor

按“可执行 CLI 工具”集成：把 `weather_data.py --format short` 封装成 MCP tool，描述用 `manifest.json` 里的 summary。无需本地 Python 包安装。

### 4. 裸 shell / Cron / IM bot

```bash
export CAIYUN_TOKEN=...
export AMAP_KEY=...
export WEATHER_DEFAULT_CITY=杭州

python3 ${SKILL_DIR}/scripts/weather_data.py \
--address "杭州市西湖区西湖风景名胜区" \
  --format short
```

发送到 IM/聊天机器人时把 `--format short` 输出直接 POST 给消息接口即可。


## 天气数据结构

顶层 JSON：

| 字段 | 类型 | 说明 |
|---|---|---|
| `location` | string | 本次查询位置名，来自用户、默认环境变量或高德结果 |
| `lng` / `lat` | number | 本次用于天气查询的经纬度 |
| `geocode` | object\|null | 高德地理编码/逆编码元数据（含 `quality`） |
| `provider` | object | 数据源元数据，如天气 provider、地理编码 provider、attribution；footer 从这里生成 |
| `units` | object | 常用单位声明，如温度、风速、降雨、气压、湿度、降水概率 |
| `timezone` | object | 本地时区信息，含 `offset_hours` 和 `name` |
| `output_dir` | string | `--save` 默认写入目录（来自 `WEATHER_OUTPUT_DIR` 或默认值） |
| `date` | string | 日期 `YYYY-MM-DD` |
| `date_cn` | string | 中文日期 `M月D日` |
| `weekday` | string | 周X |
| `generated` | string | 生成时间 `YYYY-MM-DD HH:MM` |
| `alerts` | array | 预警列表 |
| `realtime` | object | 实时天气（含日出日落、is_daytime、life_index、visibility_level、**precip_nearest 附近雨带**） |
| `today` | object | 今日概况（含白天/夜间分时段 skycon 与雨强 max/avg） |
| `minutely` | object\|null | 未来 2 小时逐分钟降雨摘要；**仅付费 token 可用**，免费 token 时 `available=false` |
| `keypoint` | string\|null | 彩云 forecast_keypoint，仅短时参考 |
| `keypoint_hourly` | string\|null | 彩云 hourly.description：高密度中文描述（例如「小雨，今天下午16点钟后雨停，转阴」），**免费 token 也可用**；minutely 不可用时优先引用 |
| `hourly` | object | 未来 24h 关键时段与降雨/异常标记 |
| `days` | array | N 天（默认 7，可用 `--days` 或 `WEATHER_DAILY_STEPS` 调到 1–15）每日概况，含 `sunrise` / `sunset` |

### realtime

```js
{
  skycon,
  temp,        // ℃
  app_temp,    // ℃
  humidity,    // 0-1
  humidity_pct,// 0-100，可直接输出 %
  wind_dir, wind_spd,
  precip,      // mm/h
  visibility,  // km
  visibility_level,  // 极差/差/一般/良好/极佳
  pressure,    // Pa
  pressure_hpa,// hPa，1 位小数
  aqi, pm25,
  uv, uv_desc,
  comfort,
  precip_local_status,        // realtime.precipitation.local.status：ok / unknown / etc.
  precip_local_datasource,    // local 数据源：radar / observation
  precip_nearest: {           // 最近雨带，**免费 token 也有**
    status,
    distance,                 // km；0 = 脚下就是雨中心
    intensity                 // mm/h
  } | null,
  sunrise_today,  // "HH:MM"
  sunset_today,   // "HH:MM"
  is_daytime,     // bool：当前是否在日出–日落之间
  life_index: {
    ultraviolet: { desc, index },
    comfort:     { desc, index },
    coldRisk:    { desc, index },  // 感冒指数，接口不保证总是返回
    dressing:    { desc, index },  // 穿衣指数，接口不保证总是返回
    carWashing:  { desc, index }   // 洗车指数，接口不保证总是返回
  }
}
```

优先使用 `humidity_pct`、`pressure_hpa`、`uv_desc`；老字段仅作备用，避免“把 0.78 当 78% 写成 78%”这种反复换算错误。

回答「几点日出」「现在是白天吗」直接用 `sunrise_today` / `sunset_today` / `is_daytime`；回答「适合洗车/会感冒/穿什么」直接用 `life_index.{carWashing,coldRisk,dressing}.desc`。真实接口可能只返回部分生活指数；缺项时不要自行推导成确定结论。

### today / days

`precip_prob` 已统一为 `0-100` 整数百分比，输出时直接加 `%`。

`today` 除了 `t_min/t_max/skycon/aqi/precip_prob`，还额外提供：

- `skycon_day` / `skycon_night`：今日白天 08–20 点 / 夜间 20–32 点 的 skycon，可能与全天 `skycon` 不同。「今晚会下雨吗/白天还下吗」优先读这两个字段。
- `precip_max` / `precip_avg`：自然日全天峰值雨强、平均雨强 mm/h，来自 `daily.precipitation`。
- `precip_day_*` / `precip_night_*`：白天 08–20、夜间 20–次日 08 的分时段雨强（prob/max/avg）。夜间段可包含次日清晨。

`days` 默认 7 天，可用 `--days N` 或 `WEATHER_DAILY_STEPS` 调到 1–15 天；每项携带 `date / offset / skycon / t_min / t_max / precip_prob / aqi / uv / sunrise / sunset`。其中 `offset` 为从今日起的偏移（0/1/2/…），便于下游定位「今日/明日/后天」等标签。

### 「现在在下雨吗/雨会变大吗」主信号（realtime.precip + nearest）

脚本不只暴露 `realtime.precip`（脚下网格雨强），还暴露 `realtime.precip_nearest`：

```js
realtime.precip_nearest = {
  status,
  distance,   // km。附近雨带距离脚下的距离；0 = 雨中心就在脚下
  intensity   // mm/h。附近雨带雨强
}
```

brief 里进一步聚合为 `rain_now`（供 LLM 直接读）：

- `is_raining`：脚下是否在下雨（local.intensity > 0.03 mm/h）
- `local_intensity` / `nearest_intensity` / `nearest_distance_km`：原始数值
- `approaching`：脚下不在下 + 5 km 内有雨带 → 雨在靠近
- `intensifying`：脚下在下 + 附近雨强更大 → 雨在变大

**使用原则**：「现在在下雨吗」「附近有雨带吗」「雨会不会变大」这类问题优先读 `rain_now`；不要手动拼 `precip` 和 `nearest`。

### 「还要下多久/几点雨停」高密度描述（keypoint_hourly）

顶层 `keypoint_hourly`（来自彩云 hourly.description）例如：

```text
小雨，今天下午16点钟后雨停，转阴，其后小雨
```

这是彩云官方提供的「未来 24 h 跨度的高密度中文描述」，**免费 token 也能拿到**。`minutely.available=false` 时，「雨会下多久」「几点停」优先引用此字段，不要用 `hourly.precipitation` 逐小时手动拼。

### minutely（逐分钟降雨摘要，仅付费 token）

```js
{
  available,            // bool：true 仅付费 token；false 表示该能力不可用，下面详细字段均为 null
  status,               // "ok" 表示数据可信
  description,          // 彩云原始中文描述，例如「未来两小时内无雨」
  has_rain_in_2h,       // bool|null：未来 120 分钟内是否有雨 (阈值 0.03 mm/h)
  starts_in_minutes,    // int|null：当前不下雨时，几分钟后开始下雨；已在下雨则为 null
  stops_in_minutes,     // int|null：当前在下雨时，还有几分钟雨停；不在下雨为 null
  peak_intensity,       // 未来 2h 内最大 mm/h
  peak_in_minutes       // 峰值出现在第几分钟
}
```

仅当 `minutely.available=true`（付费 token）时可用此字段回答「再过几分钟下雨」「雨会下多久」。`available=false`（**免费 token 的默认状态**）时：

1. 「雨会下多久/几点停」改用 `keypoint_hourly`；
2. 「现在在下雨吗/附近有雨带吗/雨会变大吗」改用 `rain_now`；
3. 「几分钟后下雨」可结合 `rain_now.approaching` 与 `nearest_distance_km` 给出粗略判断，但不要虚拟出具体分钟数；
4. 几个渠道都不够准时如实告知「当前 token 不支持逐分钟预报」。

### hourly

```js
{
  morning_rush: { hour, datetime, day_offset, skycon, temp, wind_spd, wind_dir, precip, precip_prob, precip_prob_pct },
  noon:         { ... },
  evening_rush: { ... },
  night:        { ... },
  day_rain, night_rain,
  today_day_rain, next_early_morning_rain,
  abnormal: [
    { type: "rain", hour, datetime, day_offset, precip, duration_hours, end_hour, end_datetime, end_day_offset },
    { type: "wind", hour, datetime, day_offset, wind_spd }
  ]
}
```

字段语义：

| 字段 | 说明 |
|---|---|
| `datetime` | 本地时间 `YYYY-MM-DD HH:00` |
| `day_offset` | `0` 今日，`1` 明日，`2` 后天 |
| `precip_prob` | 逐小时降水概率，保留 0–1 原值 |
| `precip_prob_pct` | 逐小时降水概率，0–100 整数，推荐直接输出 |
| `day_rain` | 未来 24h 内任意 06–23 点有雨 |
| `night_rain` | 未来 24h 内任意 00–05 点有雨 |
| `today_day_rain` | 今日 06–23 点有雨，适合判断“今天出门带伞” |
| `next_early_morning_rain` | 明日 00–05 点有雨，适合判断“明早备伞” |
| `abnormal` | 四个固定时段之外的降雨或大风，最多 2 条 |

## 转换表

### 天气代码 → 中文 + emoji

| 代码 | 中文 | emoji |
|---|---|---|
| CLEAR_DAY | 晴 | ☀️ |
| CLEAR_NIGHT | 晴 | 🌙 |
| PARTLY_CLOUDY_DAY | 多云 | ⛅ |
| PARTLY_CLOUDY_NIGHT | 多云 | 🌥️ |
| CLOUDY | 阴 | ☁️ |
| LIGHT_RAIN | 小雨 | 🌦️ |
| MODERATE_RAIN | 中雨 | 🌧️ |
| HEAVY_RAIN | 大雨 | ⛈️ |
| STORM_RAIN | 暴雨 | ⛈️ |
| RAIN | 雨 | 🌧️ |
| LIGHT_SNOW | 小雪 | 🌨️ |
| MODERATE_SNOW | 中雪 | ❄️ |
| HEAVY_SNOW | 大雪 | ❄️ |
| STORM_SNOW | 暴雪 | ❄️ |
| SLEET | 雨夹雪 | 🌨️ |
| FOG | 雾 | 🌫️ |
| WIND | 大风 | 💨 |
| HAZE | 霾 | 😷 |
| DUST | 沙尘 | 🌪️ |
| SAND | 扬沙 | 🌪️ |

未知代码按 `CLOUDY` 处理。

### 风向（角度）→ 中文

取最后一个 `>= 阈值` 的匹配：

| 阈值 | 方向 |
|---|---|
| 0 | 北 |
| 22.5 | 东北 |
| 67.5 | 东 |
| 112.5 | 东南 |
| 157.5 | 南 |
| 202.5 | 西南 |
| 247.5 | 西 |
| 292.5 | 西北 |
| 337.5 | 北 |

### 预警级别

`01`=🔵蓝 / `02`=🟡黄 / `03`=🟠橙 / `04`=🔴红。

### 风力判断

严禁写“阵风”，彩云字段是当前或预报风速，不是阵风。

| 风速 m/s | 表述 | 行动建议 |
|---|---|---|
| `<5` | 风小 | 正常出行 |
| `5–8` | 有风 | 正常通勤影响不大 |
| `8–11` | 风感明显 | 骑行注意横风 |
| `11–14` | 风力较大 | 不建议骑行 |
| `>=14` | 强风 | 减少外出 |

### AQI

`<=50` 优；`51–100` 良；`101–150` 轻度污染；`151–200` 中度污染；`>200` 重度污染。

### UV

优先使用 `uv_desc`；无描述时按数值判断：`<=2` 弱，`3–5` 中等，`6–7` 强，`>=8` 很强。

## 回答模板

### 一般天气问答

适用于“今天天气怎么样”“现在热吗”。输出 3–6 行，直接给结论：

```text
{location}今天整体是{天气}，现在{温度}℃，体感{体感}℃。
今天温度大约 {最低}–{最高}℃，{湿度/风/空气质量的关键影响}。
{降雨结论}
出门建议：{穿着/雨具/防晒/通勤中最重要的 1–2 条}。
```

### 降雨/带伞问答

适用于“什么时候下雨”“今天会下雨吗”“要带伞吗”。优先使用 `today_day_rain`、`next_early_morning_rain`、`abnormal` 和四个关键时段：

```text
结论：{今天会/不会/主要在明晨有雨}。
具体看：{列出最可能下雨的时段、降水强度、概率，直接用 precip_prob_pct}。
雨具：{今天日间雨→带伞；仅明晨雨→今晚可不带，明早备；无雨→无需}。
```

注意：`day_offset=1` 且 `hour < 6` 时必须写“明晨 HH:00”，不能写“今晚 HH:00”。

### 完整天气播报（骨架 + 填充规则）

**仅当用户明确要求「播报/报告/日报/富文本通知/定时任务」时才使用。** 完整播报由 LLM 渲染，不再由脚本硬编码。

**渲染流程**：

1. 调用 `weather_data.py --format bundle` 拿到同源的 `json` 完整事实层与 `brief` 决策层；完整播报不要分别请求 `brief` 和 `json`。
2. 按下方「播报骨架」渲染 Markdown；**骨架必须遵守**（章节顺序、emoji 标题、表格列结构、分隔线）；**措辞/排版细节/场景化建议可自由发挥**。
3. 需要落盘时，把渲染好的 Markdown 通过管道传给 `--save`：`echo "$md" | weather_data.py --save`。

**播报骨架**：

```markdown
# 🌦️ {location}天气播报

> 📍 **位置**：{geocode.formatted_address 或 location}
> 🕖 **生成时间**：{generated} · {weekday}
> 🌐 **坐标**：`{lng}, {lat}`

---

> **今日速览**
>
> `{realtime.temp}℃` · {sky.emoji} {sky.label} · {根据 rain/risks 凝一句降雨/天气结论} · 空气{aqi.level} · 今日 {t_min}–{t_max} ℃
> **出门关键词**：{brief.keywords 逗号拼接或直接引用}

---

## ⚪ 天气预警
{遍历 alerts；无则写「暂无预警。」}

---

## 🌤️ 一、当前体感
{一段题记式叙述（天气/气温/体感/湿度综合判断） + 关键 bullet：
- 风、能见度、空气质量、紫外线、舒适度；缺字段直接略过，不要堆砌或失真。}

---

## ⚡ 二、需要特别注意
{直接遍历 `brief.risks[]`（已按 severity 排好序），把 headline 改写成行动建议；注意：
- 不要和下方表格重复堆字段；
- risks 为空时写「⚪ 暂无明显天气风险，按日常节奏出门即可。」}

---

## 🕒 三、未来 24 小时关键时段
{Markdown 表格，列固定为：时间 / 时段 / 天气 / 温度 / 风速 / 降水概率；
从 `hourly` 中 `night / morning_rush / noon / evening_rush` 按时间升序渲染；
`precip_prob_pct ≥ 50%` 时把概率单元格加粗；字段缺失用 — 占位。}

---

## 📆 四、未来 7 天
{Markdown 表格，列固定为：日期 / 天气 / 温度 / 降水概率 / AQI / UV；
遍历 `days` 7 天；前 3 天加「今日/明日/后天」标签，后 4 天只显示日期；
`precip_prob ≥ 50%` 加粗；字段缺失用 — 占位。}

---

## 💡 五、生活建议
{6 条场景化建议，顺序固定：👔 穿着 / 🌂 雨具 / 🚗 通勤 / 🏃 运动 / ☀️ 防晒 / 💧 湿度；
每条要结合今天的具体数据写成「出行助理」口吻，不要写「请注意天气变化」这种泛泛之词。}

---

## 🎯 六、一句话结论
**{行动判断优先，20–50 字，加粗单行。}**

---

> 数据来源：{provider.attribution}
```

**填充规则**（必须遵守）：

- **用 brief.risks[] 作为「二、需要特别注意」的唯一来源**，保证风险不漏、不重复、排序与严重度一致。
- **用 brief.keywords 作为「出门关键词」的主骨架**，可改写措辞但不应新增/删减角度。
- **用 brief.umbrella.code 的枚举值判断雨具话术**（`carry_today` / `carry_tomorrow_morning` / `none`），不要直接照搬 text。
- **用 brief.rain.peak_window**（或 `windows[0]`，已按峰值雨量排好）作为主风险提示；用 `brief.rain.first_window` / `windows_chronological` 组织时间线。
- **时间标签严格按 `day_offset` 写**：`0` →「今天 HH:00」；`1` 且 `hour<6` →「明晨 HH:00」；`1` 且 `hour≥6` →「明天 HH:00」。不写「今晚 03:00」指代明晨。
- **逐小时用 `precip_prob_pct`（整数），日级用 `precip_prob`（整数）**，两者都是 0–100，直接加 `%`；不得把小数概率与百分比混用。
- **须使用 brief 里的已分级枚举**（wind.level / uv.level / aqi.level / humidity.feel / visibility.level），不要自己重新按原始值判断。
- **「逐分钟近期降雨」问题**（「还有几分钟下雨」/「雨会下多久」）优先级：`brief.minutely.available=true` 时使用 `brief.minutely`；否则退一步用 `brief.keypoint_hourly`（「16 点雨停」这种高密度描述）；都拿不到时告知用户「当前 token 不支持逐分钟预报」。不要用 `hourly.precipitation` 手动拼一个假装精确到分钟的答案。
- **「现在在下雨吗/附近有雨带吗/雨会变大吗」使用 `brief.rain_now`**（`is_raining` / `approaching` / `intensifying` / `nearest_distance_km`），不要手动拼 `realtime.precip` 和 `realtime.precip_nearest`。
- **「今晚会下雨吗/白天还下吗」使用 `brief.today_split`**（`day.skycon/precip_max` 与 `night.skycon/precip_max`），不要只看全天 `today.skycon`。
- **「今天自然日会下多大雨」使用 `today.precip_max` / `today_split.full_day_precip_max`**；**「今晚到明早雨多大」使用 `today_split.night.precip_max` 或 `day_night_peak_precip`**。不要把 `20h_32h` 当作自然日全天。
- **「几点雨停/雨会转成什么」优先使用 `brief.keypoint_hourly`**（完整中文跨度描述）。
- **「几点日出/日落」「现在是白天吗」直接用 `brief.daylight` 或 `realtime.sunrise_today/sunset_today/is_daytime`**，不要估算。
- **「适合洗车吗/会感冒吗/穿什么」用 `brief.life_index`**（`carWashing` / `coldRisk` / `dressing` / `ultraviolet` / `comfort`），直接引用已返回项的 `desc`；缺项时说明当前接口未返回，不要从原始气温/湿度再推导。
- **雾天/能见度查询**：`brief.visibility` 或 `realtime.visibility_level`；<1km 主动提醒「驾驶注意」。
- **台风查询**：本 Skill **不提供台风路径预报**。只当 `alerts[]` 里有 `台风` 预警标题时，可以汇报「已有台风 XXX 预警」；其余情况全部走「已知不支持」分支直接告知用户，不要用 `keypoint` 和零星词拼一个看似自信的答案。
- **keypoint 只作短时占位引用**，与长时结论矛盾时优先舍弃 keypoint。
- **严禁写「阵风」**（彩云给的是当前或预报风速、不是阵风）。
- **排版/措辞/场景化细节可自由发挥**：根据用户意图和数据特点调整语气，避免「请注意天气变化」这类模板腔。

**落盘路径**（仅 `--save` 时写入）：

```text
{output_dir}/weather_{YYYYMMDD}_{HHMM}.md
```

其中 `{output_dir}` 来自 JSON 的 `output_dir` 字段（默认 `~/.cache/caiyun-weather/outputs`，可通过 `WEATHER_OUTPUT_DIR` 或 `--save /path/to.md` 覆盖）。默认文件名精确到分钟，适合随时手动触发或自动化多次触发。

## 判断规则

**时间标注**：

- `day_offset=0`：写“今天 HH:00”或直接写“HH:00”。
- `day_offset=1` 且 `hour < 6`：写“明晨 HH:00”。
- `day_offset=1` 且 `hour >= 6`：写“明天 HH:00”。

**降雨表述**：

- `today_day_rain=true`：今天出门按有雨处理，雨具建议“带伞”。
- `today_day_rain=false` 且 `next_early_morning_rain=true`：写“今天白天无明显降雨，明晨可能有雨；今晚可不带伞，明早备伞”。
- `day_rain=false` 且 `night_rain=false`：写“未来 24 小时无明显降雨”。
- 逐小时概率直接读 `precip_prob_pct`；日级 `precip_prob` 已是 0–100，直接加 `%`。

**`keypoint` 使用边界**：仅作短时提示，长时间结论必须以 `hourly`/`days` 字段为准，避免“未来两小时不会下雨”和“今天有雨”矛盾。

**重点提醒必列条件**：

- 有预警。
- 风速 `>=8 m/s`。
- `today_day_rain=true` 或 `next_early_morning_rain=true`。
- `AQI > 100`。
- `UV >= 6`。

## 输出原则

1. 普通问答不默认写文件，除非用户要求保存、日报、归档或定时任务。
2. 完整天气播报由 LLM 基于 `brief` 数据和 SKILL.md 骨架渲染 Markdown；只有在用户明确要求落盘时，才把 LLM 渲染好的 Markdown 通过管道交给 `--save` 写入 `{output_dir}/weather_{YYYYMMDD}_{HHMM}.md`。脚本本身不再渲染完整播报。
3. 所有自然语言回答必须使用 `location` 字段，不硬编码地点。
4. 不输出 API 字段名或天气代码。
5. 不暴露 token、`.env` 内容或完整请求 URL；自检输出的 `CAIYUN_TOKEN` / `AMAP_KEY` 字段已自动掩码，不要再原样回显。
6. 地理编码 `quality` 为 `low`/`unknown` 时，回答里必须显式提示“定位可能偏粗”。

## 异常处理

- 脚本返回 `error`：报告错误并停止。
- 地址查天气但缺高德 Key：提示调用方注入 `AMAP_KEY` / `GAODE_KEY`，或改用 `--lng/--lat`。
- 天气查询缺 `CAIYUN_TOKEN`：提示调用方注入 `CAIYUN_TOKEN`。
- 字段为 `null`：跳过该项或写“暂无数据”。
- 数据冲突时，以布尔风险字段优先：降雨看 `today_day_rain` / `next_early_morning_rain`，风看 `wind_spd`。

## 生成前自检

输出前检查：

1. 是否选择了最小必要动作：地理编码、天气问答、降雨判断或完整播报。
2. 地点是否来自用户输入、环境变量或脚本返回的 `location`，没有硬编码。
3. 没有输出字段名、天气代码、token 或完整 API URL。
4. "今天/明晨/明天"的时间标签与 `day_offset` 一致。
5. 雨具建议与 `today_day_rain`、`next_early_morning_rain` 一致。
6. 逐小时用 `precip_prob_pct`，日级用 `precip_prob`，没把两种单位弄反。
7. 没使用"阵风"。
8. `quality=low`/`unknown` 时已提示定位偏粗。
9. 普通查询没有不必要地写文件；完整播报仅在用户要求落盘时才写文件。
10. 「再过几分钟下雨」「雨会下多久」类查询：先看 `minutely.available`，=true 时读 minutely；=false 时退为 `keypoint_hourly`；都不够时如实告知「不支持逐分钟预报」，不用 `hourly` 凑数。
11. 「现在在下雨吗/附近有雨带吗」使用了 `rain_now`，没有只依靠 `realtime.skycon` 猜。
12. 「今晚会下雨吗/白天还下吗」使用了 `today_split`，没有只看全天 skycon。
13. 「今天会下多大雨」提及了 `precip_max` 的 mm/h 数据，而不是只说概率。
14. 「几点日出/日落」「现在是白天吗」直接读 `sunrise_today/sunset_today/is_daytime`，没有估算。
15. 台风路径/到达时间类问题已走「已知不支持」分支告知用户，没有用 `keypoint` 凑答案。
