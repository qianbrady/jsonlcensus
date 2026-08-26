# jsonlcensus — JSONL 字段普查器

**Streaming JSONL schema census: field occurrence, type mix, null rate,
nesting depth and drift — one command to a Markdown report.**
纯标准库，内存恒定，支持 GB 级文件。

jsonlcensus 对大型 JSONL 文件逐行流式解析，为每个字段统计
**出现率 / 类型分布 / 空值率 / 最大嵌套深度 / 截断示例值**，
一条命令输出 Markdown 画像报表；`drift` 子命令按文件顺序分桶，
检测 **schema 漂移**（字段新增 / 消失 / 类型变化 / 出现率偏移）。

## 特性

- 纯标准库，Python ≥ 3.10，零第三方依赖
- 流式逐行解析：内存恒定 O(字段数)，GB 级文件无压力
- 坏行计数不中断，报告附跳过行号；空白行、非对象行分别计数
- 输出确定性：同文件同参数 → 字节级一致
- 嵌套路径点分记法（`a.b.c`），数组元素用 `[]` 标记（`users[].id`）
- 递归深度超过 50 的字段标记「过深」，不再下钻
- stdio 强制 UTF-8，GBK 控制台不崩
- 退出码 0 / 1 / 2：成功 / 数据错误 / 用法错误

## 安装

无需安装即可使用：仓库根目录 `python -m jsonlcensus ...`；
也可以 `pip install .` 获得 `jsonlcensus` 命令入口。

## 用法

### profile —— 全量字段画像

```bash
python -m jsonlcensus profile data.jsonl
python -m jsonlcensus profile data.jsonl --limit 1000   # 只扫前 1000 行
```

### drift —— 分桶对比 schema 漂移

```bash
python -m jsonlcensus drift data.jsonl                 # --buckets 2：前半 vs 后半
python -m jsonlcensus drift data.jsonl --buckets 4      # 四分桶
python -m jsonlcensus drift data.jsonl --min-delta 5    # 出现率变化阈值（百分点）
```

判定规则：出现率 0 → 非 0 为「新增」；非 0 → 0 为「消失」；
出现率偏移 ≥ `--min-delta`（默认 10.0 个百分点）或类型分布变化为「漂移」；
否则「稳定」。

## 语义约定

- **字段路径**：点分记法；键名本身含 `.` 时路径有歧义（工具按字面拼接）
- **数组**：容器字段记为 `array` 类型；元素经 `[]` 伪字段展开（`tags[]`、
  `users[].id`）；空数组不产生元素字段
- **出现率** = 字段出现的行数 ÷ 有效对象行数（显式 `null` 也算出现）
- **空值率** = 显式 `null` 的行数 ÷ 有效对象行数
- **类型分布**：按行去重计次（一行内同字段多种类型各计 1 次）
- **示例值**：每字段保留前 5 个去重值，紧凑 JSON 渲染，截断至 24 字符
- **过深**：路径深度 > 50 的字段标记「（过深）」，不再下钻
- 非对象行（裸数字 / 裸数组等）不进入字段统计

## 退出码

| 码 | 含义 |
| -- | -- |
| 0 | 成功（含空文件画像、含坏行的画像） |
| 1 | 数据错误：文件不存在 / 是目录 / 读取失败；drift 空文件无法分桶 |
| 2 | 用法错误：缺参数、非法选项、`--buckets < 2`、`--limit < 1` |

## 与 jq / csvkit 的对比

csvkit 官方 README 自述：*"csvkit is a suite of command-line tools for
converting to and working with CSV, the king of tabular file formats."*

| 维度 | jsonlcensus | jq | csvkit |
| --- | --- | --- | --- |
| 输入 | JSONL（逐行 JSON 对象流） | 单条 JSON / 流式变换 | CSV 等表格 |
| 定位 | 全量 schema 画像 + 漂移检测 | 单条记录的查询与变换 | CSV 转换、清洗、统计 |
| 输出 | 一行命令直接出 Markdown 画像 / 漂移报表 | 变换后的 JSON 数据 | CSV / 统计表 |
| 坏行容错 | 计数不中断，继续扫描 | 遇错即退出 | 工具各异 |
| schema 漂移检测 | 内置（分桶对比） | 无 | 无（弱类型 CSV） |
| 内存模型 | 流式 O(字段数)，GB 级无压力 | 单条记录在内存 | 视工具而定 |

一句话：**jq 是单条变换，csvkit 面向 CSV，而 jsonlcensus 专注 JSONL
全量画像与前后漂移，一行命令出 Markdown 报表。**

## 开发

```bash
python -m unittest discover -s tests -q    # 全量测试
```

工程铁律：

- 临时产物只允许放在工作区 `.build-tmp/`（已加入 .gitignore），不污染仓库
- CLI 启动即 reconfigure stdio 为 UTF-8
- 输出完全确定：字段按键排序、示例按首次出现顺序、无随机、无时戳
- CI：GitHub Actions，ubuntu + windows × Python 3.10 / 3.12

## License

MIT © 2025 ox-alpha