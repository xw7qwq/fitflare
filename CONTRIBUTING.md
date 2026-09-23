# 开发与维护

环境使用 Python 3.11、Node.js 24，安装步骤见 [README](README.md#开发与检查)。运行依赖固定在 `requirements.lock`，浏览器测试依赖固定在 `package-lock.json`。

## 提交变更

1. 从 `main` 创建工作分支，说明改动的触发条件、预期行为和验证方法。
2. 先运行相关测试，提交前运行 `npm run check`，它与 GitHub Actions 使用同一入口。
3. 检查 `git diff --check`、`python3 scripts/audit.py` 和 `git status`，只提交源码、文档和配置样例。
4. 合并前检查 Actions 结果；CI 通过不表示已部署。升级见 [DEPLOYMENT.md](DEPLOYMENT.md#更新与回滚)。

`npm test` 运行前端纯函数测试，`python3 -m unittest discover -s tests -p 'test_*.py' -v` 运行后端单元测试。完整的 `npm run check` 还会构建镜像及执行浏览器回归，需要 Docker 和已安装的 Chromium。

所有回归使用生成的 Demo/Hidden 合成档案。不要将生产 `profiles/` 挂载到测试容器，也不要让浏览器回归对生产地址执行管理操作。

## API 文档

接口目录源文件为 `common/api_docs.py`。修改后运行：

```bash
python3 scripts/docs.py
python3 scripts/docs.py --check
```

`API.md` 是生成结果，不要单独手工修改。涉及接口结构时一并更新契约测试和前端请求测试。

## 兼容性与数据

- 单账户是产品边界：所有界面、读写接口和定时任务只处理 `FITFLARE_PROFILE_ID`。测试中额外放置 Hidden 目录是为了证明隔离，不是多用户能力。
- 保持 `FITBAUS_*` 和 Compose 部署名称兼容；新 API 使用 `/me`，旧带目录 ID 的 URL 仅兼容唯一账户。
- 修改数据结构前准备迁移与恢复方式，不能只依赖旧镜像回滚。
- 不提交 `.env`、授权信息、健康记录、备份、日志或带真实数据的截图；文件审计不能替代人工确认。
- 更新依赖时同步维护锁文件，并完成整套发布检查。
- 继承源码和第三方组件说明见 [来源记录](docs/provenance.md)。
