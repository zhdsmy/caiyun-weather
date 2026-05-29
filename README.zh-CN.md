# caiyun-weather

[English Version](README.md) | [中文版本](README.zh-CN.md)

`caiyun-weather` 是一个面向中文用户的天气查询 Skill。它通过脚本提供结构化天气数据、地理编码、风险预分级和轻量问答数据；完整天气播报由 AI 根据 `references/reporting.md` 中的骨架渲染为 Markdown。

## 功能特性

- **天气查询**：支持按地址或经纬度查询实时天气、今日概况、未来小时级和日级预报。
- **地址解析**：支持结构化地址转经纬度，也支持经纬度逆地理编码。
- **决策字段**：提供降雨、雨具、风、紫外线、空气质量、湿度、能见度、生活指数等预分级结果。
- **完整播报**：输出同源 `bundle` 数据（`json` + `brief`），由 AI 按 `references/reporting.md` 骨架生成完整 Markdown 天气播报。
- **离线演示**：内置 `sunny`、`rain`、`alert` mock 场景，便于演示、调试和 CI。
- **安全边界**：脚本不会读取 `.env` 文件，不会输出 token 原值；环境变量由调用方注入。

## 运行要求

- Python 3.10+
- 无第三方 Python 依赖
- 天气查询需要彩云 API token
- 地址查询或地址转坐标需要高德 Web 服务 Key

## 环境变量

| 变量 | 说明 | 必填场景 |
|---|---|---|
| `CAIYUN_TOKEN` | 彩云 API token | 所有真实天气查询 |
| `AMAP_KEY` / `GAODE_KEY` | 高德 Web 服务 Key | 地址查天气、地理编码、逆地理编码 |
| `WEATHER_LNG` / `LONGITUDE` | 默认经度 | 经纬度模式默认地点 |
| `WEATHER_LAT` / `LATITUDE` | 默认纬度 | 经纬度模式默认地点 |
| `WEATHER_ADDRESS` | 默认地址 | 地址模式默认地点 |
| `WEATHER_LOCATION` | 输出展示名 | 可选 |
| `WEATHER_CITY` | 地理编码限定城市 | 可选 |
| `WEATHER_DEFAULT_CITY` / `DEFAULT_CITY` | 默认城市兜底 | 可选 |
| `WEATHER_TZ` | 时区偏移小时，默认 `8` | 可选 |
| `WEATHER_DAILY_STEPS` | 日级预报天数，默认 `7` | 可选 |
| `WEATHER_OUTPUT_DIR` | `--save` 默认输出目录 | 可选 |
| `WEATHER_CACHE_DIR` | 缓存目录 | 可选 |
| `WEATHER_CACHE_SECONDS` | 缓存秒数，默认 `0` | 可选 |
| `WEATHER_LOG_PATH` | JSONL 调用日志路径 | 可选 |

缓存会按经纬度以及请求形态（`hourlysteps`、`dailysteps`、alert 标记）生成 key，避免不同预报天数复用错误缓存。

定位优先级：显式地址或经纬度优先；未传地点时先看 `WEATHER_LNG/WEATHER_LAT`，再看 `WEATHER_ADDRESS`。

## 快速开始

以下示例使用 `${SKILL_DIR}` 表示本 Skill 根目录。

### 配置自检

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --check
```

### 按地址查询天气

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区西湖风景名胜区" --format short
```

### 按经纬度查询天气

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --lng 120.155100 --lat 30.274100 --location 西湖 --format short
```

### 地址转经纬度

```bash
python3 ${SKILL_DIR}/scripts/geocode.py geo --address "杭州市西湖风景名胜区" --city 杭州
```

### 经纬度转地址

```bash
python3 ${SKILL_DIR}/scripts/geocode.py regeo --lng 120.155100 --lat 30.274100 --extensions base
```

## 输出格式

`weather_data.py` 支持以下输出格式：

| 格式 | 说明 | 适合场景 |
|---|---|---|
| `json` | 完整结构化天气数据 | 下游解析、AI 自行组织回答 |
| `brief` | 风险、降雨、雨具、生活建议等决策字段 | 完整 Markdown 播报、移动端卡片 |
| `bundle` | 同一次请求得到 `{json, brief}`：`json` 是规整事实层，`brief` 是派生决策层 | 完整播报、Cron、工作流集成 |
| `short` | 3–6 行中文直接回答 | 即时聊天、IM 推送 |

示例：

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --format bundle
```

