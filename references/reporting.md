# Reporting and Answer Guidance

完整天气播报由 LLM 渲染，脚本只提供结构化数据。普通问答优先直接回答，不默认写文件。

## 目录

- [轻量回答模板](#轻量回答模板)
- [完整播报流程](#完整播报流程)
- [播报骨架](#播报骨架)
- [填充规则](#填充规则)
- [字段速查](#字段速查)
- [落盘](#落盘)

## 轻量回答模板

一般天气问答，输出 3-6 行，直接给结论：

```text
{location}今天整体是{天气}，现在{温度}℃，体感{体感}℃。
今天温度大约 {最低}-{最高}℃，{湿度/风/空气质量的关键影响}。
{降雨结论}
出门建议：{穿着/雨具/防晒/通勤中最重要的 1-2 条}。
```

降雨/带伞问答：

```text
结论：{今天会/不会/主要在明晨有雨}。
具体看：{列出最可能下雨的时段、降水强度、概率，直接用 precip_prob_pct}。
雨具：{今天日间雨->带伞；仅明晨雨->今晚可不带，明早备；无雨->无需}。
```

注意：`day_offset=1` 且 `hour < 6` 时必须写“明晨 HH:00”，不能写“今晚 HH:00”。

## 完整播报流程

仅当用户明确要求“播报 / 报告 / 日报 / 富文本通知 / 定时任务”时使用。

1. 调用 `weather_data.py --format bundle` 获取同源的 `json` 完整事实层和 `brief` 决策层。
2. 用 `brief` 做判断和风险排序，用 `json` 填充表格、坐标、provider、详细事实。
3. 按下方骨架渲染 Markdown。章节顺序、emoji 标题、表格列结构和分隔线保持稳定；措辞和场景化建议可自由发挥。
4. 需要落盘时，把最终 Markdown 通过 stdin 传给 `--save`。

不要分别请求 `brief` 和 `json` 组合同一份播报。

## 播报骨架

```markdown
# 🌦️ {location}天气播报

> 📍 **位置**：{geocode.formatted_address 或 location}
> 🕖 **生成时间**：{generated} · {weekday}
> 🌐 **坐标**：`{lng}, {lat}`

---

> **今日速览**
>
> `{realtime.temp}℃` · {sky.emoji} {sky.label} · {根据 rain/risks 凝一句降雨/天气结论} · 空气{aqi.level} · 今日 {t_min}-{t_max} ℃
> **出门关键词**：{brief.keywords 逗号拼接或直接引用}

---

## ⚪ 天气预警
{遍历 alerts；无则写「暂无预警。」}

---

## 🌤️ 一、当前体感
{一段题记式叙述（天气/气温/体感/湿度综合判断） + 关键 bullet：
- 风、能见度、空气质量、紫外线、舒适度；缺字段直接略过，不堆砌。}

---

## ⚡ 二、需要特别注意
{直接遍历 brief.risks[]，把 headline 改写成行动建议；
risks 为空时写「⚪ 暂无明显天气风险，按日常节奏出门即可。」}

---

## 🕒 三、未来 24 小时关键时段
{Markdown 表格，列固定为：时间 / 时段 / 天气 / 温度 / 风速 / 降水概率；
从 hourly 的 night / morning_rush / noon / evening_rush 按时间升序渲染；
precip_prob_pct >= 50% 时把概率单元格加粗；字段缺失用 — 占位。}

---

## 📆 四、未来 7 天
{Markdown 表格，列固定为：日期 / 天气 / 温度 / 降水概率 / AQI / UV；
遍历 days；前 3 天加「今日/明日/后天」标签，后续只显示日期；
precip_prob >= 50% 加粗；字段缺失用 — 占位。}

---

## 💡 五、生活建议
{6 条场景化建议，顺序固定：👔 穿着 / 🌂 雨具 / 🚗 通勤 / 🏃 运动 / ☀️ 防晒 / 💧 湿度；
每条结合具体数据写成出行助理口吻，不写「请注意天气变化」。}

---

## 🎯 六、一句话结论
**{行动判断优先，20-50 字，加粗单行。}**

---

> 数据来源：{provider.attribution}
```

## 填充规则

- `brief.risks[]` 是“需要特别注意”的唯一来源，已按严重度排序；不要重复堆字段。
- `brief.keywords` 是“出门关键词”的主骨架，可改写措辞但不要新增或删减角度。
- 雨具话术用 `brief.umbrella.code`：`carry_today`、`carry_tomorrow_morning`、`none`。
- 主降雨风险用 `brief.rain.peak_window` 或 `windows[0]`；时间线用 `first_window` / `windows_chronological`。
- 时间标签按 `day_offset`：`0` 今天，`1` 且 `hour < 6` 明晨，`1` 且 `hour >= 6` 明天。
- 逐小时概率用 `precip_prob_pct`，日级概率用 `precip_prob`，二者都是 0-100。
- 风、紫外线、AQI、湿度、能见度优先用 brief 中的已分级字段，不重新按原始值判断。
- 分钟级降雨：`brief.minutely.available=true` 时用 `brief.minutely`；否则“几点雨停/雨会下多久”用 `brief.keypoint_hourly`；都没有时说明当前 token 不支持逐分钟预报。
- 现在/附近/雨变大：使用 `brief.rain_now`，不要手动拼 `realtime.precip` 和 `realtime.precip_nearest`。
- 今晚/白天是否下雨：使用 `brief.today_split.day/night`。
- 自然日雨强：使用 `today.precip_max` / `today_split.full_day_precip_max`；今晚到明早用 `today_split.night.precip_max` 或 `day_night_peak_precip`。
- 日出日落和是否白天：直接用 `brief.daylight` 或 `realtime.sunrise_today/sunset_today/is_daytime`。
- 洗车、感冒、穿衣、防晒：用 `brief.life_index` 已返回的 `desc`；缺项时说明暂无。
- 能见度：用 `brief.visibility` 或 `realtime.visibility_level`；低于 1 km 主动提醒驾驶风险。
- 台风路径：不支持。只有 `alerts[]` 已有台风预警时汇报现有预警。
- `keypoint` 只作短时占位引用；与长时结论冲突时以 `hourly/days/today_split` 为准。
- 严禁写“阵风”，接口给的是当前或预报风速，不是 gust。

## 字段速查

顶层常用字段：

| 字段 | 用途 |
|---|---|
| `location`、`lng`、`lat`、`geocode` | 位置展示与定位质量 |
| `provider`、`units`、`timezone` | 数据源、单位、时区 |
| `generated`、`date_cn`、`weekday` | 播报时间 |
| `alerts` | 天气预警 |
| `realtime` | 实时天气、日出日落、生活指数、附近雨带 |
| `today` | 今日概况、白天/夜间 skycon 与雨强 |
| `keypoint_hourly` | 彩云小时级中文高密度描述 |
| `minutely` | 逐分钟降雨摘要，仅付费 token 可靠 |
| `hourly` | 未来 24 小时关键时段与异常 |
| `days` | 未来 N 天概况 |

`brief` 常用字段：

| 字段 | 用途 |
|---|---|
| `headline`、`keywords` | 速览和关键词 |
| `risks[]` | 主要风险，已排序 |
| `rain`、`rain_now`、`today_split` | 降雨、当前雨带、白夜分段 |
| `umbrella` | 雨具判断 |
| `wind`、`uv`、`aqi`、`humidity`、`visibility` | 已分级指标 |
| `life_index`、`daylight` | 生活建议和日照 |

完整 schema 以 `schemas/*.json` 为准。

## 落盘

仅 `--save` 时写文件。默认路径：

```text
{output_dir}/weather_{YYYYMMDD}_{HHMM}.md
```

`output_dir` 来自 JSON 的 `output_dir` 字段，默认 `~/.cache/caiyun-weather/outputs`，可通过 `WEATHER_OUTPUT_DIR` 或 `--save /path/to.md` 覆盖。
