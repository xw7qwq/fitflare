# 2026-09-23 优先级优化

## 上线验证

已通过统一发布脚本部署 `fitbaus-fitbaus:release-20260923T124049Z`。容器 healthy，实际用户 10001:10001，根文件系统只读、capabilities 全部移除、no-new-privileges 生效。六小时自动同步调度器已在工作进程中启动。

公网桌面 1440×1000 与手机 390×844 均验证八个视图，无页面 JavaScript 异常或横向溢出。首屏 API 请求从原来的四次减为三次，不请求档案摘要或记录表。当前档案 JSON 未压缩响应由 95720 字节降为 82476 字节，减少约 13.8%；不是网络加载时间指标。进入档案视图后摘要请求一次，返回该视图复用结果。

九项前端单元测试、十六项后端离线测试、桌面/手机完整浏览器回归、私有模式登录/退出回归和源码/镜像敏感路径检查均通过。分页用 55 条合成记录验证 20 + 20 + 15，真实数据仅执行只读展示检查。未对真实 Fitbit 执行手动同步、重新授权、创建或删除档案；非 root 写权限使用临时探针验证。

## 访问边界与运行权限

保留现有公开只读使用方式，但用 `FITBAUS_PUBLIC_PROFILES` 显式列出可公开档案；新建档案不会因为加入目录就自动公开。空字符串表示没有公开档案，`*` 表示全部公开。

`FITBAUS_DATA_ACCESS=private` 会统一保护仪表盘、档案列表、版本化 API、快照、数据表与 SVG 图表。静态登录页面、健康接口与不含数据的文档仍可访问。私有模式必须配置管理员口令或哈希，否则启动直接失败，避免配置错误时意外公开数据。

匿名仪表盘响应改为服务端白名单，不再包含 `files` 内部路径。所有数据及管理响应为 `private, no-store`；只有匿名公开版 API 返回 CORS 许可。登录增加每个连接来源 60 秒最多 5 次尝试的限制，未信任任意客户端转发 IP 头；在反向代理后，此限制可能由多个访问者共享。

生产运行用户为 `10001:10001`。根文件系统只读，drop ALL capabilities，no-new-privileges，临时目录为独立 tmpfs。profiles 目录归该专用用户，目录权限 700、文件权限 600；`.env` 归部署用户，权限 600。原有十二个运行数据文件逐一进行 SHA-256 前后校验，内容没有改变；只调整属主和权限。

独立随机会话签名密钥持久化在 `profiles/.system/session.key`，仅通过 `FITBAUS_SESSION_SECRET_FILE` 读取。不使用管理员口令作为会话密钥，也不生成默认管理员密码。原有 Fitbit 凭据和管理员口令配置均保持不变。

## 后端结构

```text
server.py                    应用工厂、静态入口、健康检查（88 行）
backend/config.py            环境配置
backend/security.py          会话、CSRF、访问白名单、登录限流
backend/repository.py        可见档案及公共数据读取
backend/dashboard_routes.py  页面数据与按页读取接口
backend/public_routes.py     版本化只读 API
backend/admin_routes.py      档案、手动同步及 OAuth 操作
backend/jobs.py              同一工作进程内的任务状态
backend/sync.py              同步调度、文件锁及进度解析
backend/time_utils.py        共用时间函数
templates/public_api.html    从 Python 字符串移出的 API 文档
```

原有 URL 保留，新增 `/api/tables/<profile_id>/<table_key>`。禁用了 Flask 未使用的默认 `/static/` 文件入口。原有仪表盘无参数请求仍包含表格；`?tables=none` 用于轻量首屏。

Gunicorn 改为 1 worker、4 threads，使手动任务状态不再随机分散到两个独立内存字典。任务 ID 使用 UUID，终态任务保留最近 100 个，子进程输出仅保留最后 200 行。禁用按请求数自动回收 worker，调度器只在 worker 初始化后启动。保留档案文件锁，避免并行同步同一档案。任务仍是进程内状态，不承诺服务重启后继续手动任务；这不是独立任务队列。

## 前端按需加载

首页不再请求多档案摘要，也不再携带所有记录表。档案摘要只在打开“档案”时请求，成功后复用；登录状态变化、重建缓存或同步完成会使缓存失效。

表格进入对应主题并展开后才请求；分页每页 20 条，后端 limit 上限 100。支持上一页、下一页、加载失败重试和档案切换取消旧请求。日期范围控制仍仅作用于趋势图；表格保留原有记录范围，由分页独立控制。

## 检查、发布与回滚

保留了原有 Git 历史，在 `maintenance/priority-20260923` 分支上修改；重构前检查点标记为 `before-priority-20260923`。没有推送远程仓库。之前关于“没有 Git 仓库”的说明已更正：当时是 root 属主触发目录保护。

固定 Python 基础镜像 digest；Python 依赖继续使用既有 requirements.lock。Playwright 仅作为开发依赖，锁定版本并提交 package-lock.json。

```sh
npm ci --ignore-scripts
npx playwright install --with-deps chromium
npm run check
```

完整检查包括源文件敏感路径及私钥标记检查、JavaScript 语法与单元测试、Docker 构建、镜像敏感文件检查、16 项离线 Flask 测试、桌面/手机浏览器回归及私有模式登录/退出。浏览器和后端测试只使用生成的 Demo/Hidden 合成档案，不复制真实健康数据或授权文件。

`.github/workflows/check.yml` 调用同一个检查入口。文件已准备好，但只有将分支推送至支持 GitHub Actions 的仓库后才会触发远端 CI。

发布入口会强制先执行整套检查，检查生产档案同步锁，再重建容器并校验健康与数据访问响应；失败时自动恢复前一镜像，不改写 profiles。

```sh
bash scripts/deploy.sh
```

该脚本需要 Docker、Node、Python 和 Playwright。现有管理员口令仍未配置，因此生产继续公开只读，不自动开启管理功能。

回滚到本次优化前的精简界面版本，继续使用已经修复的非 root 权限：

```sh
FITBAUS_IMAGE=fitbaus-fitbaus:before-priority-20260923 \
  docker compose up -d --no-deps --no-build fitbaus
```

脚本检查现有同步锁，但不提供分布式原子发布协议；部署应安排在无同步任务时。旧版回滚镜像不具备新增访问白名单与私有模式保护，因此启用私有模式以后不要直接回滚到旧镜像。

本机配置和历史备份位于 `/home/lucius7/fitbaus-priority-20260923/`，目录权限 700。其中 env-before 含原配置，应作为敏感备份保管，不上传或公开。
