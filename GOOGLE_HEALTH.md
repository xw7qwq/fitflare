# Google Health API v4 接入与迁移

Fitflare 新授权默认使用 Google Health API v4。旧 Fitbit 账户在完成新授权前仍使用原连接；授权成功后切换来源，保留历史 CSV 和个人查询接口。

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
5. 先在 Google Health 手机应用中登录对应 Google 账号；如有旧 Fitbit 账号，完成关联或迁移。必须与本地历史数据所属账号相同。

配置界面名称和项目限制以 [Google 官方设置文档](https://developers.google.com/health/setup)为准。Testing 模式下 Google 的离线刷新令牌通常在七天后过期，长期自动同步需要按控制台要求调整发布状态；发布或验证不会由 Fitflare 自动完成。

## 2. 在服务器完成授权

Fitflare 网站公开只读，没有登录或授权表单。Google 授权通过以下命令完成，Google 自己的同意页面仍需登录对应的 Google 账号。

1. 在服务器运行下节的授权命令，填写 Client ID、Client Secret 和完全相同的回调 URI。
2. 在自己的浏览器打开命令输出的 Google 授权链接，同意需要的数据权限。
3. 复制最终浏览器地址栏的**完整 URL**（包含 `code` 和 `state`），粘贴回终端，输入不回显。localhost 页面无法访问不影响复制 URL。请在十分钟内提交；超时重新开始。
4. 授权成功后运行同步命令，再刷新网站查看记录。

此手动回调流程不需要向公网暴露 8080 端口。若使用其它回调 URI，Google Cloud 和 Fitflare 必须完全一致。只授予部分权限也可连接，其余类别明确显示未更新。

## 3. 命令行和环境变量

```bash
python auth/authorize_google_health.py --configure
python fetch/fetch_all.py
```

容器部署使用以下命令：

```bash
docker exec -it fitflare-app python auth/authorize_google_health.py --configure --no-browser
docker exec -it fitflare-app python fetch/fetch_all.py
```

第一条命令交互读取 Client ID、Client Secret 和回调 URI，并输出 Google 授权链接。用自己的浏览器完成授权，将完整回调 URL 粘贴回终端。已有客户端配置时可以省略 `--configure`。第二条命令只在授权成功后运行。

可通过 `GOOGLE_HEALTH_CLIENT_ID`、`GOOGLE_HEALTH_CLIENT_SECRET` 和可选的 `GOOGLE_HEALTH_REDIRECT_URI` 提供配置；环境变量优先于命令行保存的配置。否则使用账户目录中私有的 `auth/google_client.json`。授权状态和令牌不写入版本库或镜像。

## 数据兼容

- 日步数、距离、热量、楼层和活跃分钟使用 Google 的日聚合；心率、HRV、睡眠及其它记录使用 v4 数据类型接口，按官方单位转换。
- 继续生成原 `fitbit_*.csv`，避免影响看板和公共 API 的消费者。不同数据集分别记录进度，失败的数据集下次继续补取；缺失值保持为空。
- 睡眠分数沿用 Fitflare 本地算法，并显示为「睡眠估算得分」，不是 Google 或 Fitbit 官方睡眠分数。
- v4 未提供的旧目标、徽章、设备闹钟等资料保留历史快照，并标注为不再更新。不会用猜测的接口替代。
- 迁移时先验证授权权限和 `users/me/identity`。旧 Fitbit ID 必须匹配返回的 `legacyUserId`，再次授权时 Google Health ID 必须一致。验证失败不会切换账号或覆盖历史数据。
- 切换后停止旧 Fitbit 拉取并撤销旧令牌；若撤销请求暂时失败，显示提示并在刷新时重试，不再使用旧连接同步。

## 常见问题

| 提示 | 处理 |
| --- | --- |
| 找不到 Google 凭据 | 配置新的 Google Web OAuth 客户端 |
| `redirect_uri_mismatch` | 核对 Google Cloud 与命令行配置的回调 URI，包括协议、端口和路径 |
| 账号未关联 Google Health | 先在手机应用登录并完成旧 Fitbit 账号迁移 |
| 账号与本地记录不一致 | 使用历史数据所属账号；Fitflare 只服务一个账户，其它账号请单独部署 |
| state 过期或不匹配 | 重新发起授权，复制本次完整回调 URL |
| 部分权限未授予 | 运行授权命令重新授权，选择需要的数据类别 |
| 授权已失效 | 重新授权，并检查 Google Cloud 的测试/发布状态 |
| 当前账户正在同步 | 同步完成后重试，避免迁移与数据写入同时发生 |

参考：[迁移指南](https://developers.google.com/health/migration)、[权限范围](https://developers.google.com/health/scopes)、[v4 REST 参考](https://developers.google.com/health/reference/rest)。
