# RAGFlow 知识库：入库与测试操作手册（P1 / P1.5）

> 面向教授侧正式部署与本地验证。配套脚本：`scripts/ragflow_bootstrap.py`（建号/取 Key/建库）、
> `scripts/ragflow_ingest.py`（批量入库，可续跑）、`scripts/ragflow_ops.py`（上传/解析/检索试跑）。
> RAGFlow 本身的部署与踩坑见 `D:\AI\RAGFlow\README.md`。

## 0. 顺序总览

```
① 语料体检（离线，不占 RAGFlow）
② 去重与排坏 + 生成入库清单
③ 试切小批（10 篇内，测速 + 查质）      ← 别跳过，配置错了要重跑全库
④ 全量入库（可中断续跑，按论文/专著分库）
⑤ 抽检：检索命中核对 + 端到端跑流水线
```

## 1. 语料体检（已完成，2026-09-22）

产出：`knowledge/corpus_report.md` / `.json`（逐篇页数、是否扫描、体积）。

本批 246 个 PDF 的结论：

| 项 | 数值 |
|---|---|
| 有文字层 | 211 篇 / 8595 页 |
| 扫描版（需 DeepDoc OCR，慢） | 35 篇 / 5287 页 |
| **内容重复（md5 相同）** | **58 个** |
| **损坏/不可用文件** | **3 个**（已移入 `references/_broken/`） |

### 1.1 坏文件与缺失文献（2026-09-22 复核）

`references/` 已按用途分子目录，**只移动、未删除**：

- `_broken/`：3 个不可用文件
  - `14_Ferrell_1969_Taiwan_Aboriginal_Groups.pdf`——21MB 但 **0 页**（文件损坏）
  - `Pawley_Green_Chapter3.pdf`（**13KB**）、`Pawley_Prehistory_Oceanic_Languages.pdf`（**42KB**）
    ——**截断的下载残留**（体积远小于正常论文），非空文件
- `_duplicates/`：与保留件**字节相同**的冗余副本（如 `5_Grace_1964_…`）

**缺失、需教授提供**（均为付费/纸本，无合法免费源，已核实）：

| 文献 | 状态 |
|---|---|
| Ferrell, Raleigh. 1969. *Taiwan Aboriginal Groups: Problems in Cultural and Linguistic Classification*. 中央研究院民族學研究所專刊 17（444 页） | 只有纸本（NT$400，三民/萬卷樓/五南）；Sinica IR 无全文 |
| Grace, G. W. 1961. Austronesian Linguistics and Culture History. *American Anthropologist* 63(2): 359-368. doi:10.1525/aa.1961.63.2.02a00070 | Wiley 付费墙（直下 403） |
| Grace, G. W. 1964. The Linguistic Evidence. *Current Anthropology* 5(5): 361-368. doi:10.1086/200527 | University of Chicago Press 付费墙 |
| `Pawley_Green_Chapter3.pdf` 的原始目标 | **疑似即**《The Austronesians》第 3 章（Pawley & **Ross**，pp. 43-80）——原文件名把合著者写成 Green；该章已从 ANU Press 开放获取下载并入库。若教授本意是另一篇 Pawley & Green 作品，需另行提供 |

### 1.2 本次补齐的文献（已下载入库）

- ✅ `Pawley_Ross_1995_Prehistory_of_Oceanic_Languages_Austronesians_ch3.pdf`（38 页）
  ——《The Austronesians: Historical and Comparative Perspectives》第 3 章，ANU Press 开放获取
  （https://press.anu.edu.au/publications/series/comparative-austronesian/austronesians）

### 1.3 文件名与内容不符的更正

- `3_Grace_1961_Austronesian_Linguistics_and_Culture_History.pdf` 的实际内容是
  **Barker, Milton E. 1962. Topical Report 1. Linguistics. *Asian Perspectives* 6**（9 页），
  与文件名完全不符 → 已按「**以 PDF 自身标题为准**」改名为
  `Barker_1962_Topical_Report_1_Linguistics_Asian_Perspectives.pdf`
  （RAGFlow 库内文档名同步更正）。
- 另有一份真实文献 `Grace_Central_Eastern_Oceanic.pdf`（1.85MB / 22 页）保持原名。

> 教训：**入库前用 PDF 首页文字核对文件名**，别信文件名的表面意思——本批就有一份
> 名字是 Grace、内容是 Barker 的情况（而且被复制给了两个不同的文件名）。

