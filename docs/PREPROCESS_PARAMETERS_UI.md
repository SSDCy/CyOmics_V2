# PREPROCESS-PARAMETERS-UI-001 私有候选

独立、小型、默认关闭的“数据预处理参数”卡及样式，不挂正式 app、不联网、不改共享 Git/冻结候选/远程 A/服务/预览、不发布。只有参数确认，不执行过滤或 log2，不提交任务，不生成科研结果。

## 合同来源与可编辑范围

已读取 Root 指定的 `work/orchestration-round-004/log2-r-unit-prep-001/payload-files/parameters.json`，SHA256=c84e77dc7c9910238356add0f78e31874ebfa3b20ad6175dae59439c3c806e07。原字节副本位于 contract/parameters.reference.json，来源/大小/摘要见 contract/source.json。

仅可选 quant_family（Intensity 或 LFQ intensity，单选）和三个布尔 filter_flags：contaminant、reverse、site_only。三个开关初始 false，family 初始未选，必须由用户明确选择/勾选后点击确认；参考合成 unit 中三个 true 不是默认剔除指令。其余参数固定、没有可编辑输入：

```json
{"blank_policy":"to_NA","filter_flags":{"contaminant":false,"reverse":false,"site_only":false},"imputation":"none","input_scale":"raw_nonnegative","log2":"positive_only","normalization":"none","output_scale":"log2","preset":"positive_log2_zero_na_no_impute_v1","pseudocount":0,"seed":null,"steps":["flags_filter","zero_and_blank_to_NA","log2_positive","normalization_none","imputation_none"],"zero_policy":"to_NA"}
```

说明明确：原始正值用 R base::log2；原始 0、原始空值分别留痕再转 NA；不加伪计数、不填补、不标准化。Intensity/LFQ 为替代定量类型，不推断组、时间、条件或重复。本卡确认参数不等于通过数据适用性、标记列或后端权限验证。

## 最小接口

`mountPreprocessParameters(root, props)` 返回冻结 `{update(nextProps), dispose()}`。update 是完整 props 快照；不会继承旧来源或旧回调。只创建/清除自己追加的节点，不清宿主上传或其他节点。

```js
const props = {
  featureEnabled: false,
  lang: 'zh', // zh 或 en
  source: {
    contextKey: 'opaque-trusted-non-reused-revision',
    datasetVersion: {project_id, dataset_version_id, file_id},
    availableQuantFamilies: ['Intensity', 'LFQ intensity']
  },
  onConfirm: null // 可选的同步/异步参数处理回调；不是任务运行接口
};
```

调用方负责传入已经验证的 datasetVersion 和可用 family，并将 Owner/project/版本/会话变化绑定到可信、不复用的 contextKey。前端检查三个 ID 的格式及 family 白名单不能证明身份或权限。source 可接受完整 datasetVersion，但仅复制上述三个 ID，不读文件或复核数据内容。family 列表只允许这两个确切名称，无重复；空列表显示无可用类型，不猜选项；改变顺序不构成来源变化。

点击“确认预处理参数”才调用：

```js
onConfirm(Object.freeze({
  contextKey,
  source: Object.freeze({project_id, dataset_version_id, file_id}),
  quant_family: 'Intensity',
  parameters: /* 上述完整固定合同，仅三个 filter_flags 随用户选择 */
}));
```

envelope、source、parameters、filter_flags、steps 均冻结。回调返回 void/Promise；返回 DTO 或任意成功字段一律不作为任务状态。本页只在该次回调未报错且来源/选项仍相同时标注“本页参数已确认，未提交或运行任务”。无 onConfirm 时按钮禁用并明确尚未接入；错误使用通用说明，可明确重试，不自动回调或重试。

family/flags 的实际变化使旧确认永久失效，改回旧值也不会自动复活。用户可在回调等待期间修改选项，这会撤销旧回调在本卡的确认资格；调用方负责取消自己启动的工作，必须按回调内冻结来源处理，不能在异步完成时改读当前项目。source/Owner/context/family 可用列表变化清空 family、三个 flags 和旧确认；回调更换仅撤销确认，保留当前选择。语言或同一来源对象重绘保留选择和本页确认，不自动发送。禁用或 dispose 清除自己的文本/监听，旧回调不能重建界面。

## 验证边界与交付

使用原生 select、checkbox、button 和包裹 label/fieldset/legend，提供中英文、ARIA status 与可见焦点样式。不自造键盘截获逻辑。定向测试检查原生语义及 change/click、反例与迟到回调，不代表实际浏览器键盘、布局或无障碍工具已验收。

无 HTTP/文件上传/下载/文件读取/存储/计算/标准化/填补/PCA/任务结果；无新增上传入口。产品文件仅 preprocess-parameters.js、preprocess-parameters.css。测试数据为合成 UUID/上下文，不登录或读取用户实验数据。原始测试日志、new-file 补丁和文件摘要随本目录交付；Root 独立审核后再决定后端接线与集成。本单完成后 STOP。
