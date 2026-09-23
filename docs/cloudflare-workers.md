# Cloudflare Workers 免费层可行性

核查日期：2026-09-24。本文依据当前仓库源码与 Cloudflare、Google 官方文档，属于迁移设计，**尚未在 Workers 上部署或测量 CPU，也未完成 Google Health 授权**。

## 结论

**现有项目不能原样搬到 Workers 免费层；改成单账户、分批同步的 Worker + D1 后，有条件可行。** 日级健康数据的存储和请求量有充足余量，主要约束是每次执行只有 10 ms CPU，以及当前使用的上游 API 即将停用。推荐保留前端，重写同步与持久化层；不建议为“保留 Python”继续搬整套文件、子进程和 Pandas 流程。

**优先处理上游迁移。** Google 官方说明旧 Fitbit Web API 将于 **2026 年 9 月**停用，后续应使用 Google Health API 和 Google OAuth。官方页面没有给出这里已核实的精确停用日，因此不能据此宣称服务器现在已经无法同步。当前仓库仍访问 `api.fitbit.com`；网站可打开、历史图表可展示不代表新增数据持续同步。旧 access/refresh token 不能直接转移，账户本人必须重新授权。[上游停用说明](https://developers.google.com/health/about) · [迁移指南](https://developers.google.com/health/migration)

| 方案 | 判断 | 实际意义 |
| --- | --- | --- |
| 把当前 Docker / Gunicorn 直接发布成 Worker | 不可行 | Worker 不是常驻 Linux 容器 |
| 只把前端放 Static Assets，后端保留服务器 | 可行，但依然依赖服务器 | 改动小，不能算完整迁移 |
| Python Worker + Flask 适配 + D1 + 拆分同步 | 技术上可做，需实测 | 仍需改写最重的持久化和后台执行部分 |
| TypeScript Worker + D1 + Static Assets | 推荐的完整迁移方向 | 减少运行时依赖，适合单账户日级汇总；免费条件须通过后述验收 |

## 当前源码为什么不能原样运行

| 当前实现 | Worker 上的处理 |
| --- | --- |
| `server.py` 的 Flask，`gunicorn.conf.py` 的 gthread 常驻进程 | **Flask 本身已经受支持**，可通过 Cloudflare 的 WSGI 入口适配；Gunicorn 及常驻线程调度不保留 |
| `backend/sync.py` 的线程、`fcntl.flock`、`subprocess.Popen` | Python Workers 不支持这些运行模型，改成 `scheduled` 事件和数据库任务状态 |
| `fetch/fetch_all.py` 顺序启动 Python 子进程 | 改成可独立调用的小批次同步函数 |
| `auth/*.py` 的本地回调服务、浏览器/剪贴板及文件 token | 改成 HTTPS OAuth 回调与持久化加密凭据 |
| CSV、`tokens.json`、`dashboard.json`、文件锁 | Workers 文件系统是临时内存，不跨实例共享；业务数据和状态进入 D1 |
| `fetch/*.py` 的 Pandas 全文件读取、合并、排序、重写 | 迁移成按唯一键 UPSERT，窗口查询交给 SQL，图表由浏览器绘制 |
| 限流后的长时间 `sleep`、内存中的任务状态 | 保存游标、失败原因和 `retry_at`，退出后由下一次调度继续 |

不能笼统说“Workers 不支持 Python/Flask/Pandas”：官方在 2026 年 9 月已支持 Flask WSGI，Python 通过 Pyodide 执行；Pandas 在 Pyodide 有构建，但具体 Cloudflare 版本与依赖仍需验证。即使能够导入，也不证明全历史处理符合免费 CPU 预算。[Flask 支持](https://developers.cloudflare.com/workers/languages/python/packages/flask/) · [Python 包支持](https://developers.cloudflare.com/workers/languages/python/packages/) · [Python 标准库与临时文件系统](https://developers.cloudflare.com/workers/languages/python/stdlib/) · [Pyodide 运行限制](https://pyodide.org/en/stable/usage/faq.html) · [Pyodide 包列表](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)

## 免费层预算

以下是核查日的公开配额；额度以账户为单位与其他项目共享，不能视为本项目独占。

| 项目 | 免费额度/约束 | 对 Fitflare 的影响 |
| --- | --- | --- |
| Worker 请求 | 100,000 次/日 | 单人使用通常远低于上限 |
| Worker CPU | HTTP **10 ms/次**，Cron **也是 10 ms/次** | 最主要瓶颈；不能把付费 Cron 的较长 CPU 额度套用到免费层 |
| 内存 / 外部子请求 | 128 MB；50 次/执行 | JSON 大响应和一次拉取几十个端点都需要拆分 |
| Cron | 账户最多 5 个；按 UTC 执行 | 一个调度器即可；业务日期仍按用户时区计算 |
| Static Assets | 直接命中资源免费、不限请求；免费版 20,000 个文件，每个 25 MiB | 静态 HTML/CSS/JS/Chart.js 很合适 |
| D1 读写 | 每日 5,000,000 行读、100,000 行写 | 索引修改也会增加写入计数；避免每次刷新全表扫描 |
| D1 容量 | **单库 500 MB**；账户合计 5 GB | 单账户用一个数据库；不要把合计额度误当单库额度 |
| D1 每次执行 | 最多 50 次查询；每条语句 100 个绑定参数 | 批量写入仍要限制语句和参数数目 |
| KV（可选） | 每日 100,000 次读、1,000 次写；1 GB | 只作为可丢弃的缓存，不作为同步锁或 token 的事实来源 |

网络等待不计入 Worker CPU，但解析响应、转换数据、加解密、序列化会消耗 CPU。静态请求若设置成先运行 Worker，仍消耗 Worker 请求额度；公开前端壳可以直接走 Assets，健康数据必须走受保护的 API。[Workers 限制](https://developers.cloudflare.com/workers/platform/limits/) · [Cron 文档](https://developers.cloudflare.com/workers/configuration/cron-triggers/) · [静态资源计费](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/) · [D1 价格](https://developers.cloudflare.com/d1/platform/pricing/) · [D1 限制](https://developers.cloudflare.com/d1/platform/limits/) · [KV 价格](https://developers.cloudflare.com/kv/platform/pricing/)

### 数据量估算

这是容量模型，**没有读取或复制线上健康数据，不能当作实际账户统计**。

- 当前四个主数据集是活动、睡眠、HRV、静息心率。按每项每日一条、十年计算，约 `4 × 365.25 × 10 = 14,610` 条；睡眠小睡等会增加条数。
- 若每条连同必要字段按 1 KB 估算，原始行约 15 MB，加入索引、快照和任务记录后仍有望显著低于单库 500 MB。真正容量应以迁移后数据库统计为准。
- 设计目标：每轮仅重取最近 7–14 天有变化的数据，去重后只写变化行；每天新增及修正控制在 1,000 行量级，旧任务日志按保留期清理。
- 不要把这个估算用于分钟级心率：一项每分钟一条，一年就是 525,600 条；按每条 0.5 KB，未算索引已约 263 MB。无限期保存多种原始日内数据需要单独评估和归档。

首次导入应从已有 CSV 离线转换成 SQLite/SQL，再分批导入 D1，校验行数、日期范围和校验和。每日写入预算还包括索引；不要为了导入方便一次耗尽全账户配额。可先迁移近一年，余下历史按日分批补齐。

## 建议的单账户架构

```text
浏览器 ── Static Assets（界面与图表代码）
   └── 受保护的 /api/* ── Worker ── D1（日级记录、快照、任务、加密 token）
Google Health ── /webhooks/health ── 验证来源后写入任务表
Cron ── 每次取一个到期任务 ── 小批量调用 Google Health REST ── 更新游标
```

建议只有一个所有者，不建立多档案、多租户、档案选择和批量授权功能。内部保留固定账户记录即可。

D1 可包含：

- `daily_metrics`：日期为唯一键，保存每日活动、HRV、RHR 等标准化字段。
- `sleep_logs`：以来源记录 ID 为唯一键，保留跨日睡眠和小睡，不能简单按日期覆盖。
- `snapshots`：资料、目标、设备等较小快照，携带源数据时间和同步状态。
- `oauth_credentials`：加密 token、到期时间、权限、版本号。
- `sync_jobs`：数据类型、时间窗口、页游标、租约、重试时间、错误摘要。
- `sync_state`：各数据类型最后成功覆盖区间；空数据、权限缺失和失败分别记录。

原来的日/周/月聚合用索引 SQL 查询或小型预计算表；较大统计在浏览器按分页数据计算。导出也分页或流式生成，不能每次把所有历史 CSV 读进内存。KV 是最终一致的，跨位置读取可能在 60 秒以上仍看到旧值，不能用它串行化刷新 token；需要原子条件更新的 D1 租约与版本检查，或另行验证 Durable Object 方案。[KV 一致性](https://developers.cloudflare.com/kv/concepts/how-kv-works/)

### 定时与批量同步

源码中的资料快照包含 **30 个上游端点**，活动同步另外包含 **8 个序列端点及最多 14 个日摘要**；再算睡眠、HRV、RHR、刷新 token、失败重试，一次完整同步可能超过免费 Worker 的子请求上限。不能把现有“完整同步”直接塞进一个 Cron。

推荐一个每 5 分钟执行的 Cron：每天 288 次唤醒，没有任务时只检查并返回。同步拆成单个数据类型的一个小时间窗/一页，先从每次 1–2 个上游请求起步，按实测响应大小调整；超过预算则存游标，下一次继续。Cron 本身使用 UTC，但日期切片应根据账户时区生成，尤其不能错误归属跨午夜睡眠。

- 优先使用 Google Health webhook 生成变更任务；接收端校验预共享认证值、限制请求体、持久化并去重后迅速响应，不能收到通知就现场跑全历史。
- 首次历史回填与日常同步分开；已有 CSV 可减少上游回填成本。数据写入成功后才能推进游标。
- 429/5xx 保存退避时间，结合服务端重试提示；401 按需刷新一次后重试，权限撤回或刷新失败标记需要重新连接。
- 手动同步与定时同步使用同一任务入口和租约，避免同时轮换 token。租约到期后接管必须校验版本，不能只靠实例内全局变量。
- 任务需要幂等；小窗口重取用于处理迟到数据、修改或删除，不能只用“最大日期 + 1 天”认定历史永远不会变。
- 若 5 分钟推进一个批次太慢，可在实测后提高 Cron 频率；不要靠递归自调用绕过运行预算。

Google Health 支持 REST，因此不用把完整 gRPC 客户端搬入 Worker。官方建议 webhook，提供自动订阅和接收端认证机制。[REST 支持](https://developers.google.com/health/get-started) · [Webhook 文档](https://developers.google.com/health/webhooks)

## OAuth 与上游接口的迁移边界

旧 Fitbit 接口原有每用户每小时 150 请求限制，响应限流头也可能滞后；现有脚本因此会等待到下个窗口。这只用于理解旧代码，**不是新 Google Health 的额度**。[Fitbit 开发者关于旧限流的说明](https://community.fitbit.com/t5/Web-API-Development/How-is-fitbit-web-api-hourly-rate-calculated/td-p/3868729) · [旧接口限流头说明](https://dev.fitbit.com/build/reference/web-api/intraday/get-activity-intraday-by-date-range/)

Google Health 当前公布的默认用户限制为每分钟 300 请求；未验证应用另有每用户约 2.5 QPS 的说明。个人小批量同步足够低，但仍应以项目实际配额与 429 响应控制。新旧端点结构、单位、时区和数据类型不同，必须建立字段映射并对比数据，不能只把域名替换掉。特别是旧的徽章、目标、饮食、设备、睡眠分数等辅助信息，要逐项确认是否有等价接口；没有对应项时明确显示不可用，保留历史来源。[Google Health 配额](https://developers.google.com/health/rate-limits) · [迁移总览](https://developers.google.com/health/migration)

个人自用需要自行建立 Google Cloud 项目、启用 Google Health、设置 OAuth 回调并授权所需只读 scope。`Testing` 模式 refresh token **7 天失效**，不能当作长期无人值守方案；新 API access token 约 1 小时，应该在实际同步需要时按需刷新。[账户设置及 token 生命周期](https://developers.google.com/health/setup) · [新旧授权差异](https://developers.google.com/health/migration/data-access)

Google 文档提供个人自用验证豁免，Health 设置文档也描述未验证应用的 100 人上限。因此单人自用有可测试路径；这并不等于已核实当前 Google 项目可长期运行。实施前应在账户中验证发布状态、同意屏幕、所需 scope 和超过 7 天后的刷新行为；公开 GitHub 源码不意味着开放公共 OAuth 注册。如果以后向任意用户提供服务，需重新评估正式验证和安全审查。[个人自用例外](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) · [Google Health 上线要求](https://developers.google.com/health/developer-checklist)

建议的凭据处理：

1. OAuth client secret、会话密钥和 token 加密主密钥放 Workers Secrets，不能打包到 Assets、提交 Git 或写入日志。
2. D1 中的 access/refresh token 用 Web Crypto AES-GCM 加密；每次生成独立随机 nonce，保存密钥版本与密文。主密钥与数据库分开存放，备份必须能恢复对应密钥。
3. OAuth `state` 随机、短期、一次性并绑定当前管理会话；支持时使用 PKCE；严格匹配回调 URL，旧 token 无法静默换成 Google token。
4. 新 token 与版本号一致更新；对于 token 交换成功但数据库写失败的情况保留明确恢复流程，避免重试旧的一次性刷新凭据。
5. 所有者登录可以采用 Cloudflare Access，并在 Worker 校验身份令牌；也可沿用独立管理会话，但加密与登录 CPU 均须计入预算。Webhook 只开放独立认证的接收路径。
6. 健康 JSON、导出和 SVG 默认私有，返回恰当的私有/禁止缓存头；静态资源目录绝不放健康数据和 token。

[Workers Secrets](https://developers.cloudflare.com/workers/configuration/secrets/) · [Web Crypto](https://developers.cloudflare.com/workers/runtime-apis/web-crypto/) · [Access 应用令牌](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)

## 迁移工作与验收判据

迁移量属于后端改造，**不是增加一个 wrangler 配置文件**。可分为四个可回退阶段：

1. 在现有服务器上完成 Google Health 授权和字段映射，先证明新的上游能返回所需数据。
2. 从已有单账户数据制作离线迁移器，建立 D1 schema、索引和读接口；对比日期、数量、空值语义与主要指标。
3. 实现 Worker 小批次同步、重试、恢复、OAuth 安全与前端接口适配；用合成数据测试，再在独立测试环境测量。
4. 达到下面条件后再切换域名；保留服务器和离线备份，切换期间只允许一个同步写入方。

**判为免费层可行需要同时满足：**

- Google Health 目标 scope、历史访问和长期刷新实际通过；核心指标的数据对比满足需求。
- 每种 HTTP/Cron 执行在接近最大预期负载下 CPU 都有余量，建议目标 p99 < 8 ms；未出现资源超限。冷启动、token 刷新、最大响应和 CSV 导出分别测量。
- 每次子请求及 D1 查询低于平台限制；任务被打断、重复触发、429 后可继续，且不会丢 token、跳过窗口或重复数据。
- 单库低于 500 MB，并为索引和增长留余量；账户总用量含其他项目也在配额内。
- 未登录不能读取健康数据、触发管理操作或绕过备用域名保护；仓库与部署资源均不含真实凭据和个人数据。
- 初次导入、备份恢复、至少一轮计划同步和人工同步完成实际读回验证。

如果必须保留现有子进程/Pandas 全历史运算，或者最小可用批次仍稳定超过 10 ms CPU，**就不能承诺 Workers 免费层完整承载**。此时继续在服务器运行采集与重计算，或评估付费 Workers；付费也不会自动解决子进程和持久化文件系统的兼容问题。当前仓库仍以服务器部署为实际可用方案，本文没有改变线上部署。