## 2. 入库清单的生成规则

`knowledge/ingest_manifest.json`：**185 篇 / 10530 页**

- `papers`（≤150 页，`paper` 切片）：164 篇 / 2984 页 → dataset「考古文献库」
- `books`（>150 页，`book` 切片）：21 篇 / 7546 页 → dataset「考古专著」
- `skipped_duplicates`：58 个重复文件（**只入库一份，原文件保留未删**）
- `broken`：3 个坏文件（已排除）

去重保留优先级：带 `[N]` 编号的 > 无临时名（`*temp*`）的 > 名称更完整的。
**注意**：`3_Grace_1961_...` 与 `5_Grace_1964_...` 内容 md5 相同但分属两篇不同论文，
说明其中一个是拿错文件——需人工确认。

## 3. 试切小批（已完成）

```bash
python scripts/ragflow_ingest.py --group trial --wait
```

实测结论（本机 8 核 / 32GB / bge-m3）：

- `paper` 切片对中文发掘报告的产出约 **2-3 chunk/页**；扫描件走 OCR 正常（31 页 → 109 chunk）
- **耗时瓶颈是嵌入而非解析**：TEI 单条 4.5s，批量 16 条时 0.87s/chunk
- `book` 切片已建库验证（专著用）

## 4. 全量入库

```bash
# 论文组
python scripts/ragflow_ingest.py --group papers --wait --interval 60

# 专著组（21 篇 / 7546 页，最慢，建议夜里跑；分波提交避免打爆嵌入服务）
for i in 1 2 3 4 5 6; do python scripts/ragflow_ingest.py --group books --wave 5 --wait --interval 60; done

# 只看进度（随时可查，可中断后续跑）
python scripts/ragflow_ingest.py --group all --status-only
```

脚本特性：按「库内已存在的文件名」去重 → 重复执行只补缺失；打印按页数估算的 ETA。
**中断安全**：直接 Ctrl-C 或超时被杀都不影响服务端解析。

常用开关：

| 开关 | 用途 |
|---|---|
| `--wave N` | 本次最多触发 N 篇解析，其余留待下次。**首次入库或大部头务必分波**，一次性提交几百篇会把 TEI 打爆（排队 >30s 触发读超时失败） |
| `--retry-fail` | 只重试 `FAIL` 文档，不动 `UNSTART`——避免把已排队的文档重复提交 |
| `--include-running` | 把停在 `RUNNING` 的文档也重新触发（**执行器停摆或容器重启后必用**，否则这些文档永远卡着） |
| `--auto-restart` | 检测到执行器停摆且补触发无效时，自动重启 RAGFlow 容器（无人值守推荐） |
| `--stall-minutes N` | 连续 N 分钟零完成即判定停摆（默认 15） |
| `--upload-only` | 只上传不解析（先把文件送进库，等闲时再触发） |
| `--status-only` | 只看进度 |
| `--interval` / `--timeout` | 轮询间隔 / 等待上限 |

> **退出条件说明**：`--wait` 以「连续两次采样无文档在跑」为结束条件（而不是"没有待解析文档"），
> 因此 `--wave` 分波时不会因为剩下未触发的文档而永远等待；结束时它会明确提示还有多少篇未触发。

### 实测吞吐与 ETA（本机 8 核 / 32GB / bge-m3 / 2 并发 worker）

| 阶段 | 实测 | ETA |
|---|---|---|
| 论文组（164 篇 / 2984 页） | **1.4 篇/分钟** | ≈ 1.8 小时 |
| 专著组（21 篇 / 7546 页，多扫描大部头） | 按页数外推 | ≈ 4-6 小时 |

### ⚠️ 一次性提交过多文档会打爆嵌入服务（已实测踩到）

一次性提交 159 篇后，RAGFlow 立刻并发拉起 40+ 个解析任务，**TEI 排队时间飙到 16 秒**，
超过 RAGFlow 侧的 **30 秒读超时** → 已有 2 篇以
`HTTPConnectionPool(host='tei', port=80): Read timed out` 失败（重跑即会重试）。

**对策**：首次入库不要一次提交几百篇；按 20-30 篇一批提交并等前一批跑完
（或分日导入）。另注意 TEI 在 CPU 上只有一个模型实例、推理是串行的，
请求侧并发过高只会互相拖慢。

