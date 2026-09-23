# API 文档重构 · 2026-09-23

## 范围

只重构文档、OpenAPI 和相关测试，不改变既有数据接口 URL、响应数据、公开档案白名单、管理员配置或 Fitbit 授权。没有主动同步、创建或删除真实档案。

入口保持 `/api/public/v1/docs`、`/api/public/v1/docs.md`、`/api/public/v1/openapi.json`。

## 单一接口目录

`common/api_docs.py` 定义 25 个版本化 GET 接口（22 个数据接口、3 个文档入口）、参数、返回结构、合成示例与访问说明。HTML 和 Markdown 直接从此目录生成；OpenAPI 由原来的 9 个路径补齐到 25 个，并按当前部署模式描述会话认证。

`common/public_api.py` 保留数据与 SVG 构建逻辑，移除了旧的手写 OpenAPI 副本。公开接口响应中的多余 public 缓存头赋值已移除；最终策略仍由统一安全层设置为 private, no-store，没有放宽缓存或访问控制。

文档不会枚举或读取真实档案。示例使用 YOUR_PROFILE 占位符，用户在请求示例中自行填写。域名由浏览器当前 origin 生成；无 JavaScript 的静态示例用 YOUR_HOST 占位符，OpenAPI servers 使用同源相对地址，避免反代后的 HTTP/HTTPS 示例错配。

维护流程：

```sh
# 修改 common/api_docs.py 中的目录或说明后：
python3 scripts/docs.py
python3 scripts/docs.py --check
npm run check
```

API.md 为生成结果，不要单独手改。检查脚本会拒绝生成文件漂移；离线测试还会将 OpenAPI 路径、路径参数及响应结构与实际 Flask 路由比较。

## 页面结构

- `templates/public_api.html`：页面结构、原生 details 折叠接口、无 JavaScript 可读正文。
- `docs.css`：独立样式，与仪表盘保持深色视觉；不会影响仪表盘 CSS。
- `js/docs.js`：本地接口搜索、深链接、复制、参数输入和显式 GET 操作。
- `js/docs-request.js`：同源请求 URL、cURL/JavaScript/Python 示例、响应预览上限等可独立测试的函数。

首页只显示快速开始和分组接口摘要，参数表及示例展开阅读。搜索支持名称、路径和参数，`/` 聚焦搜索，Escape 清空。禁用 JavaScript 后，25 个接口、参数和合成响应仍可阅读。

接口说明明确区分：series/SVG 的 limit 是最后 N 个点；datasets/tables 是从 offset 开始分页，默认分别为 200/100，上限 1000。tables 仅覆盖派生缓存里已有的记录，不能当作完整历史。

## 请求示例的边界

页面加载、切换接口、修改参数和生成代码均不会请求健康数据。只有“发送 GET”触发一次同源版本化 API 请求；支持当前站点登录会话。未替换 YOUR_PROFILE 时阻止发送。

请求可取消，15 秒超时，不自动重试；响应最多读取 64 KiB，按 textContent 展示，即使返回 SVG/HTML 也不插入可执行 DOM。响应不写入 localStorage、sessionStorage 或服务端；切换请求或离开页面会清空预览。

文档设置独立 CSP，脚本、样式、图像与连接限于本站，不依赖 CDN、第三方文档框架或外部字体。剪贴板不可用或权限拒绝时选中代码并提示手动复制。

## 验证

13 项前端纯函数测试、25 项后端测试；其中新增 4 项文档请求工具测试和 9 项后端文档契约测试。响应检查覆盖项目使用的 JSON Schema 子集，不将其称作第三方 OpenAPI 全规范认证。

`tests/docs-browser.cjs` 在公开/私有两种隔离环境中验证：桌面与手机搜索、复制、深链接、三种语言、显式 GET、会话边界、参数输入、64 KiB 上限、SVG 文本隔离、取消和剪贴板降级；无 JavaScript 可读性；320/390/768/1024/1440 px 宽度的溢出检查。测试均使用合成档案。

统一发布脚本仍执行原有仪表盘回归、镜像敏感路径检查、同步锁检查和失败回滚。文档浏览器回归已经接入同一 gate，因此已有 GitHub Actions 配置也会执行它；没有推送远程仓库。

## 基线与回滚

改动前桌面文档完整高度为 4136 px（1440×1000 视口）；手机为 7703 px（390×844 视口），手机实际内容宽度达到 1164 px。初轮重构后相同视口高度为 2893 / 3297 px，宽度没有超出视口。此数据衡量默认折叠状态下的版面，不代表加载速度；新版包含更多接口和交互，不声称总传输体积下降。

源码基线、前后截图、测试输出保存在 `/home/lucius7/fitbaus-docs-20260923/`。改动前镜像保留为 `fitbaus-fitbaus:before-docs-20260923`，具备上一轮的私有模式和非 root 加固。

```sh
# 仅在确认无正在运行的同步任务时回滚；不修改 profiles 或 .env。
FITBAUS_IMAGE=fitbaus-fitbaus:before-docs-20260923 \
  docker compose up -d --no-deps --no-build fitbaus
```
