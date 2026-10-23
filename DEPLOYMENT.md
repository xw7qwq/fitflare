# Fitflare 生产部署

## 当前部署（2026-10-23）

- 正式地址：`https://fitflare.lucius7.dev/`
- 旧地址：`https://fitbit.lucius7.dev/`，保留路径和查询参数并跳转至新地址。
- SSH：`lucius7`
- 部署目录：`/home/lucius7/fitbaus`
- Docker Compose 服务：`fitbaus`；容器：`fitbaus-app`
- 上游监听：`127.0.0.1:9000`，通过 Caddy 和 Cloudflare 代理提供 HTTPS。
- 持久数据：部署目录的 `profiles/`；历史 CSV、缓存和授权凭据保留。
- Google OAuth 应用名称：`Fitflare`；发布状态：正式版（个人自用）。
- 自动同步：每 21600 秒（6 小时）检查并同步。

网站、API 文档与 OAuth 品牌统一为 Fitflare。已有目录、Compose 服务、环境变量、Cookie 和自定义 API 标头保留兼容名称，避免改名打断现有数据及运维。

## 运行与检查

```bash
cd /home/lucius7/fitbaus
docker compose ps
curl -fsS http://127.0.0.1:9000/api/health
docker compose logs --tail 100 fitbaus
```

手动同步：

```bash
docker exec -it fitbaus-app python fetch/fetch_all.py --profile Lucius7
```

现有 Compose 已指向经过安全修补的构建基底和构建目录。更新前核对 `build.context`、`build.dockerfile` 和 `image`，保留前一镜像标签；不要用上游原始镜像覆盖安全基底。

```bash
docker compose build fitbaus
docker compose up -d --no-build fitbaus
```

容器以非 root 用户运行，根文件系统只读，移除 capabilities，并启用 no-new-privileges。只挂载 `profiles/` 持久数据，令牌和客户端密钥不进入镜像。

## 域名与 HTTPS

Cloudflare 的 `fitflare` A 记录指向现有服务器并启用代理。旧 `fitbit` 记录保留，用于兼容跳转。Caddy 主配置：

`/home/lucius7/server-ops/caddy/Caddyfile`

使用已有的 `caddyctl check` 和 `caddyctl apply` 检查并原子加载候选配置，自动保留受限备份；不直接覆盖共用配置或重启其他服务。

Google 品牌首页、隐私政策、使用条款分别使用新域名下的 `/about.html`、`/privacy.html`、`/terms.html`。授权网域仍为 `lucius7.dev`。

## Google Health 授权

当前使用 Google Health API v4 与六项只读权限。访问令牌按需自动刷新。OAuth 已切换为正式版并重新签发刷新令牌，不再使用测试模式的七天授权。Google 仍可因撤销、长时间未使用等原因使授权失效。

客户端回调保持 `http://localhost:8080/callback`。这是手动粘贴回调的流程，与网站域名无关，不需要开放公网 8080 端口。配置或重新授权：

```bash
docker exec -it fitbaus-app python auth/authorize_google_health.py --profile Lucius7 --configure --no-browser
```

`profiles/Lucius7/auth/google_client.json` 保存 Google 客户端配置；`tokens.json` 保存授权。不要打印、提交或上传这些文件。完整流程见 [GOOGLE_HEALTH.md](GOOGLE_HEALTH.md)。

## 访问控制与历史数据

当前部署使用公开只读看板。网页管理未配置管理员口令时自动停用；授权和运维经 SSH 完成。`FITBAUS_DATA_ACCESS`、`FITBAUS_PUBLIC_PROFILES` 和管理员配置继续使用兼容名称。

域名和品牌调整不修改 Google OAuth 客户端 ID、权限、历史 CSV 或档案 ID。备份和回滚时保留 `profiles/`，不要执行数据重置。
