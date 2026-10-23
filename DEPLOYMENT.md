# Fitflare 生产部署指南

本文适用于 Linux 上的 Docker Compose 部署。Compose 项目和服务名为 `fitflare`，容器为 `fitflare-app`，配置变量统一使用 `FITFLARE_*`。应用只处理 `FITFLARE_PROFILE_ID` 指定的唯一账户。宿主机只监听 `127.0.0.1:9000`，由反向代理提供 HTTPS。

## 当前部署（lucius7 服务器）

- 正式地址：`https://fitflare.lucius7.dev/`；旧地址 `https://fitbit.lucius7.dev/` 保留路径和查询参数跳转至新地址。
- 部署目录：`/home/lucius7/fitflare`，即本仓库的 `main` 检出；持久数据在其中的 `profiles/`。
- 账户目录：`FITFLARE_PROFILE_ID=Lucius7`；访问模式：公开只读（`FITFLARE_DATA_ACCESS=public`、`FITFLARE_PUBLIC_PROFILES=Lucius7`），任何人可查看健康记录。
- Caddy 主配置：`/home/lucius7/server-ops/caddy/Caddyfile`，用 `caddyctl check` / `caddyctl apply` 检查并原子加载；服务登记在 `/home/lucius7/server-ops/services.json`。
- 网页管理：停用（未设置管理口令，页面不显示登录入口）。同步、授权等操作通过 SSH 执行，见 [运维命令](#运维命令)。
- Google OAuth 应用名称 `Fitflare`，正式版（个人自用）；自动同步每 21600 秒（6 小时）。
- 服务器专用的两个文件不进入 Git（已列入 `.gitignore` 与 `.dockerignore`）：
  - `Dockerfile.host`：基于本机安全修补的运行时基底 `local/cpython-pip-security-20261022:fitflare`，只复制应用代码。不要换回上游原始镜像。
  - `docker-compose.override.yml`：让 Compose 使用 `Dockerfile.host` 构建。

## 1. 准备源码与配置

安装 Docker Engine、Compose 插件和 Git，然后在选定目录执行：

```bash
git clone https://github.com/xw7qwq/fitflare.git
cd fitflare
cp deploy/.env.production.example .env
chmod 600 .env
```

编辑 `.env`。以下是生产样例中的关键设置：

| 变量 | 建议值或用途 |
| --- | --- |
| `FITFLARE_UID` / `FITFLARE_GID` | `10001` / `10001`，与数据目录属主一致 |
| `FITFLARE_BIND_HOST` / `FITFLARE_PORT` | `127.0.0.1` / `9000` |
| `FITFLARE_PROFILE_ID` | 新安装为 `me`；迁移时填原数据目录名，不是网页可切换的账户 |
| `FITFLARE_DATA_ACCESS` | `private`，数据读取需要登录 |
| `FITFLARE_PUBLIC_PROFILES` | 仅公开模式使用，只能填唯一账户 ID；私有模式留空 |
| `FITFLARE_ADMIN_PASSWORD` | 网页管理口令；也支持用 `FITFLARE_ADMIN_PASSWORD_HASH` 提供 Werkzeug 兼容哈希。私有模式必填；公开模式留空则停用网页登录，管理只走命令行 |
| `FITFLARE_SESSION_SECRET` | 独立生成并持久保存的随机密钥，至少 32 字符；也可用 `FITFLARE_SESSION_SECRET_FILE` |
| `FITFLARE_SESSION_COOKIE_SECURE` | `true`，通过 HTTPS 登录 |
| `FITFLARE_AUTO_SYNC_ENABLED` | 首次部署设为 `false`，授权并验证手动同步后再开启 |
| `GOOGLE_HEALTH_CLIENT_ID` / `_SECRET` | 可选；不填则在网页“账户设置”中保存 |
| `TZ` | 按需要设置容器时区 |

分别执行以下命令两次，为密码与会话密钥生成不同的随机值，并填入 `.env`：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

不要沿用样例密码。没有管理员口令或哈希时，私有模式会拒绝启动；公开模式会正常启动，只是不提供网页登录。会话密钥变化会使已有登录失效。`.env` 不进入镜像和 Git；不要将展开后的 `docker compose config` 输出发布到工单或聊天记录，因为其中可能包含凭据。

## 2. 准备持久化目录并首次启动

Compose 将宿主机 `./profiles` 挂载到容器 `/app/profiles`。首次创建时按样例 UID/GID 设置权限：

```bash
sudo install -d -o 10001 -g 10001 -m 700 profiles
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
```

若迁入已有数据，应先备份，再使整个 `profiles/` 归配置的 UID/GID 所有；目录权限设为 `700`、文件权限设为 `600`。不要删除已有目录或通过开放所有用户写权限来修复问题。

镜像使用固定的 Python 基础镜像摘要和 `requirements.lock`，以非 root 用户运行。Compose 启用只读根文件系统，移除 capabilities 并启用 no-new-privileges，可写区域只有 `profiles/` 与临时目录。Gunicorn 保持单 worker、多线程，因为后台任务状态保存在单个进程中。

首次启动使用上述 `docker compose up -d --build`。`scripts/deploy.sh` 会读取已有容器和镜像，**不能用于首次安装**。

## 3. 配置 HTTPS 反向代理

将自有域名解析到服务器，并让反向代理接收 80/443 请求。Caddy 示例：

```caddy
fitflare.example.com {
    reverse_proxy 127.0.0.1:9000
}
```

参考文件：[deploy/caddy-fitflare.example.caddy](deploy/caddy-fitflare.example.caddy)。不要用示例覆盖已有服务器的其他站点配置；本服务器使用 `server-ops` 的 `caddyctl`。随后在 HTTPS 页面登录，确认能读取健康记录。未登录访问数据接口应返回 `401`；首页和 API 文档可以正常展示。

生产保持 `FITFLARE_SESSION_COOKIE_SECURE=true`。在 HTTP 地址登录时浏览器不会发送 Secure Cookie；只有隔离的本机开发环境才应设为 `false`。不要将业务端口直接暴露公网来绕过代理问题。

## 4. 连接 Google Health 与首次同步

在 Google Cloud 创建启用 Google Health API 的 Web OAuth 客户端，回调 URI 登记为 `http://localhost:8080/callback`。完整步骤与权限范围见 [GOOGLE_HEALTH.md](GOOGLE_HEALTH.md)。

1. 在 HTTPS 页面登录，点击顶部“连接 Google Health”，填写 Client ID、Client Secret 与回调 URI 并保存。
2. 打开 Google 授权页，用关联原 Fitbit 账户的 Google 账号同意所需权限。
3. 浏览器跳转到 `http://localhost:8080/callback?code=...&state=...`。localhost 指浏览器所在电脑，页面打不开也没关系，从地址栏复制**完整 URL**，粘贴回授权窗口提交。
4. 点击“同步数据”完成首次同步。确认数据正常后再开启自动同步。

命令行维护入口（同一账户，无需 `--profile`）：

```bash
docker exec -it fitflare-app python auth/authorize_google_health.py --configure --no-browser
docker exec -it fitflare-app python fetch/fetch_all.py
```

`profiles/<FITFLARE_PROFILE_ID>/auth/google_client.json` 保存客户端配置，`tokens.json` 保存授权；不要打印、提交或上传这些文件。授权 URL、回调 URL 和令牌都不要公开。同步包含网络请求、限流等待和令牌刷新，首次抓取可能耗时较长。

## 运维命令

以下命令在部署目录执行，均作用于 `FITFLARE_PROFILE_ID` 指定的唯一账户，不需要网页登录。

```bash
# 立即同步（与定时同步相同，同步完成后自动重建仪表盘缓存）
docker exec fitflare-app python fetch/fetch_all.py

# 查看最近同步结果与数据来源
curl -s http://127.0.0.1:9000/api/public/v1/me/snapshot-status
docker compose logs --tail 100 fitflare

# 只重建仪表盘缓存（不访问 Google）
docker exec fitflare-app python -c "from common.dashboard_cache import build_dashboard_cache; build_dashboard_cache(None)"

# 重新授权 Google Health（沿用已保存的 OAuth 客户端）
docker exec -it fitflare-app python auth/authorize_google_health.py --no-browser

# 更换 Google OAuth 客户端并授权
docker exec -it fitflare-app python auth/authorize_google_health.py --configure --no-browser
```

授权命令会打印 Google 授权链接：在自己的浏览器打开并同意，然后把跳转后的**完整 URL**（含 `code` 和 `state`）粘贴回终端，输入内容不回显。授权成功后执行一次同步。私有模式下数据接口需要登录，用 `curl` 查询状态会返回 401，改看日志即可。

自动同步、手动同步和授权共用同一把账户锁，不会并发写入；已有同步在进行时，再次执行同步或授权会直接报错退出，部署脚本也会拒绝切换，稍后重试即可。

## 从多档案部署迁移

先备份现有 `.env` 和 `profiles/`，记录原有数据目录名，例如 `profiles/myprofile`。设置 `FITFLARE_PROFILE_ID=myprofile`，保留原凭据、CSV、缓存和文件权限。不要将目录改名为 `me`，也不要合并多个目录。

Fitflare 不会删除其他目录，但不会枚举、访问或同步它们，登录用户也不例外。创建/删除档案接口已经移除。`FITFLARE_DATA_ACCESS` 默认是 `private`，升级时必须配置管理员密码。旧版 `FITBAUS_*` 变量不再读取，升级时需把 `.env` 中的变量名改为 `FITFLARE_*`；会话 Cookie 改名后需要重新登录一次。

## 更新与回滚

部署主机需要 Python 3、Node.js 24、npm、Chromium 系统依赖和 `flock`。门禁中的文档检查也会导入 Python 应用模块，不能只在镜像中安装依赖。首次在部署主机准备检查环境：

```bash
cd /home/lucius7/fitflare
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
npm ci --ignore-scripts
npx playwright install --with-deps chromium
```

每次更新时激活虚拟环境；锁文件有变化时重新安装对应依赖。先确认当前没有正在运行的同步，也没有其他终端在抓取数据。

```bash
cd /home/lucius7/fitflare
git switch main && git pull --ff-only
. .venv/bin/activate
bash scripts/deploy.sh
```

脚本依次执行：

1. 获取远端 `main`，确认当前是干净的 `main`，且提交与 `origin/main` 一致；不满足则停止。部署锁放在 Git 公共目录，避免多个工作树同时发布。
2. 审计源文件和完整 Git 历史，构建 `fitflare:release-<UTC 时间>`（存在 `Dockerfile.host` 时自动使用它），用合成数据跑完整发布门禁。
3. 再次核对源码提交和镜像标签，检查同步锁，以及 Compose 的非 root、只读根文件系统配置。
4. 保留当前镜像为 `fitflare:rollback`，替换应用容器。
5. 等待健康检查，再验证线上数据访问边界；成功后标记为 `fitflare:latest` 并写入 `test-results/deployment.json`。

镜像的 `org.opencontainers.image.revision` 标签记录完整源码提交，`dev.fitflare.source-dirty` 记录构建时是否有未提交改动。`test-results/release-gate.json` 关联提交、镜像 ID 和合成测试结果；`deployment.json` 另记录发布时间与前一镜像 ID。这些文件不含运行配置或健康数据，不进入 Git。`test-results/` 的记录会被后续运行更新，需要长期保留时另存至仓库外的发布记录目录。查看当前运行版本：

```bash
docker inspect fitflare-app --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
cat test-results/deployment.json
```

切换后的健康或访问检查失败时，脚本自动恢复前一镜像。回滚只恢复应用镜像，**不会恢复 `.env`、Compose 文件或数据**。手工回滚：

```bash
FITFLARE_IMAGE=fitflare:rollback docker compose up -d --no-deps --no-build fitflare
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
```

通常让 `FITFLARE_IMAGE` 使用默认的 `fitflare:latest`。不要在其他目录构建或手工修改 Compose 的 `image`/`build`，以免线上代码与仓库不一致。缓存结构版本变化时，首个请求会自动重建 `profiles/*/cache/dashboard.json`。脚本不检查 DNS 和代理，发布后还要验证 HTTPS 域名和浏览器登录。

## 备份与恢复

备份必须保存在仓库之外。`profiles/` 包含令牌、凭据、原始记录与缓存；`.env` 包含部署密钥，应与数据一起限制访问。不要上传到 GitHub、CI 产物或公开网盘。

先关闭自动同步、等待现有同步完成，再停止容器创建一致备份：

```bash
docker compose stop fitflare
sudo install -d -o root -g root -m 700 /var/backups/fitflare
backup_file=/var/backups/fitflare/$(date -u +%Y%m%dT%H%M%SZ).tar.gz
sudo tar -czf "$backup_file" profiles .env
sudo chmod 600 "$backup_file"
docker compose start fitflare
```

恢复时先停止同步与容器，将备份解压到仓库外的暂存目录并检查内容；保留当前 `profiles/` 的副本，再恢复 `.env` 与数据，修复到配置的 UID/GID 和私有权限后启动。确认健康接口、登录与数据均正常，再恢复同步。

## 日常检查

```bash
docker compose ps
docker compose logs --tail 100 fitflare
curl -fsS http://127.0.0.1:9000/api/health
```

容器健康只说明应用响应正常；公网访问还取决于 DNS、代理和 HTTPS。排查时先比较本机健康接口与域名健康接口。日志可能包含账户标识或授权上下文，对外分享前先脱敏。API 用法见 [API.md](API.md)，安全事项见 [SECURITY.md](SECURITY.md)。
