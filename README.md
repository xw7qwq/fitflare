# fitflare

[![Release checks](https://github.com/xw7qwq/fitflare/actions/workflows/check.yml/badge.svg)](https://github.com/xw7qwq/fitflare/actions/workflows/check.yml)

基于 FitBaus 的 Fitbit 自托管数据仪表盘。通过 Fitbit OAuth 授权同步数据，在自己的服务器上保存档案、CSV 与缓存，再用中文界面查看睡眠、活动、恢复和其他健康记录。

本仓库整理自服务器部署的 [theLucius7/fitbaus](https://github.com/theLucius7/fitbaus)，维护地址为 [xw7qwq/fitflare](https://github.com/xw7qwq/fitflare)。为兼容现有部署，界面仍使用 **FitBaus** 名称，环境变量仍为 `FITBAUS_*`，Compose 服务和容器仍叫 `fitbaus` / `fitbaus-app`。来源与整理范围见 [docs/provenance.md](docs/provenance.md)。本项目与 Fitbit 或 Google 无隶属关系。

## 功能

- 中文仪表盘：总览、睡眠、活动、恢复、体征、生活、账户与多档案视图。
- 图表与记录：近期趋势、睡眠阶段、HRV、静息心率、分区数据表及相关性参考；可用内容取决于设备、授权范围和 Fitbit 返回的数据。
- 档案隔离：每个档案独立保存 OAuth 凭据、令牌、原始数据和派生缓存。
- 同步管理：手动同步、可选定时同步、任务进度及档案级文件锁。
- 访问控制：管理员登录、会话与 CSRF 校验、私有数据模式，以及公开模式下的档案允许列表。
- 只读 API：版本化 JSON 接口、SVG 趋势图、交互文档和 OpenAPI 描述。

后端使用 Python 3.11、Flask 和 Gunicorn；前端使用原生 JavaScript 与仓库内的 Chart.js。数据保存在文件系统中，无需额外数据库。

## Docker 安装

完整的配置、HTTPS、OAuth、更新与备份步骤见 [生产部署指南](DEPLOYMENT.md)。以下命令适用于 Linux 主机，需要 Docker Engine 和 Compose 插件。

```bash
git clone https://github.com/xw7qwq/fitflare.git
cd fitflare
cp deploy/.env.production.example .env
chmod 600 .env
```

先编辑 `.env`，生成并填写管理员密码与会话密钥，再准备数据目录。样例使用 UID/GID `10001`、私有模式、关闭自动同步及仅本机监听；未填写管理员密码时，私有模式会拒绝启动。

```bash
sudo install -d -o 10001 -g 10001 -m 700 profiles
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
```

应用默认绑定宿主机 `127.0.0.1:9000`，通过 Caddy 提供 HTTPS 后再登录。生产样例启用了 Secure Cookie；仅在本机 HTTP 开发时将 `FITBAUS_SESSION_COOKIE_SECURE=false`。授权和首次同步方法见 [DEPLOYMENT.md](DEPLOYMENT.md#fitbit-授权与首次同步)。

## API

- [API.md](API.md)：接口、分页、访问模式与响应格式。
- 部署后的 `/api/public/v1/docs`：浏览器交互文档。
- 部署后的 `/api/public/v1/openapi.json`：OpenAPI 描述。

API 读取已同步的本地数据；缺失或过期的派生缓存可能在读取时重建。私有模式的数据接口需要管理员会话，文档页仍可访问。公开模式只对外提供允许公开的档案，公开数据不会自动匿名化。

## 目录

| 路径 | 用途 |
| --- | --- |
| `server.py`、`backend/` | Flask 入口、路由、访问控制与同步调度 |
| `common/` | 档案路径、缓存、Fitbit 权限和 API 定义 |
| `auth/`、`fetch/` | Fitbit 授权、令牌刷新与数据抓取 |
| `index.html`、`app.js`、`js/`、`style.css` | 当前仪表盘界面 |
| `templates/`、`docs.css` | 浏览器 API 文档 |
| `assets/`、`vendor/` | 图片与前端依赖 |
| `deploy/` | 生产环境和 Caddy 配置样例 |
| `scripts/` | 文件审计、文档生成、完整检查与部署脚本 |
| `tests/` | 单元测试、浏览器测试和合成数据夹具 |
| `docs/history/` | 有日期的历史改造记录，不能代替当前验证 |
| `profiles/` | 运行时数据目录，由部署时创建，禁止提交到 Git |

## 开发与检查

使用 Python 3.11 和 Node.js 24；完整检查还需要 Docker 与 Chromium。Python 运行依赖固定在 `requirements.lock`。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
npm ci --ignore-scripts
npx playwright install chromium

npm test
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/docs.py --check
python scripts/audit.py
```

Linux 若缺少浏览器系统依赖，可运行 `npx playwright install --with-deps chromium`。完整发布检查为：

```bash
npm run check
```

该命令检查源文件和文档、运行 JavaScript 与 Python 测试、构建镜像，并以合成档案验证公开/私有模式及浏览器页面。检查结果写入已忽略的 `test-results/`，不会读取生产档案。已有部署的升级使用 `bash scripts/deploy.sh`，流程与回滚限制见 [部署指南](DEPLOYMENT.md#更新与回滚)。

API 文档由 `common/api_docs.py` 生成；修改接口说明后运行 `python scripts/docs.py`，不要单独修改生成的 `API.md`。更多约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据、来源与许可

`profiles/` 包含健康记录、账户信息和 OAuth 凭据；`.env`、备份和测试产物也不应进入仓库。请按 [部署指南](DEPLOYMENT.md#备份与恢复) 将备份保存在仓库外，并参考 [SECURITY.md](SECURITY.md)。

继承源码没有提供标准 `LICENSE` 文件；原 README 的说明为 “This project is for personal use.”。这里保留该说明，不另行授予开源许可证。来源和第三方组件说明见 [docs/provenance.md](docs/provenance.md)。
