# Fitflare

[![Release checks](https://github.com/xw7qwq/fitflare/actions/workflows/check.yml/badge.svg)](https://github.com/xw7qwq/fitflare/actions/workflows/check.yml)

一个给自己使用的健康数据仪表盘：通过 Google Health API 同步自己的数据，保留 Fitbit 历史记录，在中文界面查看睡眠、活动、HRV、静息心率和长期趋势。网站公开只读，无需登录；授权和同步通过服务器命令行维护。

## 一个账户，一份记录

- 六个视图：总览、睡眠、活动、恢复、体征与营养、设备与数据。
- 总览给出恢复估算、本期要点、带迷你趋势的核心指标、近 7 天对比和月度趋势。
- 睡眠结构与作息、活动强度与一周规律、步数日历、HRV / 静息心率个人基线等图表；未佩戴设备的日子留空并不计入均值。
- 定时自动同步；连接、重新授权和手动同步通过服务器命令行完成。
- 任何人可查看健康记录；网站没有登录、管理面板、账户设置或写入 API。
- `/api/public/v1/me` 提供个人只读 API、SVG 趋势图和 OpenAPI 文档。
- 可选：同步后把指标、趋势和睡眠记录推送到 [lucius7.dev](https://lucius7.dev/) 面板，见 [DEPLOYMENT.md](DEPLOYMENT.md#推送到面板)。

没有档案切换、家庭比较、创建多个档案或网页删除健康记录的功能。后端、定时任务和旧接口也只能访问配置的唯一账户。已有 `profiles/<id>` 目录可以继续使用，不需要重命名或重新导入历史记录。

![Fitflare 个人仪表盘](docs/images/dashboard.png)

截图使用合成测试数据，不包含真实健康记录。

## 开始使用

当前可运行的部署方案是 **Linux + Docker Compose**，使用 Python 3.11、Flask、Gunicorn 和原生 JavaScript。无需数据库或前端打包步骤。

```bash
git clone https://github.com/xw7qwq/fitflare.git
cd fitflare
cp deploy/.env.production.example .env
chmod 600 .env
```

编辑 `.env`，确认 `FITFLARE_DATA_ACCESS=public`：这会公开唯一账户的健康记录。新安装的 `FITFLARE_PROFILE_ID=me` 表示唯一数据目录；从旧部署迁移时改成原目录名。随后：

```bash
sudo install -d -o 10001 -g 10001 -m 700 profiles
docker compose up -d --build
curl -fsS http://127.0.0.1:9000/api/health
```

配置 HTTPS 反向代理后，在服务器命令行连接 Google Health 并首次同步：

```bash
docker exec -it fitflare-app python auth/authorize_google_health.py --configure --no-browser
docker exec fitflare-app python fetch/fetch_all.py
```

生产样例仅监听 `127.0.0.1:9000`，由 HTTPS 反向代理公开站点。应用要求显式设置 `FITFLARE_DATA_ACCESS=public`；旧私有配置或未设置时会拒绝启动，避免升级时意外发布健康记录。日常维护见 [DEPLOYMENT.md](DEPLOYMENT.md#运维命令)。

完整步骤、已有数据迁移、备份和回滚见 [DEPLOYMENT.md](DEPLOYMENT.md)；Google Cloud 配置与权限范围见 [GOOGLE_HEALTH.md](GOOGLE_HEALTH.md)。

## 数据来源

同步使用 Google Health API v4 和六项只读权限（活动、健康指标、睡眠、营养、个人资料、设置）。从 Fitbit 迁移的账户需要用关联原 Fitbit 账户的 Google 账号重新授权；授权前仍可浏览已有记录，迁移不会删除历史 CSV。Google Health 不再提供的数据（终身统计、徽章、闹钟等）保留为历史缓存并在页面标注。

## 能部署到 Cloudflare Workers 免费层吗？

**现有版本不能原样部署；重写同步与存储后，有条件可行。** 推荐方向是 Static Assets + TypeScript Worker + D1，用 Cron 分批增量同步。免费层的 10 ms CPU 预算要求轻量请求和预计算，不能照搬 Pandas 全量分析、持久化文件、子进程和长期后台线程。

[完整可行性研究](docs/cloudflare-workers.md) 包含官方限制、容量估算、Google Health 授权影响及迁移验收标准。这是设计评估，尚未创建或部署 Worker。

## API 与开发

- [API.md](API.md)：单账户只读接口，统一通过 `/me` 访问，无需档案 ID。
- `/api/public/v1/docs`：交互文档；`/api/public/v1/openapi.json`：OpenAPI。
- 文档、数据 API 和 SVG 均可直接访问，不使用登录会话或 Cookie。

可直接调用（把域名换成自己的部署）：

```bash
curl -s https://fitflare.example.com/api/public/v1/me                                  # 账户概览与链接
curl -s 'https://fitflare.example.com/api/public/v1/me/series/daily?metrics=steps,hrv&limit=30'
curl -s 'https://fitflare.example.com/api/public/v1/me/tables/activity_logs?limit=10'    # 最近运动记录
```

SVG 趋势图可以直接嵌入网页或 README：`<img src="https://fitflare.example.com/api/public/v1/me/charts/overview-trend.svg?theme=light">`。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
npm ci --ignore-scripts
npx playwright install --with-deps chromium
npm run check
```

开发使用 Node.js 24（`.nvmrc`）；完整检查需要 Docker。`npm run check` 检查源码和生成文档、构建镜像、执行单元及桌面/手机浏览器回归；所有测试只用合成数据。快速检查可单独执行 `npm test` 和 `FITFLARE_DATA_ACCESS=public python -m unittest discover -s tests -p 'test_*.py' -v`。

API 目录维护在 `common/api_docs.py`，修改后运行 `python scripts/docs.py`。协作约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 仓库导航

| 路径 | 内容 |
| --- | --- |
| `backend/`、`server.py` | 单账户边界、只读接口与同步调度 |
| `common/` | 数据路径、缓存、Google Health 客户端、指标和 API 定义 |
| `auth/`、`fetch/` | 授权与数据同步；默认只处理配置账户 |
| `index.html`、`app.js`、`js/`、`style.css` | 个人仪表盘 |
| `templates/`、`docs.css` | API 文档界面 |
| `deploy/`、`scripts/` | 配置样例、检查、部署和面板推送工具 |
| `tests/` | 合成数据、访问隔离和浏览器回归 |
| `docs/` | Workers 研究、来源和历史记录 |
| `profiles/` | 本地私有运行数据，不进入 Git 或镜像 |

## 来源与数据

项目基于 [markrai/fitbaus](https://github.com/markrai/fitbaus)，继承自原 `theLucius7/fitbaus` 的服务器部署版本（旧仓库已删除）。作者和提交历史已保留；来源说明见 [docs/provenance.md](docs/provenance.md)。本项目与 Fitbit 或 Google 无隶属关系。

继承源码未提供标准许可证，原说明为 “This project is for personal use.”；本次整理不另行授予整个项目的开源许可证。

已配置账户的仪表盘和数据 API 会公开展示健康记录；真实 `.env`、OAuth 凭据、原始健康数据文件、日志和备份均不进入仓库。安全与私密报告说明见 [SECURITY.md](SECURITY.md)。