Bundle 输出形态：

```json
{
  "schema_version": "6.4.0",
  "json": {"location": "深圳市龙华区民治街道"},
  "brief": {"keywords": ["明早备伞"]}
}
```

## 生成完整 Markdown 播报

完整播报不由脚本硬编码，而是由 AI 根据 `references/reporting.md` 中的「完整天气播报（骨架 + 填充规则）」渲染。

推荐流程：

1. 调用 `weather_data.py --format bundle` 一次获取同源的 `json` 和 `brief`。
2. 使用 `bundle.brief` 做判断，使用 `bundle.json` 填充表格和详细事实。
3. AI 按 `references/reporting.md` 的播报骨架生成 Markdown。
4. 需要落盘时，将最终 Markdown 通过 stdin 传给 `--save`。

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --format bundle
```

保存 AI 渲染后的 Markdown：

```bash
echo "$rendered_md" | python3 ${SKILL_DIR}/scripts/weather_data.py --address "杭州市西湖区" --save
```

注意：`--save` 只负责保存 stdin 中的 Markdown，不负责获取天气 JSON，也不负责渲染播报。

## Mock / 离线演示

```bash
python3 ${SKILL_DIR}/scripts/weather_data.py --mock sunny --format short
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format brief
python3 ${SKILL_DIR}/scripts/weather_data.py --mock alert --format json
python3 ${SKILL_DIR}/scripts/weather_data.py --mock rain --format bundle
```

## 集成方式

- **AI Skill**：加载本目录后，按 `SKILL.md` 的执行策略选择最小必要动作；完整播报使用 `references/reporting.md`。
- **Workflow / Cron**：由运行环境注入环境变量，完整播报调用 `weather_data.py --format bundle`，短消息调用 `--format short`。
- **IM Bot**：短消息可直接发送 `--format short` 输出；富文本播报建议由 AI 渲染 Markdown 后发送。
- **MCP / 工具封装**：可使用 `manifest.json` 中的 entrypoints 注册命令。

## 错误格式

CLI 失败时返回结构化 JSON，并以非 0 状态退出：

```json
{
  "ok": false,
  "error": {
    "code": "missing_env",
    "message": "缺少必填环境变量: CAIYUN_TOKEN",
    "hint": "请通过调用方所在的 AI 工具或 shell 注入相应环境变量后重试。"
  }
}
```

自动化调用方应检查顶层 `error` 字段；一旦存在就停止生成，不要编造天气。

## GitHub Topics

建议仓库 Topics：

```text
weather caiyun-weather weather-api amap geocoding markdown cron ai-agent automation chinese zh-cn
```

## 已知限制

- 不支持历史天气查询。
- 不提供卫星云图、雷达图或任意图像。
- 不提供台风路径预报；只能在接口返回台风预警时汇报已有预警。
- 免费彩云 token 通常不提供逐分钟降雨数据；脚本会退化使用小时级描述和附近雨带信息。
- 高德地址解析主要覆盖中国大陆；海外地点建议直接传经纬度。

## 隐私与安全

- 脚本不会主动读取 `.env` 文件。
- 自检输出会掩码显示 token 和 key。
- 回答和日志中不应包含完整 API URL、token 或密钥原值。
- 本地缓存、输出和日志建议保存在用户目录或受控路径中。

## 许可证

本项目采用个人使用、非商业用途许可证。允许个人学习、研究、测试和自用；禁止商业使用、转售、商业集成或作为付费服务的一部分使用。详见 `LICENSE`。
