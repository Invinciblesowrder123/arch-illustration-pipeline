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
| 某篇解析 `FAIL`，`progress_msg` 报 OCR/版式错误 | 扫描件质量差或文件损坏 | 单篇重试；仍失败则该篇排除并记录 |
| 检索结果全是同一篇 | 语料集中在少数文献 | 正常；按 §5.1 建回归集再调 chunk/阈值 |
| 检索命中率低 | 术语不一致（"昙石山" vs "曇石山"） | 建术语/同义词表（RAGFlow 术语重写），或让查询规划器多出几组词 |

## 7. 未完成 / 待决

- [ ] 3 篇付费文献需教授用机构订阅获取（Ferrell 1969 / Grace 1961 / Grace 1964，见 §1.1）
- [ ] `Pawley_Green_Chapter3` 的原始目标待教授确认（见 §1.1）
- [x] ✅ 已下载 Pawley & Ross 章节（ANU 开放获取）并入库
- [x] ✅ 文件名与内容不符的 `3_Grace_1961_…` 已按 PDF 真实标题更正（Barker 1962）
- [ ] 专著组入库（用户要求暂缓；21 篇 / 7546 页，多为扫描）
- [ ] §5.1 的 20 组检索回归集（需教授提供历史需求）→ 建成后作为配置变更的验收护栏
- [ ] 语料侧优化：`references/_duplicates/` 里的冗余副本可择机删除（当前保留未删）