### 提速杠杆（三选一或组合，**换嵌入模型要重跑全库**）

| 杠杆 | 手段 | 预期 |
|---|---|---|
| 嵌入模型 | `.env` 里 `TEI_MODEL=${TEI_MODEL:-Qwen/Qwen3-Embedding-0.6B}`（镜像内 1.2GB） | 参数少 7 倍，嵌入显著变快，且**省约 12GB 内存**（可同时调高并发） |
| chunk 大小 | dataset 的 `parser_config.chunk_token_num` 调大 | chunk 数下降，嵌入量等比下降；检索粒度变粗 |
| 解析并发 | `docker/service_conf.yaml.template` 的 `ingestor.max_concurrent_workers`（默认 2） | 需先腾出内存，否则 OOM |

## 5. 抽检与端到端测试

```bash
# ① 连通性（含 RAGFlow 探测项）
python main.py --check

# ② 只测检索通路，不调语言/绘图模型（零费用）
python main.py --dry-run --mode rag

# ③ 完整闭环（需求 → 检索 → 知识(带引用) → 绘图 → 审稿 → 报告）
python main.py --mode rag --max-attempts 1

# ④ 单点检索抽查（换关键词，人工判断命中是否对题）
python scripts/ragflow_ops.py retrieve "昙石山遗址的贝壳堆积与年代"
```

判读要点：

- **报告里看三样**：`知识层模式: rag`、`检索词:`（模型拆的检索词是否覆盖器物全称/简称/形制）、
  `引用文献列表`（每条带页码）
- **知识摘要**里带 `[文献 p.X]` 标注的行占比越高越好；若大量断言无出处 → 检索命中不足
- **页码必须与原文对得上**（页码取自 `positions`，0 基 +1，已在客户端处理）

## 6. 常见失败与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| 解析报 `No default embedding model is set.` | 租户默认嵌入未设 | `python scripts/ragflow_ops.py set-defaults --embd "BAAI/bge-m3"` |
| 检索报 `Provider  not found for model .` | dataset 的 `embd_id` 为空 | `python scripts/ragflow_ops.py set-dataset --embd "BAAI/bge-m3@Builtin" --name <库名>` |
| 解析报 `file type not supported yet(pdf supported)` | `paper` 切片只支持 PDF | DOCX/TXT 改写/转 PDF（或另建 `naive` 库） |
| 某篇解析 `FAIL`，`progress_msg` 报 `tei ... Read timed out` | 并发过高把嵌入服务打爆 | 重跑（`--retry-fail`）即可；下次用 `--wave` 降并发 |
| 触发解析接口报 `Internal server error` | 文档多/负载高时接口偶发 500 | 脚本已自动小批量 + 退避重试；仍失败会在空闲时补触发 |
| **触发解析**持续报 `Internal server error`（code=102，重试 3 次全挂，与负载无关） | **该 PDF 文件损坏**——`pdfminer: No /Root object!`（下载截断/伪装成 PDF，特征：页数明显不对，如 1–2 页的 Nature 论文）。服务端 `queue_tasks` 先算页数，坏文件直接 500 | 本地用 PyMuPDF 体检（`is_pdf` + 页数对不对）→ 坏文件移 `references/_broken/` → 删库内记录 → 教授重下后重传。**2026-09-24 实测：11 篇全是这类；脚本曾对它们一夜重启容器 12 次全是空转**（现已加固：补触发全失败即标记跳过） |
| **★ 进度长时间不涨，多篇卡在 `RUNNING` 且进度 0%** | **task_executor 静默停摆** | 见下方专节 |
| 检索结果全是同一篇 | 语料集中在少数文献 | 正常；按 §5.1 建回归集再调 chunk/阈值 |
| 检索命中率低 | 术语不一致（"昙石山" vs "曇石山"） | 建术语/同义词表（RAGFlow 术语重写），或让查询规划器多出几组词 |

### ★ 高危故障：task_executor 静默停摆（2026-09-23 实测，白等 5.7 小时）

**症状**（四条同时出现才可判定）：

1. 文档状态大面积 `RUNNING`，但 `progress` 恒为 0%、`chunk_count=0`、`process_duration=0`
2. `docker stats` 里 ragflow 容器 CPU **接近 0**（宿主也几乎空闲）
3. `/ragflow-logs/task_executor_*.log` 的 **最后修改时间停住**（不再写新行）
4. 容器内 `task_executor.py` **进程还在**（`State: S`、`wchan: do_epoll_wait`）——看不出异常

