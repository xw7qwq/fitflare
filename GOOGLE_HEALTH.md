# Google Health API v4 接入与迁移

FitBaus 新授权默认使用 Google Health API v4。旧 Fitbit 档案在完成新授权前仍使用原连接；授权成功后按档案切换来源，保留历史 CSV 和公共查询接口。

## 1. 配置 Google Cloud

1. 在 [Google Cloud](https://console.cloud.google.com/) 创建或选择项目，并[启用 Google Health API](https://console.developers.google.com/apis/library/health.googleapis.com)。
2. 配置 Google Auth Platform 的应用名称、支持邮箱及同意页面。个人验证阶段选择 External / Testing，在 Audience 的 Test users 中添加自己的 Google 邮箱。
3. 在 Data Access 中添加以下只读权限（完整前缀为 `https://www.googleapis.com/auth/googlehealth`）：

   | 后缀 | 用途 |
   | --- | --- |
   | `.activity_and_fitness.readonly` | 步数、距离、热量、活跃分钟及运动 |
   | `.health_metrics_and_measurements.readonly` | 静息心率、HRV、体重及补充体征 |
   | `.sleep.readonly` | 睡眠时长及阶段 |
   | `.nutrition.readonly` | 饮食、饮水 |
   | `.profile.readonly` | 账户资料及历史数据起点 |
   | `.settings.readonly` | 设备资料 |

4. 在 Credentials 创建 **Web application** OAuth 客户端，Authorized redirect URIs 填写 `http://localhost:8080/callback`。保存 Client ID 和 Client Secret，不使用原 Fitbit 应用凭据。
5. 先在 Google Health 手机应用中登录对应 Google 账号；如有旧 Fitbit 账号，完成关联或迁移。必须与本地档案历史数据所属账号相同。

配置界面名称和项目限制以 [Google 官方设置文档](https://developers.google.com/health/setup)为准。Testing 模式下 Google 的离线刷新令牌通常在七天后过期，长期自动同步需要按控制台要求调整发布状态；发布或验证不会由 FitBaus 自动完成。

## 2. 在 FitBaus 完成授权

1. 管理员登录 → 档案管理 → 对已有档案点击「连接 Google Health」。首次会打开 Google 客户端配置表单；也可直接点「Google 客户端配置」。
2. 填入新 Client ID、Client Secret 和完全相同的回调 URI，保存并继续。
3. 打开 Google 授权页，同意需要的数据权限。
4. 复制最终浏览器地址栏的**完整 URL**（包含 `code` 和 `state`），粘贴回 FitBaus。localhost 页面无法访问不影响复制 URL。请在十分钟内提交；超时重新开始。
5. 授权成功后点击「同步健康数据」，再查看账户页面的缓存状态。

此手动回调流程不需要向公网暴露 8080 端口。若使用其它回调 URI，Google Cloud 和 FitBaus 必须完全一致。只授予部分权限也可连接，其余类别明确显示未更新。

## 3. 命令行和环境变量

```bash
python auth/authorize_google_health.py --profile Lucius7 --configure
python fetch/fetch_all.py --profile Lucius7
```

容器部署且网页显示「管理未配置」时，可直接在服务器终端运行，无需先启用网页管理：

```bash
docker exec -it fitbaus-app python auth/authorize_google_health.py --profile Lucius7 --configure --no-browser
docker exec -it fitbaus-app python fetch/fetch_all.py --profile Lucius7
```

第一条命令交互读取 Client ID、Client Secret 和回调 URI，并输出 Google 授权链接。用自己的浏览器完成授权，将完整回调 URL 粘贴回终端。已有客户端配置时可以省略 `--configure`。第二条命令只在授权成功后运行。

如要启用网页管理，在部署的 `.env` 中设置自己的 `FITBAUS_ADMIN_PASSWORD`，然后运行 `docker compose up -d --no-build fitbaus`。不要把口令提交到 Git。已有 Google 环境变量配置时可不填表单。

可通过 `GOOGLE_HEALTH_CLIENT_ID`、`GOOGLE_HEALTH_CLIENT_SECRET` 和可选的 `GOOGLE_HEALTH_REDIRECT_URI` 提供配置；环境变量优先于表单设置。否则使用档案私有的 `auth/google_client.json`。授权状态和令牌不写入版本库或镜像。

## 数据兼容

- 日步数、距离、热量、楼层和活跃分钟使用 Google 的日聚合；心率、HRV、睡眠及其它记录使用 v4 数据类型接口，按官方单位转换。
- 继续生成原 `fitbit_*.csv`，避免影响看板和公共 API 的消费者。不同数据集分别记录进度，失败的数据集下次继续补取；缺失值保持为空。
- 睡眠分数沿用 FitBaus 本地算法，并显示为「睡眠估算得分」，不是 Google 或 Fitbit 官方睡眠分数。
- v4 未提供的旧目标、徽章、设备闹钟等资料保留历史快照，并标注为不再更新。不会用猜测的接口替代。
- 迁移时先验证授权权限和 `users/me/identity`。旧 Fitbit ID 必须匹配返回的 `legacyUserId`，再次授权时 Google Health ID 必须一致。验证失败不会切换账号或覆盖历史数据。
- 切换后停止旧 Fitbit 拉取并撤销旧令牌；若撤销请求暂时失败，显示提示并在刷新时重试，不再使用旧连接同步。

## 常见问题

| 提示 | 处理 |
| --- | --- |
| 找不到 Google 凭据 | 配置新的 Google Web OAuth 客户端 |
| `redirect_uri_mismatch` | 核对 Google Cloud 与表单回调 URI，包括协议、端口和路径 |
| 账号未关联 Google Health | 先在手机应用登录并完成旧 Fitbit 账号迁移 |
| 账号与档案不一致 | 使用历史数据所属账号；其它账号请创建新档案 |
| state 过期或不匹配 | 重新发起授权，复制本次完整回调 URL |
| 部分权限未授予 | 在档案管理中重新授权，选择需要的数据类别 |
| 授权已失效 | 重新授权，并检查 Google Cloud 的测试/发布状态 |
| 当前档案正在同步 | 同步完成后重试，避免迁移与数据写入同时发生 |

参考：[迁移指南](https://developers.google.com/health/migration)、[权限范围](https://developers.google.com/health/scopes)、[v4 REST 参考](https://developers.google.com/health/reference/rest)。
