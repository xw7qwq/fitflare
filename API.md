# FitBaus Public API

> 此文件由 common/api_docs.py 生成。请修改接口目录后运行 python3 scripts/docs.py。

基础路径：`/api/public/v1`。全部 25 个接口均为 GET。

## 快速开始

先读取可见档案，再将 YOUR_PROFILE 替换为响应中的档案 ID。YOUR_HOST 需替换为你的部署域名。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles'
```

## 访问与隐私

public/v1 只提供 GET 读取，不会主动发起 Fitbit 同步。缺失或过期的派生缓存可能在读取时本地重建。

公开模式只返回部署者允许公开的档案；私有模式的数据接口要求本站管理员会话。文档始终可读，示例中的 YOUR_PROFILE 是占位符，不是真实档案。

私有模式请先在仪表盘登录，再使用同源浏览器请求。这里不提供 API Key 或 Bearer Token，也不收集、保存或展示管理员密码。cURL 与 Python 示例默认不带会话。

匿名公开版 API 允许跨域读取；私有模式和管理员会话响应不开放 CORS。所有 API 响应使用 private, no-store，不应在共享缓存中保存健康数据。

公开响应不包含内部 files 路径及 OAuth 凭据文件，但档案和快照仍可能包含个人资料、设备和饮食记录；这不是匿名化数据。

## 范围与分页

series 与 SVG 的 limit 表示最后 N 个记录点，不等于 N 个日历天；省略时不截断。日、周、月分别由 daily、weekly、monthly 选择。

datasets 从完整本地结构化数据集的 offset 开始取 limit 条，默认 200；tables 从仪表盘内已缓存的表格分页，默认 100，不保证包含完整历史。两者 offset 默认 0，limit 上限 1000。

分页结果位于 data.rows，meta 包含 total、offset、limit、count。下一页使用 offset + count；count 为 0 或 offset + count >= total 时结束。

整数参数的空值或无法解析值回退到默认值，超出边界会被截到允许范围。未知查询参数被忽略；具体边界见接口参数表。

metrics 按逗号分隔，只选择当前数据中存在的数值指标。省略时取全部可用指标；全部名称都不匹配时也回退到全部可用指标，不返回 400。

SVG 对各指标分别归一化，适合比较走势，不适合读取绝对值。需要绝对值请使用 series JSON。

## 响应与错误

成功 JSON 使用 api_version、resource、generated_at、data 包装，profile_id、meta、links 按接口出现。generated_at 是派生缓存生成时间，不保证每个上游数据源同一时刻更新；空值表示缺失，不应当作 0。

业务错误通常包含 error.code 和 error.message；访问控制层也可能返回字符串 error 及顶层 code。调用端应兼容两种格式，并先检查 HTTP 状态和 Content-Type。

401 表示私有数据需要登录；404 表示资源不存在或对当前访问者不可见；服务异常可能返回 500 和文本响应。不要对认证错误无限重试。

SVG 成功返回 image/svg+xml，Markdown 返回 text/markdown，HTML 文档返回 text/html，OpenAPI 返回规范对象，均不使用业务 JSON 包装。

### 成功响应示例（合成数据）

```json
{
  "api_version": "v1",
  "resource": "series",
  "generated_at": "2026-01-02T08:00:00",
  "profile_id": "YOUR_PROFILE",
  "data": {
    "granularity": "daily",
    "dimension": "date",
    "metrics": [
      {
        "key": "steps",
        "label": "步数",
        "unit": "步",
        "tone": "green"
      }
    ],
    "points": [
      {
        "date": "2026-01-01",
        "steps": 5200
      },
      {
        "date": "2026-01-02",
        "steps": null
      }
    ]
  },
  "meta": {
    "count": 2,
    "available_metrics": [
      "steps"
    ]
  }
}
```

## 档案与指标

### API 索引

`GET /api/public/v1`

可见档案数量、资源分类与文档链接。

返回：`application/json`；data 对象。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1'
```

### 可见档案

`GET /api/public/v1/profiles`

返回当前访问者可见的档案摘要及快捷链接。建议从这里开始。