**诱因**：解析过程中累计多次 `tei ... Read timed out`（实测 45 次）之后，执行器的任务消费
循环静默停掉，**不会自愈、不会重试、日志无报错**。

**处置**：

```powershell
docker restart docker-ragflow-cpu-1                      # 复活执行器
python scripts\ragflow_ingest.py --group papers --include-running --wait --interval 60   # 重触发卡住的文档
```

**预防**：无人值守时务必加 `--auto-restart --stall-minutes 15`——脚本检测到
「连续 15 分钟零完成且仍有 RUNNING」会先补触发，无效则自动重启容器。
同时**降低瞬时并发**（用 `--wave` 分波），从源头减少 TEI 超时。

## 7. 云 embedding 迁移（推荐路径：先试验、后切换）

文章 `ragflow-memory-optimization` 给出的方向是对的：把 embedding 从本地 TEI 挪到云 provider，可以省掉本地常驻模型和一部分 WSL 内存。但这不是“换一个环境变量”这么简单：embedding 模型一旦变化，旧向量和新向量不在同一空间，已有文档必须重新解析/向量化。

本项目采用下面的安全切换策略：

1. **保留正式库**：正式 dataset 继续使用当前 `BAAI/bge-m3@Builtin`，不在生产库上直接改模型。
2. **在 RAGFlow 模型供应商中配置云 embedding**：完整模型引用按 RAGFlow 实际登记值填写（通常是 `model@instance@provider`），API key 只留在 RAGFlow 的 provider 配置中，不写入 Painter 日志和报告。
3. **新建试验 dataset**：使用与正式库相同的 chunk 方法；论文库用 `paper`，专著库用 `book`。扫描版仍走 DeepDoc OCR，不因省内存把全部资料强行改成 `naive`。
4. **只上传 1–3 篇代表文献并解析**：至少覆盖一篇文字层论文、一篇扫描件或图版较多的文献；确认解析成功、向量生成成功、检索能命中正确文献与页码。
5. **跑检索回归集**：用 `docs/RAGFLOW_ARCHITECTURE.md §5.1` 的典型需求比较旧库与试验库，至少记录 top-3/top-5 命中率、最高相似度、页码正确率和单次检索耗时。
6. **回归通过后再分批迁移**：正式库不能在切换后“继续沿用旧向量”；要么全量重解析，要么新建云 embedding 正式库并在验收完成后切换 Painter 的 dataset ID。两种方式都要保留旧库作为回退。

Painter 侧的安全约束：

- `.env` 的 `RAGFLOW_EMBEDDING_REF` 只是迁移目标记录，供 `scripts/ragflow_ops.py health --embedding-ref ...` 校验；**不会自动切换 dataset，也不会在每次检索时覆盖 RAGFlow 配置**。
- `ragflow_client.py` 只对 GET 和 `/retrieval` 的连接异常、429、502、503、504 做有限退避；上传和解析 POST 不重试，避免重复副作用。
- `scripts/ragflow_ops.py wait` 遇到 `FAIL` 文档返回非零；“没有 RUNNING”不再被误报成“全部成功”。
- 云 provider 不可达时，`--mode rag` 明确失败并留下诊断；不会无提示混入 `local` 文献，避免破坏引用可溯源性。

### 实测结论（2026-09-26：云 embedding 已在本机跑通）

| 项 | 实测结果 |
|---|---|
| provider / 实例 / 模型 | `ZHIPU-AI` / `zhipu-main` / `embedding-3` |
| 建库用的完整引用 | `embedding-3@zhipu-main@ZHIPU-AI` |
| 文字层 txt（23KB） | 16 chunks，解析 **5.5s**（其中 embedding 0.79s） |
| 图版 PDF（2 页） | OCR 1.32s + layout 1.05s，检索命中 **p.2 内容正确** |
| 检索相似度 | 0.56–0.79（同一试验库，查询越聚焦越高） |
| 整栈内存 | **约 8.4GB**（无 TEI）；此前带 TEI 约 20.5GB |

试验 dataset（回归用，勿当生产库）：`1f897170b8fb11f1a79dd9ed4560b8ba`（云嵌入试验-embedding3）

一键配置（幂等，只登记 + 验证，不切库、不重解析）：

