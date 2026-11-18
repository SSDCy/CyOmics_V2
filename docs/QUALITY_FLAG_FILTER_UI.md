# PREPROCESS-FILTER-UI-001 接口与验收边界

仅私有前端源码候选，默认关闭。新增质量标记过滤面板、独立样式和合成交互测试；不改共享 Git、现有 app、上传入口、预览、认证、后端或 API，不部署。沿用蓝白紧凑面板风格。此次不执行科研过滤，不生成结果或演示统计。

## 最小受控接口（先定义，再实现）

`mountQualityFlagFilter(root, props)` 返回 `{update(nextProps), dispose()}`。root 是宿主专用挂载槽；只追加/清除自身节点，不替换宿主其他节点。update 接收完整 props 快照，不做增量合并。默认 featureEnabled=false 时不挂载面板、不触发回调。

```js
const props = {
  featureEnabled: false,
  lang: 'zh',                         // 'zh' | 'en'
  contextKey: null,                   // 可信宿主提供；绑定 Owner/project/源版本/数据类型/上下文修订
  sourceVersion: null,                // 来源版本标识；显示为纯文本
  quantFamily: null,                  // 严格 'Intensity' | 'LFQ'
  flags: {
    reverse: false,
    potentialContaminant: false,
    onlyIdentifiedBySite: false
  },
  availableFlags: {
    reverse: null,                    // true=列存在，false=明确缺列，null/未给=尚未确认
    potentialContaminant: null,
    onlyIdentifiedBySite: null
  },
  busy: false,
  result: null,
  onChange: null,                     // 可选：接收冻结 request；宿主主动 update 后才改变受控值
  onApply: null                       // 可选：接收冻结 request，返回 void 或 Promise<void>
};
```

request 只有 `{contextKey,sourceVersion,quantFamily,flags}`，flags 恰好三个布尔字段。省略的输入开关默认 false；显式非布尔配置阻断应用，不转换为 true/false。不存在默认剔除。缺少 onChange 时开关只读；缺少 onApply 时“应用过滤”禁用并显示“过滤服务尚未接入”。没有可用列、已选列缺失/未确认、来源信息不完整也阻断应用。已有可用列时允许三个开关全 false 的显式请求，不推断用户意图。

onApply 仅由明确点击触发。同步防重入及本地 pending 与 props.busy、服务端 queued/running 共同防重复点击。返回值不被当成结果、计数或成功信号：即使返回伪造 succeeded DTO，也只显示等待宿主更新。回调异常使用通用提示，允许用户核对任务状态后明确重试；没有自动重试、轮询或后台触发。onChange 为同步通知；误返 Promise 的拒绝被收敛为通用设置错误。

宿主必须在真实提交、接收任务及恢复未知状态期间保持 busy=true，并自行提供请求幂等与任务去重。onApply Promise 结束只表示宿主回调结束；本地 pending 随之释放，不能据此认定服务器已接受任务。没有宿主 busy/任务状态约束时，本面板不保证两个不同明确点击之间的网络任务去重。此限制不会由前端生成假任务或假成功来掩盖。

## 后端结果显示 DTO（由宿主适配，不规定 HTTP 路由）

```js
const result = {
  contextKey, sourceVersion, quantFamily,
  flags: {reverse, potentialContaminant, onlyIdentifiedBySite},
  status: 'succeeded', // 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown'
  counts: {before: null, after: null, removed: null},
  reasonCounts: {reverse: null, potentialContaminant: null, onlyIdentifiedBySite: null},
  overlapCount: null
};
```

contextKey、来源版本、类型、三个 flags 必须与当前受控输入逐一相同，才显示该结果；缺失绑定不显示。只有 status=succeeded 时显示计数，其他状态全部待计算。计数只接受宿主给出的非负安全整数，0 原样显示；null、缺失、负数、字符串、NaN 等显示“待计算”。不从 before-after 计算 removed，不相加原因，不推算重叠。