返回：`application/json`；data[]：档案摘要；meta.count：档案数。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles'
```

### 档案概况

`GET /api/public/v1/profiles/{profile_id}`

档案资料、概览、覆盖范围、数据地图、快照状态及链接。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE'
```

### 仪表盘缓存

`GET /api/public/v1/profiles/{profile_id}/dashboard`

完整公开仪表盘对象，包含指标、趋势、分区与表格，不包含 files。数据较多时优先使用细分接口。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/dashboard'
```

### 数据目录

`GET /api/public/v1/profiles/{profile_id}/catalog`

各数据域的覆盖范围、核心指标、来源和状态。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/catalog'
```

### 概览

`GET /api/public/v1/profiles/{profile_id}/overview`

最近记录、快照概况和本地恢复估算。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/overview'
```

### 覆盖范围

`GET /api/public/v1/profiles/{profile_id}/coverage`

各数据集记录数及起止日期。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/coverage'
```

### 指标列表

`GET /api/public/v1/profiles/{profile_id}/metrics`

指标最新值、7/30 天均值及趋势信息。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/metrics'
```

### 单项指标

`GET /api/public/v1/profiles/{profile_id}/metrics/{metric_key}`

按 key 读取一张指标卡；不存在的指标返回 404。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `metric_key` | path | string · 必填 | 从 /metrics 获取指标 key。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/metrics/steps'
```

### 相关性

`GET /api/public/v1/profiles/{profile_id}/correlations`

缓存中的相关系数、强度和重叠点数，仅供描述性参考。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/correlations'
```

## 趋势与图表

### 趋势序列

`GET /api/public/v1/profiles/{profile_id}/series/{granularity}`

从完整本地数据聚合序列，按指标选择列，按 limit 截取最后若干点。

返回：`application/json`；data.points[]：序列点；data.metrics[]：单位等元数据；meta.available_metrics[]：可选指标。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `granularity` | path | string · 必填 · daily / weekly / monthly | 序列聚合粒度。 |
| `metrics` | query | string · 可选 | 逗号分隔的数值指标；未指定或全部不匹配时使用全部可用指标。 |
| `limit` | query | integer · 可选 · 1–1000 | 只取最后 N 个点；省略时不截断，不是日历天数。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/series/daily?metrics=sleep_hours,steps,hrv&limit=30'
```

### SVG 趋势图

`GET /api/public/v1/profiles/{profile_id}/charts/{chart_key}.svg`

返回可嵌入的 SVG；各指标独立归一化。缺少数据时仍返回带空状态文字的 SVG。

返回：`image/svg+xml`；SVG 文本；X-FitBaus-Chart-Meta 响应头包含图表元数据。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `chart_key` | path | string · 必填 · series / overview-trend / weekly-trend / sleep-trend / activity-trend | 预置或自定义趋势图。 |
| `metrics` | query | string · 可选 | 逗号分隔的数值指标；未指定或全部不匹配时使用全部可用指标。 |
| `granularity` | query | string · 可选 · daily / weekly / monthly | 覆盖预置图粒度；省略时使用该预置图粒度。 |
| `limit` | query | integer · 可选 · 1–1000 | 只取最后 N 个点；省略时不截断，不是日历天数。 |
| `width` | query | integer · 可选 · 默认 960 · 360–1920 | SVG 宽度，单位 px。 |
| `height` | query | integer · 可选 · 默认 320 · 220–1080 | SVG 高度，单位 px。 |
| `theme` | query | string · 可选 · 默认 light · light / transparent | light 为浅色；transparent 为透明背景；其他值按浅色呈现。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/charts/series.svg?metrics=sleep_hours,steps,hrv&granularity=daily&limit=30&width=960&height=320&theme=light'
```

## 数据集与表格

### 数据集目录

`GET /api/public/v1/profiles/{profile_id}/datasets`

列出支持的完整结构化数据集和覆盖信息。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/datasets'
```

### 读取数据集

`GET /api/public/v1/profiles/{profile_id}/datasets/{dataset}`