```powershell
D:\AI\Painter\.venv\Scripts\python.exe D:\AI\Painter\scripts\ragflow_ops.py embedding-init
```

> 读 `.env` 的 `ZHIPU_API_KEY`；重复执行会复用已存在的 provider 与实例。

**⚠️ 关掉本地 TEI 必须同时改 `.env`**：编辑 `D:\AI\RAGFlow\ragflow\docker\.env`，
把 `COMPOSE_PROFILES` 末尾的 `,tei-cpu` 去掉并重启栈。

只停容器、不改这一行是不够的：RAGFlow 靠**容器内** `COMPOSE_PROFILES` 是否含 `tei-`
来决定 `bge-m3@Builtin` 走本地 TEI 还是走 provider。不改的话报错是
`HTTPConnectionPool(host='tei', port=80)` 这种 DNS 失败，而不是明确的模型错误，很误导。

> **⚠️ 这段估算已被 2026-09-26 实测推翻（针对"换成 bge-m3"这一路线）**：
> 实测证明本地 TEI 与云端 `BAAI/bge-m3` 向量**数值等价**，切换只需改 dataset 的
> `embedding_model` 引用，**不重解析、约 1 分钟、费用 0**。见架构文档 §11.5。
> 只有**换成不同模型**（如 embedding-3）时，下面这张表的重解析成本才成立。

**全量迁移成本（换成不同模型时：现网 187 篇 / 12892 chunks）**

| 维度 | 估算 |
|---|---|
| 费用 | 约 660 万 tokens × 0.5 元/百万（智谱官方价；Batch API 0.25）≈ **3 元量级** |
| 时间 | 瓶颈是本地 CPU 的 DeepDoc 解析/OCR，云端 embedding 只占零点几秒；按历史吞吐（论文组 1.4 篇/分钟、专著组按页数外推）≈ **5–8 小时** |
| 主要风险 | 云端有 RPM 限流。仍要**分批 20–30 篇**提交，失败用 `status --verbose` 看原因后重跑 |

也就是说：迁移的钱几乎可以忽略，**代价是几个小时的机器时间和一次不可逆的向量空间切换**，
所以仍然坚持"先试验库回归、再全量"的顺序。

### 7.1 云 embedding 服务商推荐（价格查证于 2026-09，以官方最新公示为准）

| 服务商（RAGFlow factory） | 模型 | 参考价 | 维度 | 什么时候选 |
|---|---|---|---|---|
| **ZHIPU-AI** | embedding-3 | 0.5 元/百万 tokens（Batch 0.25） | 2048（256–2048 可调） | **默认推荐**：国内直连、中文效果好、RAGFlow 原生支持。本项目在用 |
| Tongyi-Qianwen | text-embedding-v4 | 0.6 元/百万 tokens（国内地域） | 1024 默认（64–2048） | 备选：阿里百炼，新账号通常有免费额度 |
| OpenAI-API-Compatible / VLLM | BAAI/bge-m3 | **硅基流动官方定价页列为「免费」**（输入输出均免，但需实名 + 余额非负，见下） | 1024 | ★**迁移最省事（已实测）**：与旧库本地 TEI 同模型，向量等价，改引用即可免重解析 |
| Jina | jina-embeddings-v3 | 有每月免费额度 | 1024 | 多语种语料；国内直连稳定性不如前两者 |
| OpenAI | text-embedding-3-small | $0.02/百万 tokens（约 ¥0.14） | 1536 | 仅在合规与网络都可行时考虑；中文表现一般 |

**关于第 3 行（bge-m3）**：旧库现在是本地 TEI 的 `BAAI/bge-m3`（1024 维）。若改用**同名同维度**的
云端 bge-m3，向量空间理论上一致，**可能不必全量重解析**——这能把 5–8 小时迁移压到接近零。
但这是"理论成立"，必须先抽样验证：建试验库上传同一篇文档，比较相似度分布与检索命中是否一致，
确认后再决定是否省掉全量重解析。**没验证前不要直接改正式库。**

**关于「免费」——2026-09-26 实测，标价免费 ≠ 开箱可用**

本机用新注册的硅基流动 key 直连测试，三个接口全被拒：

| 调用 | 结果 |
|---|---|
| `GET /v1/models` | 200（key 有效、账号存在） |
| 免费 chat 模型（`tencent/Hunyuan-MT-7B`） | **402** `account balance is insufficient` |
| 免费 embedding（`BAAI/bge-m3`） | **402** 同上 |
| 付费 embedding（`Qwen/Qwen3-Embedding-0.6B`） | **402** 同上 |

