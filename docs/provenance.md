# 来源与迁移记录

## 项目来源

fitflare 整理自 `lucius7` 服务器 `/home/lucius7/fitbaus` 的部署源码。原远端为 [theLucius7/fitbaus](https://github.com/theLucius7/fitbaus)，GitHub 显示它是 [markrai/fitbaus](https://github.com/markrai/fitbaus) 的 fork。原始 FitBaus 作者提交以 Mark Rai 署名，后续部署改动的作者信息也予以保留。

迁移时服务器分支为 `maintenance/api-docs-20260923`，提交 `f9146f8ab7c827d5f360f382402112c80e2d10dd`。运行容器与工作树的入口、配置及依赖文件已做 SHA-256 对照。此记录只描述取材版本，不保证今后的线上状态。

## 历史整理

迁移保留原来的 30 条提交及作者、提交说明。早期 8 条提交曾引用一个在源码中硬编码的 Fitbit 客户端密钥，当前部署源码已移除此值。上传前在本地净化该历史 blob，并检查全部可达 Git 对象中不再包含该值。没有将密钥写入此记录。

净化后的部署基线为 `74bbdb9de9021b992f329ee1079bc0ea366398d0`。清理改变了提交 ID，但基线源码树与原部署提交完全一致。服务器原仓库、运行配置、授权状态和健康数据未因整理而修改。历史清理不代表旧凭据已经撤销或轮换。

新维护分支为 `main`，只发布净化后的历史；服务器旧分支和标签不直接上传。不要从原历史直接合并或强制同步，否则可能重新引入已删除的值。需要带入旧仓库改动时，应在独立环境审查补丁后应用。

`docs/history/` 保存有日期的原始改造记录，旧提交、镜像标签、服务器路径和验证结论属于当时的上下文。当前安装与维护以根目录 README、DEPLOYMENT 和 API 文档为准。

## 许可与第三方组件

继承源码没有标准 `LICENSE`、`COPYING` 或 `NOTICE` 文件，原 README 仅说明：

> This project is for personal use.

本次整理不另行授权原项目，也不将整个仓库标记为 MIT。使用和再分发时应核对原作者说明及依赖自身的许可。

`vendor/chart.umd-4.4.1.min.js` 保留 Chart.js 4.4.1 的 Chart.js Contributors 版权和 MIT 声明，也包含 `@kurkle/color` 0.3.2 的 Jukka Kurkela 版权及 MIT 声明。这些声明未被修改或移除。其余依赖的版本见 `requirements.lock` 与 `package-lock.json`。
