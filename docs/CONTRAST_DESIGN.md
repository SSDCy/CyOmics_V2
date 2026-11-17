# 比较设计草稿

本片段提供前端模型/编辑器、私人 TXT 页面中的折叠入口，以及后端纯校验模型。比较草稿仅保留在本页内存，尚未接入保存 API、数据库或统计任务；运行版本更新与真实账号验收另行验证。

来源必须是调用方完成项目授权后取得的已保存样本设计，绑定原始导入列、数据版本、定量类型、样本设计版本与规范 SHA256。摘要验证不能替代身份认证或证明记录真实存在。来源切换时清除旧草稿；不根据样本名推断实验设计，不自动确认样本。

每行由用户选择分子组和分母组。`FC = 分子/分母`、`log2FC = log2(FC)` 是方向定义，不是已计算结果。相同有向比较不重复，反向比较可独立存在；组名保留原文、大小写、Unicode 拼写和首次出现顺序，最多 32 行。

空草稿或任一未完成行均阻断后续方法选择。所有来源行显式确认、样本审查通过，且所选组对照标记一致、使用生物学重复、重复编号非空且组内唯一时，才可能进入 `ready_for_method_selection`。技术、混合或未知重复需要后续明确策略。此状态不证明有效样本量或统计方法适用，`analysis_ready` 始终为 false。

前端入口为 `createContrastDesignDraft`、`reviewContrastDraft` 和 `mountContrastDesignEditor`。编辑器卸载只销毁视图；调用方退出私人上下文时必须解绑或销毁模型，并处理语言重挂和来源变更。后端 `cyomics_projects.contrast_design` 复用未修改的既有样本校验器，调用方仍负责用户授权和已保存版本真实性。

既有样本客户端的 `savedRecord(family)` 和宿主的 `comparisonSource()` 提供只读已保存来源快照。只有当前来源、完整验证的保存/载入记录、来源 epoch、head ID 与干净草稿内容一致时才返回来源；忙碌、排队、未保存编辑、未知写入或失效上下文均不能作为比较依据。读取不触发请求，不自动确认，也不能代替授权。私人 TXT 控制器持有比较宿主，来源变化或失效、退出及销毁会清除比较；相同来源的语言或 DOM 重挂保留草稿。

合同、澄清位于 `docs/contracts/contrast-*.json`，共同合成输入位于 `tests/fixtures/contrast-common.synthetic.json`。

```sh
node --import ./tests/support/no-live-io.mjs frontend/tests/contrast-design.test.mjs
python -B -m unittest discover -s backend/tests -p test_contrast_design.py -v
```

前端 24 个合成模型/DOM 替身用例、后端 16 个纯模型用例及独立 22 个跨语言案例已通过。测试数量不代表页面、存储、权限 E2E 或科研分析验收通过；本片段不输出 FC、P 值、FDR、图表或“运行分析”按钮。