即：平台对**所有推理调用统一校验账户余额**，免费模型也不放行。官方政策（2026-05-15 起）
要求免费模型使用者**必须完成实名认证**；未实名或余额为负都会撞上 402。

**要用免费 bge-m3，得先**：① 控制台完成实名认证（需支付宝人脸核验）；
② 让余额不为负——领"认证专享礼"¥16 代金券，或最低充值 ¥10。免费模型调用本身仍计费 0，
充值只是解除账户状态门槛。

另外，免费**不是合同保障**：平台有过把模型移出免费名单的先例（如 GLM-Z1-9B-0414 已转付费），
免费版限速固定（bge-m3：RPM 2000 / TPM 500k），大批量入库仍要分批。长期依赖请留付费备选。

**但在这个项目里，钱不是决策因素**：全库 187 篇约 660 万 tokens，走智谱 embedding-3
按 0.5 元/百万算也就 3 元出头。选 bge-m3 的真正理由是**省掉 5–8 小时的全量重解析**，
不是省这几块钱——别为了"免费"反而把时间赔进去。

>  Prices change. 上表只给量级与选择依据，实际计费以服务商控制台为准。

## 8. 暂停与恢复（用完机器先让路）

入库是长时间 CPU 密集任务（本机实测整栈约占 20GB 内存 / 7-8 核）。需要把机器让给别人时，
**停容器即可，数据全在命名卷里，不会丢**：

```powershell
# ── 暂停 ──（约 10 秒，内存/CPU 立即归还；Docker 自身仅剩约 240MB）
cd D:\AI\RAGFlow\ragflow\docker
docker compose stop

# ── 恢复 ──
cd D:\AI\RAGFlow\ragflow\docker
docker compose up -d --pull never      # 起栈（tei 加载 bge-m3 约需 1 分钟）
docker compose ps                      # 各容器应 healthy / Up

# 确认知识层可用（应 4 项全绿）
cd D:\AI\Painter
python main.py --check --skip-image

# 继续入库：论文组补齐 + 专著组分波（都带自愈，可无人值守）
# 注意 --include-running：暂停/重启会留下卡在 RUNNING 的文档，不带这个开关它们永远不动
python scripts\ragflow_ingest.py --group papers --include-running --wait --interval 60 --auto-restart
for ($i=1; $i -le 4; $i++) {
  python scripts\ragflow_ingest.py --group books --include-running --wave 6 --wait --interval 60 --auto-restart --stall-minutes 20
}

# 只看进度
python scripts\ragflow_ingest.py --group all --status-only
```

**关于断点**：

- 暂停时"正在解析"的文档会中断（重启后状态多为 `FAIL` 或**仍是 `RUNNING` 但进度为 0%**）
  ——入库脚本会把 `UNSTART/FAIL` 自动重新触发，但**卡在 `RUNNING` 的必须带 `--include-running`**
  （否则它们永远停着，2026-09-22 实测踩到）。
- 未触发的文档（`UNSTART`）由脚本自动重新触发。
- 已 `DONE` 的文档与向量都在 ES / MySQL 卷里，**不会重跑**。
- 断点快照见 `knowledge/ingest_checkpoint.md`（含各库完成数、chunk 与 token 量）。

> 若不需要继续入库，只想起栈查库，可只 `docker compose up -d --pull never` 后直接
> `python main.py --mode rag` 使用已有内容。

## 8. 未完成 / 待决

- [ ] 3 篇付费文献需教授用机构订阅获取（Ferrell 1969 / Grace 1961 / Grace 1964，见 §1.1）
- [ ] `Pawley_Green_Chapter3` 的原始目标待教授确认（见 §1.1）
- [x] ✅ 已下载 Pawley & Ross 章节（ANU 开放获取）并入库
- [x] ✅ 文件名与内容不符的 `3_Grace_1961_…` 已按 PDF 真实标题更正（Barker 1962）
- [ ] 专著组入库（用户要求暂缓；21 篇 / 7546 页，多为扫描）
- [ ] §5.1 的 20 组检索回归集（需教授提供历史需求）→ 建成后作为配置变更的验收护栏
- [ ] 语料侧优化：`references/_duplicates/` 里的冗余副本可择机删除（当前保留未删）
