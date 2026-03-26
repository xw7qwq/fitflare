# FitBaus Public API

公开只读 API 基础路径：

```text
/api/public/v1
```

目标：

- 给其他项目复用 Fitbit 本地缓存数据
- 把完整缓存 datasets、仪表盘摘要、趋势序列和 SVG 图表分层暴露
- 保持公开只读，不开放创建、授权、删除等管理操作

## 响应规范

JSON 响应统一包含这些字段：

```json
{
  "api_version": "v1",
  "resource": "series",
  "generated_at": "2026-03-26T08:26:12",
  "profile_id": "Lucius7",
  "data": {},
  "meta": {}
}
```

错误响应格式：

```json
{
  "api_version": "v1",
  "error": {
    "code": "profile_not_found",
    "message": "Profile \"foo\" not found"
  }
}
```

## 文档入口

- `GET /api/public/v1`
- `GET /api/public/v1/docs`
- `GET /api/public/v1/docs.md`
- `GET /api/public/v1/openapi.json`

## 档案与仪表盘

- `GET /api/public/v1/profiles`
  返回公开档案列表和每个档案的快捷链接。

- `GET /api/public/v1/profiles/<profile_id>`
  返回单个档案的概要信息、覆盖范围和可调用链接。

- `GET /api/public/v1/profiles/<profile_id>/dashboard`
  返回完整公开仪表盘缓存。

- `GET /api/public/v1/profiles/<profile_id>/overview`
- `GET /api/public/v1/profiles/<profile_id>/coverage`
- `GET /api/public/v1/profiles/<profile_id>/metrics`
- `GET /api/public/v1/profiles/<profile_id>/metrics/<metric_key>`
- `GET /api/public/v1/profiles/<profile_id>/correlations`
- `GET /api/public/v1/profiles/<profile_id>/snapshot-status`

## 完整数据集

完整数据集接口用于服务间集成，适合直接拉取本地缓存中的结构化数据。

- `GET /api/public/v1/profiles/<profile_id>/datasets`
  返回所有可用 dataset 列表。

- `GET /api/public/v1/profiles/<profile_id>/datasets/<dataset>?offset=0&limit=200`

支持的 `dataset`：

- `activity`
- `sleep`
- `hrv`
- `rhr`
- `daily`
- `weekly`
- `monthly`

说明：

- `activity` / `sleep` / `hrv` / `rhr` 是从本地 CSV 解析出的完整缓存
- `daily` / `weekly` / `monthly` 是服务端聚合后的趋势数据
- 默认 `limit=200`
- 支持 `offset` 分页

## 趋势序列

趋势接口适合前端图表、可视化服务、移动端卡片等轻量消费场景。

- `GET /api/public/v1/profiles/<profile_id>/series/<granularity>?metrics=sleep_score,steps,hrv&limit=30`

支持的 `granularity`：

- `daily`
- `weekly`
- `monthly`

常用 `metrics`：

- `sleep_score`
- `sleep_hours`
- `steps`
- `active_minutes`
- `active_zone_minutes`
- `hrv`
- `rhr`
- `calories_out`
- `minutes_deep`
- `minutes_rem`
- `deep_pct`
- `rem_pct`

说明：

- 不传 `metrics` 时默认返回该粒度下所有可用数值指标
- `limit` 表示取最后 N 个点
- 返回体积明显小于完整 dashboard

## 摘要卡片与表格

- `GET /api/public/v1/profiles/<profile_id>/sections`
- `GET /api/public/v1/profiles/<profile_id>/sections/<section_key>`

支持的 `section_key`：

- `body`
- `vitals`
- `lifestyle`
- `account`

- `GET /api/public/v1/profiles/<profile_id>/tables`
- `GET /api/public/v1/profiles/<profile_id>/tables/<table_key>?offset=0&limit=100`

支持的 `table_key`：

- `sleep`
- `activity`
- `recovery`
- `body`
- `vitals`
- `foods`
- `devices`
- `badges`
- `alarms`
- `endpoints`

## Fitbit 快照缓存

- `GET /api/public/v1/profiles/<profile_id>/snapshot`
  返回已净化的 Fitbit 快照缓存，移除了 token 类元数据。

- `GET /api/public/v1/profiles/<profile_id>/snapshot/endpoints`
  返回快照端点索引。

- `GET /api/public/v1/profiles/<profile_id>/snapshot/endpoints/<endpoint_key>`
  返回某个 Fitbit 快照端点的完整缓存结果。

适合这些场景：

- 其他项目要直接复用 Fitbit profile / devices / goals / foods / water / vitals 数据
- 不想重新接 Fitbit OAuth，只想消费本地已缓存结果

## SVG 图表接口

SVG 图表接口返回 `image/svg+xml`，适合：

- `<img src="...">`
- `<object data="...">`
- 服务端拼装报告
- 其他前端项目直接嵌入

预置图：

- `GET /api/public/v1/profiles/<profile_id>/charts/overview-trend.svg`
- `GET /api/public/v1/profiles/<profile_id>/charts/weekly-trend.svg`
- `GET /api/public/v1/profiles/<profile_id>/charts/sleep-trend.svg`
- `GET /api/public/v1/profiles/<profile_id>/charts/activity-trend.svg`

自定义图：

- `GET /api/public/v1/profiles/<profile_id>/charts/series.svg?granularity=daily&metrics=sleep_score,hrv,rhr&limit=30&width=960&height=320&theme=light`

支持参数：

- `granularity=daily|weekly|monthly`
- `metrics=<comma-separated>`
- `limit=<int>`
- `width=<int>`
- `height=<int>`
- `theme=light|transparent`

说明：

- 多指标 SVG 会对每个指标单独归一化，以便在一张图上比较趋势形状
- 如果需要绝对值，请使用 `/series/<granularity>` JSON 接口

## 调用示例

读取公开档案列表：

```bash
curl https://fitbit.lucius7.dev/api/public/v1/profiles
```

读取完整 activity dataset：

```bash
curl "https://fitbit.lucius7.dev/api/public/v1/profiles/Lucius7/datasets/activity?limit=120"
```

读取最近 30 天睡眠/步数/HRV 趋势：

```bash
curl "https://fitbit.lucius7.dev/api/public/v1/profiles/Lucius7/series/daily?metrics=sleep_score,steps,hrv&limit=30"
```

直接嵌入综合趋势 SVG：

```html
<img src="https://fitbit.lucius7.dev/api/public/v1/profiles/Lucius7/charts/overview-trend.svg" alt="综合趋势">
```

## 公开与安全边界

- 所有 `public/v1` 接口都只读
- 创建档案、授权 Fitbit、删除档案、手动同步等接口仍然需要管理员登录
- 快照接口已剔除 token 类元数据，不公开访问令牌和刷新令牌
