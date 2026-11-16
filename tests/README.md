# 测试边界

- `frontend/tests/`：前端、会话、BFF、导入预览等现有合成单元测试。
- `backend/tests/`：API 校验、事务及安全配置 reader 等现有后端测试；POSIX 文件权限用例应在适用 Linux 环境运行。
- `tests/integration/`：真实 UI 控制器与 BFF/bridge 的合成联动，网络上游由明确假对象提供。

本轮限定的公开诊断及合成联调用例不连接真实邮箱认证、PostgreSQL、科学输入文件或运行服务。历史科研输入测试可能要求 TXT_PREVIEW_INPUT / FASTA_PREVIEW_INPUT 指向另行授权的真实文件；这些文件不随库提供，也未在本轮运行。合成测试不等同于全量浏览器 E2E，不证明真实主机启动性能或最终科研结论。

公开副本用 example.invalid 邮箱及合成 UUID 替代临时人工验收账户；严禁把实际账号密码放进用例或 CI。

使用 Node 24+。前端依赖按其 package-lock 安装，详见 frontend/README。跨层用例执行命令和离线防护见 tests/support/；真实 E2E 及人工验收仍须在单独获准的测试环境中执行。

## 跨 UI/BFF 的合成集成检查

从仓库根目录执行（前端依赖已准备）：

```sh
node --experimental-loader ./tests/support/browser-import-loader.mjs --import ./tests/support/no-live-io.mjs ./tests/integration/joint-ui-bff.test.mjs
```

前置脚本阻止网络、监听、子进程、库外文件读取和写入；浏览器绝对导入仅映射到仓库内前端文件。离线 guard 的任一拒绝都会令进程失败。原型构建需先按 frontend/README 准备本地占位配置。
