# fitflare 生产部署指南

本文适用于 Linux 上的 Docker Compose 部署。仓库名称为 fitflare，兼容名称保持为 Compose 服务 `fitbaus`、容器 `fitbaus-app`、环境变量 `FITBAUS_*`。宿主机只监听 `127.0.0.1:9000`，由 Caddy 提供 HTTPS。

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
| `FITBAUS_UID` / `FITBAUS_GID` | `10001` / `10001`，与数据目录属主一致 |
| `FITBAUS_BIND_HOST` / `FITBAUS_PORT` | `127.0.0.1` / `9000` |
| `FITBAUS_DATA_ACCESS` | `private`，数据读取需要管理员登录 |
| `FITBAUS_PUBLIC_PROFILES` | 留空，不允许匿名访问任何档案；公开模式按需填写逗号分隔的档案 ID |
| `FITBAUS_ADMIN_PASSWORD` | 必须自行生成；也支持用 `FITBAUS_ADMIN_PASSWORD_HASH` 提供 Werkzeug 兼容哈希 |
| `FITBAUS_SESSION_SECRET` | 独立生成并持久保存的随机密钥，建议至少 32 字符 |
| `FITBAUS_SESSION_COOKIE_SECURE` | `true`，管理员通过 HTTPS 登录 |
| `FITBAUS_AUTO_SYNC_ENABLED` | 首次部署设为 `false`，授权并验证手动同步后再决定是否开启 |
| `TZ` | 按需要设置容器时区 |

分别执行以下命令两次，为密码与会话密钥生成不同的随机值，并填入 `.env`：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

不要沿用样例密码。没有管理员口令或哈希时，私有模式会拒绝启动。会话密钥变化会使已有登录失效。`.env` 不进入镜像和 Git；不要将展开后的 `docker compose config` 输出发布到工单或聊天记录，因为其中可能包含凭据。

## 2. 准备持久化目录并首次启动

Compose 将宿主机 `./profiles` 挂载到容器 `/app/profiles`。首次创建时按样例 UID/GID 设置权限：

```bash
sudo install -d -o 10001 -g 10001 -m 700 profiles
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
```

若迁入已有数据，应先备份，再使整个 `profiles/` 归配置的 UID/GID 所有；目录权限设为 `700`、文件权限设为 `600`。不要删除已有档案或通过开放所有用户写权限来修复问题。

Docker 镜像使用固定的 Python 基础镜像摘要和 `requirements.lock`，以非 root 用户运行。Compose 启用只读根文件系统，运行时可写区域为 `profiles/` 与临时目录。Gunicorn 保持单 worker、多线程，因为后台任务状态保存在单个进程中。

首次启动使用上述 `docker compose up -d --build`。`scripts/deploy.sh` 会读取已有容器和镜像，**不能用于首次安装**。

## 3. 配置 Caddy 与 HTTPS

将自有域名解析到服务器，并让 Caddy 能接收 80/443 请求。把以下站点块合并进 Caddy 配置，示例中的 `fitbit.example.com` 必须换成自己的域名：

```caddy
fitbit.example.com {
    reverse_proxy 127.0.0.1:9000
}
```

参考文件：[deploy/caddy-fitbaus.example.caddy](deploy/caddy-fitbaus.example.caddy)。不要用示例覆盖已有服务器的其他站点配置。采用系统服务安装的 Caddy 可执行：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl -fsS https://fitbit.example.com/api/health
```

首次启用 Caddy 时先启动其服务。随后在 HTTPS 页面点击管理员登录，确认私有档案能够正常读取。未登录访问数据接口应返回 `401`；首页和 API 文档可以正常展示。

生产保持 `FITBAUS_SESSION_COOKIE_SECURE=true`。在 HTTP 地址登录时浏览器不会发送 Secure Cookie；只有隔离的本机开发环境才应设为 `false`。不要将业务端口直接暴露公网来绕过代理问题。

## Fitbit 授权与首次同步

先在 Fitbit 开发者平台创建适合自己账户使用的应用，保存 Client ID / Client Secret，并登记与脚本一致的 Redirect URI。下面采用 SSH/Docker 下的手工回调流程，不需要向公网开放 8080 端口。

在 `.env` 中设置以下两项为同一个已登记地址：

```dotenv
FITBIT_REDIRECT_URI=http://localhost:8080/callback
FITBIT_FALLBACK_REDIRECT=http://localhost:8080/callback
```

修改 `.env` 后应用配置并启动授权：

```bash
docker compose up -d
docker compose exec -e FITBIT_AUTH_TIMEOUT=1 fitbaus \
  python auth/authorize_fitbit.py --profile myprofile
