# 原始定量矩阵提取与基础统计

`backend/r/raw_qc.R` 是独立命令行源码，尚未接入网站任务或 API。
它使用 base R 和 `stats::median`，不执行过滤、填补、标准化、PCA 或差异分析。

```sh
Rscript --vanilla backend/r/raw_qc.R /authorized/immutable/input.txt 'Intensity' /authorized/new-output-directory
```

第二参数仅接受 `Intensity` 或 `LFQ intensity`。程序保留原行号、Protein IDs、原始样本列名、顺序和数值字符；空字符串仅在派生数值矩阵表示为 NA，并另存空值掩码；原始零保留为零。数值导出采用 R double 的 17 位有效数字。基础统计为每列正值、零、空值数量、数值和与正值中位数，不代表完整质量控制或有效生物学重复数。

输入须为 1 字节至 25 MiB 的 UTF-8 制表符文本，含唯一 `Protein IDs` 列；重复表头、参差行、NUL、无效编码、负数、非有限值及数值溢出/下溢均拒绝。完整表格至多 2,000,000 个数据单元格。输入路径须由调用端授权，不能直接采用用户提供的任意服务器路径。

成功输出 `raw-matrix.tsv`、`numeric-matrix.tsv`、`blank-mask.tsv`、`zero-mask.tsv`、`sample-qc.tsv`、`summary.tsv` 和 `R-session-info.txt`。输出目录必须尚不存在。失败可能留下不完整输出；调用端必须在隔离临时目录运行，仅在进程成功退出、输出校验通过后原子发布，失败目录不可作为有效报告。

未来 Worker 必须先核实项目权限、固定输入摘要、绑定数据与任务版本，并在执行前后复核摘要；由进程外部实施超时、CPU、内存、每文件及累计输出限制。此脚本自身不提供用户隔离、任务调度或资源沙箱。

便携合成测试：

```sh
python backend/tests/test_raw_qc.py --rscript /usr/bin/Rscript --script backend/r/raw_qc.R --temp-root /authorized/test-temp
```

已保留 R 4.6.1 下 15 个合成用例和两份获授权真实 TXT、两种定量类型的四次计算证据。独立复核核对了 519,744 个选定单元格及 132 列的和与中位数；这些证据不等同于网站 E2E、Worker 完成或所有 R 版本兼容。原始研究文件、导出矩阵、私有运行路径及登录信息均不进入公开仓库。