reasonCounts 指后端按当前选择统计的各标记剔除命中行数，可能重叠；overlapCount 指后端给出的同时命中至少两项已选过滤标记的行数。宿主须按真实后端定义适配，不能把原始标记总量冒充本轮剔除计数；若后端未提供相同语义则保持 null。此最小 DTO 是前端接入提案，不声称后端已有同名字段。

Root 已确认已审 R summary 到未来宿主 DTO 的映射：before=input_rows，after=kept_rows，removed=excluded_rows；reasonCounts.reverse=excluded_reverse、potentialContaminant=excluded_contaminant、onlyIdentifiedBySite=excluded_site_only；overlapCount=overlap_excluded_rows。三个原因仅统计本次启用规则命中，重叠行分别计入各原因；overlap_excluded_rows 是至少两项启用规则命中、每行只计一次。此候选不解析 R 文件，不自行执行这些映射；正式宿主适配另审。

## 上下文、所有权及限制

宿主必须提供当前可信且不复用修订的 contextKey，并在 Owner/project/源版本/Intensity-LFQ/会话切换时立即 update 或 dispose。面板以 contextKey+sourceVersion+quantFamily 和开关快照隔离本地回调；不同上下文或开关的迟到回调不会改变当前反馈，返回的 DTO 一律不进入结果。宿主在把异步响应转成 props.result 前仍必须校验响应所属上下文。复用完全相同 key 的未观察 A→B→A 无法由前端推断，不能把本模块当 JWT/Owner/项目权限或会话校验。

同绑定下纯语言重绘不触发应用或更改开关。来源变化立刻重置本地 pending/错误，旧 DTO 绑定不符时清空结果。旧请求本身由宿主取消；此面板不会发出或取消网络任务。dispose 清除自己的私有文本、节点和监听，迟到回调不恢复节点。

没有 HTTP、文件读取、上传、下载、存储、浏览器过滤、统计、分组推断、analysis_ready、PCA、标准化、填补或差异分析。没有修改原始文件/原始预览；没有新增输入文件或样本时间字段。用户文本只用 textContent，禁止 innerHTML。来源版本属于显示元数据，contextKey 不展示。

## 测试与交付

测试使用定向的合成 DOM 事件模型与内存 DTO；不是实际浏览器、Owner API 或真实 R 端到端验收。运行命令、完整日志、文件摘要及结果见最终 receipt.json 和 source-manifest.json。Root 独立审查后再决定接线；本单交付后 STOP。

最终：模块及测试语法检查通过，54 项合成交互测试全部通过，五类离线 guard 计数均为 0。未做浏览器布局和无障碍工具审计；响应式样式仍须由 Root 集成时实际预览复核。初次命令因工作目录相对路径错误未能加载 guard，未运行测试；纠正后首轮 50 项通过，补充四项重入与迟到回调检查后最终 54 项通过，原始日志保留。


## Root source integration: 2026-11-18

The module and isolated DOM tests are integrated as default-off source. No
existing app/build/route/upload selector is modified or mounted. Independent
source QA ran the original54 and independent6 pure DOM cases, not a browser.
The host must keep busy through submission and unknown recovery and supply
server idempotency. Updating unselected availability without host busy can
clear local pending and permit a second deliberate callback; this module is
not a global exactly-once job boundary.

Before live integration, the host adapter must explicitly map the UI label
`LFQ` to the backend/R canonical family `LFQ intensity` (Intensity maps to
Intensity). Preserve the canonical family in server lineage and a fresh host
context revision; map it consistently into displayed result bindings. Do not
send `LFQ` as an unreviewed alias to a strict scientific backend. The R summary
mapping above supplies values, not a client-side calculation or proof that a
real job ran. Source switching and unknown states must withhold old results.

Real browser layout, accessibility, host wiring and Owner/API/R end-to-end
validation remain pending. Source QA receipt SHA256:
`8ad9cd51a0d7a737356a31544c72ce51deace9b2e2be6a2b35d010c75eb59cf8`.
