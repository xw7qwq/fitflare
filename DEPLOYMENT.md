# FitBaus 生产部署指南

## 当前部署信息

这份仓库已经在当前服务器完成了一套可长期运行的部署，建议信息如下：

- **代码路径**：`/home/clawd/clawd/fitbaus`
- **容器服务名**：`fitbaus`
- **容器名**：`fitbaus-app`
- **当前对外端口**：`19000`
- **当前访问地址**：`http://159.195.64.222:19000`
- **健康检查**：`http://127.0.0.1:19000/api/health`

> 说明：目前还没有为 `fitbaus` 单独配置 DNS 记录，所以先走 IP + 端口方式。若后续补一个域名解析，可以直接接入 Caddy 做 HTTPS。

---

## 技术栈

- 后端：Python + Flask
- 生产 Web Server：Gunicorn
- 前端：原生 HTML / CSS / JavaScript
- 数据存储：本地文件
  - Fitbit token：`profiles/<profile>/auth/tokens.json`
  - Fitbit app 凭据：`profiles/<profile>/auth/client.json`
  - 抓取到的健康数据：`profiles/<profile>/csv/*.csv`
- 部署方式：Docker Compose

项目**不依赖数据库、Redis、消息队列**，部署相对轻。

---

## 推荐部署方式

### 1）启动/更新服务

在仓库目录下执行：

```bash
cd /home/clawd/clawd/fitbaus
sudo env FITBAUS_PORT=19000 docker compose up -d --build
```

### 2）检查运行状态

```bash
sudo docker compose ps
curl http://127.0.0.1:19000/api/health
```

### 3）查看日志

```bash
sudo docker compose logs -f fitbaus
```

### 4）停止服务

```bash
sudo docker compose down
```

---

## 可配置项

`docker-compose.yml` 已支持通过环境变量调端口和绑定地址：

- `FITBAUS_BIND_HOST`：默认 `0.0.0.0`
- `FITBAUS_PORT`：默认 `9000`
- `FITBAUS_UID`：Linux 宿主机用户 UID，建议设为当前部署用户的 UID
- `FITBAUS_GID`：Linux 宿主机用户 GID，建议设为当前部署用户的 GID
- `FITBAUS_CALLBACK_BIND_HOST`：仅在需要开放 8080 OAuth 回调时使用
- `TZ`：容器时区，默认 compose 里是 `Etc/UTC`

示例：

```bash
cd /home/clawd/clawd/fitbaus
export FITBAUS_BIND_HOST=0.0.0.0
export FITBAUS_PORT=19000
sudo -E docker compose up -d --build
```

如果你想只让反向代理访问，而不直接暴露公网端口，可以改成：

```bash
export FITBAUS_BIND_HOST=127.0.0.1
export FITBAUS_PORT=19000
sudo -E docker compose up -d --build
```

---

## Fitbit 凭据与授权

### 必需内容

每个 profile 都需要一组 Fitbit Developer App 凭据：

- `client_id`
- `client_secret`

推荐来源二选一：

1. 通过环境变量提供
2. 保存在 `profiles/<profile>/auth/client.json`

现在的刷新逻辑已经改为：

- **优先读取** `FITBIT_CLIENT_ID` / `FITBIT_CLIENT_SECRET`
- 否则读取 `profiles/<profile>/auth/client.json`
- **不再回退到硬编码共享凭据**

这样更适合生产环境，也更安全。

### 首次授权流程

进入容器：

```bash
sudo docker exec -it fitbaus-app bash
```

创建并授权 profile：

```bash
python auth/authorize_fitbit.py --profile lucius
```

授权完成后抓取数据：

```bash
python fetch/fetch_all.py --profile lucius
```

然后访问：

- `http://159.195.64.222:19000`

### 关于回调 URL

默认授权脚本使用：

```text
http://localhost:8080/callback
```

这对“手动粘贴回调 URL”的流程是可用的，即使服务器本身不开放 8080 也能完成授权。

如果你想做真正的本地 HTTPS 回调监听，还可以额外配置：

- `FITBIT_REDIRECT_URI`
- `FITBIT_SSL_CERT`
- `FITBIT_SSL_KEY`

并在 compose 里开放 8080。

---

## 数据持久化

数据都通过 bind mount 保存在仓库本地目录：

```text
./profiles -> /app/profiles
```

所以：

- 容器重建不会丢数据
- 升级镜像不会丢数据
- 只要 `profiles/` 还在，授权信息和 CSV 数据就都在

建议备份：

```bash
tar -czf fitbaus-profiles-backup.tar.gz profiles/
```

---

## 反向代理（可选）

如果后续给它加域名，推荐走 Caddy 反代到本地 `19000` 端口。

示例配置见：

- `deploy/caddy-fitbaus.example.caddy`

DNS 准备好后，大致流程：

1. 给域名加 A 记录指向 `159.195.64.222`
2. 把容器改成仅监听 `127.0.0.1:19000`
3. 把 Caddy 配置加进去
4. `sudo systemctl reload caddy`

---

## 常用运维命令

### 重建

```bash
cd /home/clawd/clawd/fitbaus
sudo env FITBAUS_PORT=19000 docker compose up -d --build --force-recreate
```

### 查看容器内文件

```bash
sudo docker exec -it fitbaus-app bash
ls -la /app/profiles
```

### 手动刷新数据

```bash
sudo docker exec -it fitbaus-app python fetch/fetch_all.py --profile lucius
```

### 重新授权

```bash
sudo docker exec -it fitbaus-app python auth/authorize_fitbit.py --profile lucius
```

---

## 已知注意事项

1. **没有 DNS 时先用 IP:端口访问**。
2. **生产环境不要依赖硬编码 Fitbit 凭据**，应使用 profile 自己的 `client.json` 或环境变量。
3. `profiles/` 如果权限不对，容器里创建 profile 可能失败；必要时修复：

```bash
cd /home/clawd/clawd/fitbaus
sudo chown -R 10001:10001 profiles
sudo chmod -R u+rwX,go+rX profiles
```

4. 如果你准备用 Caddy 反代，建议把容器绑定到 `127.0.0.1`，不要把业务端口直接暴露公网。

---

## 一条最省事的上线命令

```bash
cd /home/clawd/clawd/fitbaus && sudo env FITBAUS_PORT=19000 docker compose up -d --build
```