```

档案 ID 使用英文字母、数字、下划线或连字符。脚本优先读取 `FITBIT_CLIENT_ID` / `FITBIT_CLIENT_SECRET`，其次读取该档案保存的凭据；缺少凭据时会提示输入。不要将凭据直接写进源码或命令参数。

1. 脚本尝试本地回调，短暂超时后显示手工授权提示。使用手工流程输出的授权链接，在自己的浏览器完成 Fitbit 登录与权限确认。
2. 浏览器跳转到已登记的 `http://localhost:8080/callback?code=...`。此处的 localhost 指浏览器所在电脑，即使页面无法连接，也可以从地址栏复制完整 URL。
3. 将完整回调 URL 粘贴回仍在运行的终端。脚本交换令牌，并保存到 `profiles/myprofile/auth/`。
4. 首次同步：

```bash
docker compose exec fitbaus python fetch/fetch_all.py --profile myprofile
```

回到网页查看档案和数据；没有数据的指标取决于 Fitbit 的返回结果。授权 URL、回调 URL、令牌和授权日志都不要公开。保持 `FITBIT_FALLBACK_REDIRECT` 与登记地址一致，避免 HTTP 本地回调超时后使用空地址。

后续可在管理员界面手动同步。若开启定时同步，将 `.env` 中 `FITBAUS_AUTO_SYNC_ENABLED=true` 后运行 `docker compose up -d`；默认档案同步间隔为 21600 秒、扫描间隔为 300 秒。同步包含网络请求、限流等待和令牌刷新，首次抓取可能耗时较长。

## 更新与回滚

部署主机需要 Python 3、Node.js 24、npm、Chromium 系统依赖和 `flock`。以下命令面向已安装的 Linux 部署；先按下节备份，并确认当前没有正在运行的 Fitbit 同步，也没有其他终端在抓取数据。

```bash
git pull --ff-only origin main
npm ci --ignore-scripts
npx playwright install --with-deps chromium
bash scripts/deploy.sh
```

部署脚本依次执行：

1. 用合成数据执行完整检查，构建并验证带时间戳的发布镜像。
2. 检查现有档案的同步锁，以及 Compose 的非 root、只读根文件系统配置。
3. 保留旧镜像为 `fitbaus-fitbaus:rollback`，替换应用容器。
4. 等待 Docker 健康检查，再验证生产数据访问边界；成功后更新 `fitbaus-fitbaus:latest` 标签。

切换后的健康或访问检查失败时，脚本尝试恢复前一镜像。回滚只恢复应用镜像，**不会恢复 `.env`、Compose 文件或数据**；仍需检查命令退出状态与容器状态。手工恢复已有回滚标签可执行：

```bash
FITBAUS_IMAGE=fitbaus-fitbaus:rollback docker compose up -d --no-deps --no-build fitbaus
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
```

通常让 `FITBAUS_IMAGE` 使用 Compose 的 `latest` 默认值。如果在 `.env` 固定了旧镜像，下次普通 `docker compose up` 会重新采用该值，更新时必须同步维护。脚本不检查 Caddy/DNS，因此发布后还要验证 HTTPS 域名和浏览器登录。

## 备份与恢复

备份必须保存在仓库之外。`profiles/` 包含令牌、凭据、原始记录与缓存；`.env` 包含部署密钥，应与档案一起限制访问。不要将这些文件上传到 GitHub、CI 产物或公开网盘。

先关闭自动同步、等待现有同步完成，并确保没有额外的命令行抓取进程，再停止容器创建一致备份。例如：

```bash
docker compose stop fitbaus
sudo install -d -o root -g root -m 700 /var/backups/fitflare
backup_file=/var/backups/fitflare/$(date -u +%Y%m%dT%H%M%SZ).tar.gz
sudo tar -czf "$backup_file" profiles .env
sudo chmod 600 "$backup_file"
docker compose start fitbaus
```

停止容器会短暂中断站点，等待已有同步完成可避免中途打断写入。备份成功后再按需恢复自动同步。保留多个备份版本，复制到其他机器时使用受控、加密的存储。

恢复时先停止同步与容器，将备份解压到仓库外的暂存目录并检查内容；保留当前 `profiles/` 的副本，再恢复 `.env` 与档案，修复到配置的 UID/GID 和私有权限后启动。确认健康接口、管理员登录与档案数据均正常，再恢复同步。

## 日常检查

```bash
docker compose ps
docker compose logs --tail 100 fitbaus
curl -fsS http://127.0.0.1:9000/api/health
```

容器健康只说明应用响应正常；公网访问还取决于 DNS、代理和 HTTPS。排查时先比较本机健康接口与域名健康接口。日志可能包含档案标识或授权上下文，对外分享前先脱敏。API 使用方式见 [API.md](API.md)，安全事项见 [SECURITY.md](SECURITY.md)。
