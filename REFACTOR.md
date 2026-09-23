# 2026-09-23 精简重构

## 发布结果

已部署到 fitbit.lucius7.dev，公网 Chromium 桌面（1440×1000）和手机（390×844）均检查八个视图，未捕获页面 JavaScript 异常。首页完整高度由 6300 / 14360 像素降为 1177 / 1689 像素；这是相同视口下的页面高度对比，不是加载速度指标。

自有前端 HTML、JavaScript 与 CSS 共由 213117 字节降为 85509 字节，不包含前后相同版本的第三方 Chart.js。九项单元测试通过，六组主要只读 API 的响应结构对比通过。最终镜像内依赖列表与重构前运行容器一致，且未打包 profiles 中的数据或授权文件。

线上管理员口令原本未配置，重构前截图与上线后的 session 接口均证实这一状态；此次保持原有只读行为，没有设置默认口令。登录、退出与 CSRF 检查在隔离预览环境中使用临时测试口令执行。自动同步仍为六小时一次，健康接口及容器状态均正常。

## 结构

保留 Flask / Gunicorn / 原生 JavaScript。没有引入 React、前端打包器或运行时 npm 依赖。

```text
index.html          单一页面入口、八个主题页、原生 dialog
style.css           唯一自有样式表
app.js              页面状态、事件、路由、请求、管理操作
js/format.js        空值处理、数字日期格式化、HTML 转义
js/data.js          视图模型、按日期切片、7/30 天均值、周聚合
js/charts.js        Chart.js 生命周期、更新、空数据提示
js/views.js         各主题页的图表与表格
vendor/             固定版本的本地 Chart.js
requirements.lock   重构前线上实际使用的 Python 依赖版本
```

不再使用的 script.js、ui-cn.js、tailwind.css、tailwind.config.js、tailwind.input.css、version.js 已移除。mobile.html 和 spousal.html 保留为兼容跳转入口。

## 界面与行为

只保留一组导航和一个趋势范围选择器。首页显示四项核心指标、两张趋势图；其他指标与相关性折叠展示。设备、授权范围和缓存信息集中到“账户”。保留所有八个主题页与已有管理 API。

恢复页保留 HRV、静息心率、睡眠得分的均值、图表和原始记录，移除重复摘要、将数据覆盖率表述成“可信度”的说明和自动训练建议。原有本地恢复估算仍可查看，但明确不是 Fitbit 官方指标或医疗结论。

趋势范围以最近缓存日期为终点，按日历天取值，不再按最后 N 行截取。周平均图使用所选日期范围。7/30 天均值使用各自固定窗口，补充快照与原始表格保留自身时间标记，并不声称随趋势选择器一起过滤。

修正空值被 Number(null) 当成 0 的问题，以及表格格式化回调传入行对象时小数位参数异常的问题。快速切换档案会中止旧请求，并校验请求序号，避免迟到响应覆盖新档案。

图表更新重用实例；没有数据或组件加载失败时显示文字提示。弹窗使用原生 dialog，支持 Escape、焦点限制与返回。Client Secret 使用密码输入框。保留原有管理员认证、CSRF、授权交换和同步端点。

## 静态文件与构建

静态资源严格按名单开放；HTML 与自有模块使用 no-cache 重新验证，固定版本图表库使用 immutable 缓存。管理接口及认证后的响应使用 private, no-store。

Chart.js 仍使用原项目的 4.4.1，改为本地提供，没有顺带升级。文件保留许可证头，SHA-256：

```text
d2af8974e95271638772e9e9524db5b9a6f58d6ec2d5d781400447b4a31c681e
```

Docker 构建已排除 profiles/、.env*、client.json、tokens*.json、测试与测试产物。运行时 profiles 仍由原来的 bind mount 提供，未改变数据路径或授权配置。requirements.txt 保留直接依赖的说明；Docker 实际安装 requirements.lock，避免一次界面重构隐式升级后端依赖。

## 验证

无需安装 npm 依赖即可运行九项纯函数测试：

```sh
npm test
```

浏览器回归需要可用的 Playwright 和 Chromium。建议对隔离预览服务执行，而不是对生产做管理功能测试：

```sh
BASE_URL=http://127.0.0.1:9001 \
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
PLAYWRIGHT_BROWSERS_PATH=/path/to/browser-cache \
PREVIEW_ADMIN_PASSWORD=your-preview-only-password \
npm run test:browser
```

不设置 PREVIEW_ADMIN_PASSWORD 时跳过实际登录测试。默认产物目录 test-results/，也可通过 TEST_OUTPUT_DIR 指定；截图可能包含健康数据，不要提交到公开仓库。

回归覆盖桌面/手机八个视图、图表复用、范围切换、深链接、历史导航、键盘标签页、dialog 焦点、空档案、空数据、失败重试、组件失败降级、档案请求竞争、静态文件访问控制、匿名写保护和 CSRF 拒绝。

预览环境禁用自动同步，profiles 只读。测试登录只使用预览口令；未对真实档案执行创建、删除、重新授权或主动 Fitbit 同步。这些真实第三方写入链路没有作为本次验证的一部分重新执行。

## 发布与回滚

源码及测试审计保存在服务器 /home/lucius7/fitbaus-refactor-20260923/，源码备份不含 profiles 和 .env。项目当时没有 Git 仓库，因此没有伪造提交记录。

部署新镜像：

```sh
cd /home/lucius7/fitbaus
docker compose build fitbaus
docker compose up -d --no-deps --no-build fitbaus
curl -fsS http://127.0.0.1:9000/api/health
```

回滚到重构前镜像（不删除或覆盖 profiles）：

```sh
cd /home/lucius7/fitbaus
docker tag fitbaus-fitbaus:before-refactor-20260923 fitbaus-fitbaus:latest
docker compose up -d --no-deps --force-recreate --no-build fitbaus
```

回滚镜像仅保留在本机。旧构建没有排除 profiles，旧镜像应视为可能包含敏感数据，不应推送到公共镜像仓库。本次修复不会追溯清除旧镜像或已分发副本。

## 后续优先级

1. 确认公开健康数据的边界：默认首页、公共 API 和导出接口分别应公开哪些字段；私人部署可增加统一访问认证，公开分享可使用独立白名单响应。
2. 拆分 server.py：先移动内嵌 API 文档到模板，再按只读 API、管理员操作、同步任务拆分模块。保持响应契约和任务锁逻辑，不为拆文件而改变业务行为。
3. 建立 Git / CI：把纯函数测试、隔离浏览器回归、镜像敏感文件检查设为发布条件。锁定基础镜像 digest，并定期显式更新依赖锁，而不是永远冻结旧版本。
4. 收紧数据加载边界：按需请求多档案摘要；较大的记录表使用分页；后端同步任务后续可迁移到独立 worker。先测量请求与任务耗时，再决定是否需要任务队列或数据库。
