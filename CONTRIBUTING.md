# 开发与维护

环境使用 Python 3.11、Node.js 24（版本写在 `.nvmrc`），安装步骤见 [README](README.md#api-与开发)。运行依赖固定在 `requirements.lock`，浏览器测试依赖固定在 `package-lock.json`。

## 提交变更

1. 从最新的 `main` 创建短期工作分支（`feat/`、`fix/`、`chore/` 或 `docs/`），说明改动的触发条件、预期行为和验证方法。
2. 先运行相关测试，提交前运行 `npm run check`，它与 GitHub Actions 使用同一入口。
3. 检查 `git diff --check`、`python3 scripts/audit.py`、`python3 scripts/audit_history.py` 和 `git status`，只提交源码、文档和配置样例。历史审计需要完整克隆，浅克隆请先执行 `git fetch --unshallow`。
4. 发起面向 `main` 的 PR。主分支要求与最新基线同步、`check` 成功并解决评审讨论；管理员也遵守此规则，不直接推送或强推 `main`。单人维护不要求额外的批准人。
5. 一般使用 squash 合并；需要保留多个有意义的提交时使用 merge commit。远端工作分支合并后自动删除，本地确认改动已进入 `main` 后清理，并执行 `git fetch --prune`。
6. CI 通过不表示已部署。只从干净且与 `origin/main` 一致的 `main` 发布，升级见 [DEPLOYMENT.md](DEPLOYMENT.md#更新与回滚)。

`npm test` 运行前端纯函数测试，`FITFLARE_DATA_ACCESS=public python3 -m unittest discover -s tests -p 'test_*.py' -v` 运行后端单元测试。完整的 `npm run check` 还会构建镜像及执行浏览器回归，需要 Docker 和已安装的 Chromium。

所有回归使用生成的 Demo/Hidden 合成档案。不要将生产 `profiles/` 挂载到测试容器，也浏览器回归只连接合成数据预览容器，不使用生产地址。

仓库启用密钥扫描和推送保护。不要绕过凭据告警；先撤销或轮换泄露值，再处理提交。历史审计额外阻止已知的旧 Fitbit 凭据对象经分支或标签重新进入仓库，但不替代通用密钥扫描。不要执行未经审查的 `git push --all`、`git push --tags` 或镜像推送。

## API 文档

接口目录源文件为 `common/api_docs.py`。修改后运行：

```bash
python3 scripts/docs.py
python3 scripts/docs.py --check
```

`API.md` 是生成结果，不要单独手工修改。涉及接口结构时一并更新契约测试和前端请求测试。

## 兼容性与数据

- 单账户是产品边界：所有界面、只读接口、命令行维护和定时任务只处理 `FITFLARE_PROFILE_ID`。测试中额外放置 Hidden 目录是为了证明隔离，不是多用户能力。
- 保持 `FITFLARE_*` 变量名与 Compose 服务名 `fitflare`、容器名 `fitflare-app` 稳定；新 API 使用 `/me`，旧带目录 ID 的 URL 仅兼容唯一账户。
- 修改数据结构前准备迁移与恢复方式，不能只依赖旧镜像回滚。
- 不提交 `.env`、授权信息、健康记录、备份、日志或带真实数据的截图；文件审计不能替代人工确认。
- 更新依赖时同步维护锁文件，并完成整套发布检查。
- 继承源码和第三方组件说明见 [来源记录](docs/provenance.md)。
