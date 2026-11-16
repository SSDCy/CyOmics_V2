# CyOmics V2 frontend

前端页面、原型 Worker 构建及相关开发适配代码。需要 Node.js 22+ 和 npm。

在本目录执行：

```sh
npm ci --ignore-scripts
node scripts/prepare-local-hosting.mjs
npm run build
```

本地准备脚本仅在 `.openai/hosting.json` 不存在时，从 `hosting.example.json` 创建配置；已有配置不覆盖，包括并发创建。示例不含真实 Site ID，刻意使用不可部署的占位值，只供原构建脚本复制配置。实际 `hosting.json` 和构建产物须保持 Git 忽略，不应提交。公开源码不包含真实站点身份、服务密钥或研究数据。

上述步骤只构建原型 Worker。真实认证、项目和原文件保存需要独立的 A 端 BFF/API、数据库、权限和安全运行配置；Git pull 或构建不会自动启用这些服务，也不代表完整 E2E 或独立部署可用。保留的开发适配代码含历史开发环境路径，不能当作通用运行配置直接启动。

`private-project-read-check.*` 是默认关闭的临时诊断工具。公开副本只绑定 `owner-a@example.invalid`、`owner-b@example.invalid` 和合成项目 UUID，仅供离线 fixture，不可用于现有真实账号验收；不得据此启用公开诊断路由。脚本的字节数/SHA 固定值已同步至公开副本服务器。

`tests/` 包含离线测试及需要额外环境、授权科研输入的历史测试；相关真实输入不随仓库提供。科研演示结果不等于真实分析完成，原型构建不等于生产发布。