读取由 CSV 解析或聚合的完整历史，并从 offset 起分页。

返回：`application/json`；data.rows[]：当前页；data.columns[]：列名提示；meta：分页信息。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `dataset` | path | string · 必填 · activity / sleep / hrv / rhr / daily / weekly / monthly | 完整本地数据集。 |
| `offset` | query | integer · 可选 · 默认 0 · 0–100000 | 从第 offset 条开始分页，按缓存记录顺序返回。 |
| `limit` | query | integer · 可选 · 默认 200 · 1–1000 | 从 offset 起最多返回的条数，默认 200。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/datasets/activity?offset=0&limit=20'
```

### 摘要分区列表

`GET /api/public/v1/profiles/{profile_id}/sections`

读取 activity、body、vitals、lifestyle、account 摘要。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/sections'
```

### 单个摘要分区

`GET /api/public/v1/profiles/{profile_id}/sections/{section_key}`

返回指定分区的指标摘要。

返回：`application/json`；data.section、data.label、data.summary。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `section_key` | path | string · 必填 · activity / body / vitals / lifestyle / account | 摘要分区。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/sections/activity'
```

### 记录表目录

`GET /api/public/v1/profiles/{profile_id}/tables`

返回仪表盘记录表的 key、条数和链接。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/tables'
```

### 读取记录表

`GET /api/public/v1/profiles/{profile_id}/tables/{table_key}`

对仪表盘缓存中已有的记录分页；缓存可能只保留最近记录，与完整 datasets 不同。

返回：`application/json`；data.rows[]：当前页；meta.total、offset、limit、count：分页信息。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `table_key` | path | string · 必填 · sleep / activity / activity_logs / recovery / body / vitals / foods / devices / badges / alarms / endpoints | 仪表盘缓存中的记录表。 |
| `offset` | query | integer · 可选 · 默认 0 · 0–100000 | 从第 offset 条开始分页，按缓存记录顺序返回。 |
| `limit` | query | integer · 可选 · 默认 100 · 1–1000 | 从 offset 起最多返回的条数，默认 100。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/tables/sleep?offset=0&limit=20'
```

## 快照与接口状态

### 快照状态

`GET /api/public/v1/profiles/{profile_id}/snapshot-status`

权限范围、缓存时间和接口抓取概况。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/snapshot-status'
```

### 完整快照

`GET /api/public/v1/profiles/{profile_id}/snapshot`

返回选定元数据及各端点的原始 data。仍可能包含个人资料，不是匿名化结果。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/snapshot'
```

### 快照端点目录

`GET /api/public/v1/profiles/{profile_id}/snapshot/endpoints`

端点 key、状态、scope、更新时间与链接。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/snapshot/endpoints'
```

### 单个快照端点

`GET /api/public/v1/profiles/{profile_id}/snapshot/endpoints/{endpoint_key}`

指定端点的状态和上游缓存 data；先通过端点目录确认 key。

返回：`application/json`；data 对象。

| 参数 | 位置 | 约束 | 说明 |
| --- | --- | --- | --- |
| `profile_id` | path | string · 必填 | 从 /profiles 获取可见档案 ID。 |
| `endpoint_key` | path | string · 必填 | 从 /snapshot/endpoints 获取已缓存端点 key。 |

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/profiles/YOUR_PROFILE/snapshot/endpoints/profile'
```

## 文档入口

### HTML 文档

`GET /api/public/v1/docs`

当前文档页面；无需数据访问权限。

返回：`text/html`；HTML 页面。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/docs'
```

### Markdown 文档

`GET /api/public/v1/docs.md`

由同一接口目录生成的纯文本版本；无需数据访问权限。

返回：`text/markdown`；Markdown 文本。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/docs.md'
```

### OpenAPI 3.1

`GET /api/public/v1/openapi.json`

完整版本化接口描述，包含参数、响应结构和当前部署的认证模式。

返回：`application/json`；OpenAPI 规范对象，不是业务响应包装。

```sh
curl -fsS 'https://YOUR_HOST/api/public/v1/openapi.json'
```
