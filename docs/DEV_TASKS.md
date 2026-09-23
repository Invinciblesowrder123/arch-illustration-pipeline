# 开发任务书 · v1.1.0

> **给下一个开发 agent。** 先读 §1 基线、§2 现状，再按 §4 清单动手。
> 本文件是唯一的需求来源；与旧文档冲突时以本文件为准。
>
> 配套阅读：`docs/HANDOFF.md`（项目交接与坑清单）、`docs/RAGFLOW_INGEST_PLAYBOOK.md`（知识库运维）、
> `docs/RAGFLOW_ARCHITECTURE.md`（知识层设计）、`CHANGELOG.md`（版本史）、`README.md`（使用说明）。
> **凭据与环境事实不写在本文件**（本仓库公开）：见项目根 `.env` 与 `D:\AI\RAGFlow\README.md`。

---

## 1. 基线（不可动摇）

### 1.1 架构基线

| 事实 | 说明 |
|---|---|
| **知识层 = RAGFlow** | `rag` 模式是主路径：文献先入库（服务端 DeepDoc 解析 + bge-m3 向量化），运行时按检索取片段。**扫描件 OCR 由 RAGFlow 服务端完成，客户端不做重型文档解析。** |
| `local` 模式 | 保留，但定位收窄为**「临时文献快通道」**——手里刚拿到一篇文献、不想入库时直接可用。不承担扫描件重型解析职责。 |
| 引用可溯源 | rag 模式下断言必须带 `[文献 p.X]`；页码取自 RAGFlow `positions[0][0]`（**0 基，客户端已 +1**），不得改动该口径。 |
| 交付形态 | Windows + PowerShell；CLI 优先（`main.py` + `scripts/*`）。 |

### 1.2 明确不做（out of scope）

以下全部属**旧架构遗留**，已决定不再投入：

- ❌ MinerU 适配（4.x `mineru-kit`、档位降级链、产物写临时目录）——扫描件已由 RAGFlow DeepDoc 处理
- ❌ 结构感知选段（`SECTION_SELECT` / `LONG_DOC_KEEP` / `full_text` + 书签目录）——"读哪几段"已由检索取代
- ❌ `references/` 递归扫描——会连带吃掉 `_broken/`、`_duplicates/` 归档目录（现有非递归是有意为之）
- ❌ 中文 `.bat` 启动器、旧架构的 40 万字 prompt 压缩、旧架构的编码/代理兼容补丁链
- ❌ 为 `local` 模式新增重型能力

> 唯一保留自"旧机器踩坑"的是 **T4 代理健壮性**——它保护的是 LLM 客户端，新框架同样依赖它。

---

## 2. 现状快照（2026-09-23 19:40，T1–T6 完成后更新）

### 2.1 版本与仓库

- 当前版本 **v1.1.0**（2026-09-23，主题「指定重绘 + 图内文字约束」）＝本任务书 T1–T6 的成果
  （前一版 v1.0.0「RAGFlow 知识层接入」已打 tag + Release）
- 版本号唯一来源：`version.py`（改版本时同步 `CHANGELOG.md`）
- 单测：`python -m unittest discover -s tests -t .` → **86 项全绿**（T1–T6 新增 52 项，原 34 项无退化）
- 依赖：`requirements.txt` 已钉版本区间，另附 `requirements.lock.txt`（本机 `pip freeze`）

### 2.2 知识库规模（RAGFlow v0.27.2，本机部署）

| 库 | 切片 | 规模 | 已完成 | 页数 |
|---|---|---|---|---|
| 考古文献库 | `paper` | 168 篇 | **143 篇** | 2506 / 3022 页 |
| 考古专著 | `book` | 21 本 | 1 本 | 365 / 7546 页 |
| 合计 | — | 189 | 144 | **2871 / 10568 页（27.2%）** |

- 已产出 **约 10.8 万 chunk / 277 万 token**，全部来自真实文献
- 专著组按用户决定走 **B 方案（择优）**：清单已写入 `knowledge/ingest_manifest.json` 的 `books_b`
  （14 本 / 5108 页 = 11 本文本层专著 + 3 本考古类扫描本），**尚未开跑**；
  被排除的 7 本 / 2438 页记在 `books_excluded_b`，文件仍在库内，随时可补

### 2.3 入库断点与恢复

- **RAGFlow 栈当前已停**（`docker compose stop`，数据在命名卷，未丢）
- 论文组剩约 25 篇（其中约 12 篇卡在 `RUNNING`、6 篇未触发、5 篇失败）
- 恢复命令见 `docs/RAGFLOW_INGEST_PLAYBOOK.md` §7 —— **必须带 `--include-running`**
  （暂停会留下状态为 `RUNNING` 但进度 0% 的文档，脚本默认只挑 `UNSTART/FAIL`，不碰它们）

### 2.4 机时与吞吐（用于排期，勿再乐观估计）

| 类型 | 实测吞吐 |
|---|---|
| 论文（文本层+少量扫描） | 3-5 页/分钟（空档期可冲到 15-20） |
| 专著（扫描件） | **约 2.3 页/分钟** |
| 单篇耗时 | 与页数强相关；百页扫描件 20 分钟级 |

---

## 3. 本次需求的来源（教授反馈）

1. **图片里的文字是英文的** —— 教授要中文标注（图内说明文字）。
2. **图片可能需要重绘** —— 自动重绘不通过时（现状），或教授看了图想改某处时（**当前无入口**）。

> T1 / T2 即针对这两条。

---

## 4. 任务清单

### T1【P0】指定重绘（人工反馈驱动）★ 本次核心

> **状态：✅ 代码完成（v1.1.0）。** 落在 `revise.py`；CLI 见 §T1.1；
> mock 单测覆盖反馈结构化三态、基准 run 解析、链式 keep 继承、逐条判定与报告渲染。
> ⏳ 验收第 1 条（真机跑一次重绘）与第 3 条（人工抽查 3 例漂移）**待用户确认后执行**——绘图按张计费。
> 实现补充：修订校验**同时送上一版与本轮新版两张图**，漂移判定才有依据；上游无图生图接口时
> 自动降级并写进报告。

**问题**：现有重绘只有"模型自己判不通过 → 自动重绘"这一条路。教授人工看图后提出修改意见时，
没有任何入口能把意见带进重绘——只能改 `requirement.txt` 从零重跑，知识摘要与已通过的方面都丢了。

**目标**：给定"某次运行"与一段自然语言反馈，产出新一版插图，并**逐条回应反馈**、**保证未指定的方面不漂移**。

#### T1.1 CLI 设计

```powershell
# 基本：对某次 run 按反馈重绘
python main.py --revise output/run_20260922_183750 --feedback "图内标注改成中文；底部比例尺移到右下角；镶嵌片不要规则化"

# 可选参数
--refine-from final|last|<图片文件名>   # 以哪一版为修订基准（默认 last）
--must-change "标注文字,比例尺位置"      # 手工指定必须改的项（不给则由模型从反馈拆）
--must-keep  "构图,视角,器物形制"        # 手工指定必须保持的项（不给则由模型拆）
--text-mode in_image|caption_only       # 图内文字策略（见 T2）
--max-attempts N                        # 重绘同样允许自动迭代（沿用现有语义）
--re-retrieve                            # 反馈涉及"文献依据有问题"时，允许重新检索
```

#### T1.2 行为规格

1. **读取基准 run**：`report.md`、`knowledge/illustration_spec.json`、`knowledge/knowledge_summary.md`、
   该 run 的图片（`final_illustration.png` 或最新 `illustration_attempt_N.png`）。
   缺产物 → 明确报错并列出可用路径，不要静默降级。
2. **反馈结构化**（**一次轻量 LLM 调用**）：把自然语言反馈拆成
   `must_change[]` / `must_keep[]` / `open[]`；模型返回不可解析时**降级**为
   "整段反馈并入 must_change"，并在报告里标注降级。
3. **组装新提示词** = 原始需求 + 上一轮知识摘要（**引用标注保留，不重新检索**）+ 反馈约束
   + 硬约束（见 T2）+ 一句明确要求：
   *"除 must_change 列出的项外，其余内容必须与上一版保持一致"*。
4. **生成 → 校验**：沿用现有 `verify.py` 通道，但校验提示词**增加两组判定**：
   - `must_change` 逐条：满足 / 部分满足 / 未满足 + 证据（**证据要指到图面位置**）
   - `must_keep` 逐条：是否漂移 + 证据
   未通过则把"未落实的反馈项"并入问题清单，走现有自动重绘循环。
5. **落盘**：`output/<原run名>_rev<N>/`，含新图 + `report.md`，报告须写明：
   由人工反馈触发／反馈原文／结构化后的 must_change 与 must_keep／逐条判定结果／最终结论。
6. **链式修订**：`--revise` 指向 `_rev1` 目录可继续；`must_keep` **累积继承**（rev1 的 keep 在 rev2 依然生效）。

#### T1.3 防止"越改越跑偏"（硬要求）

- `must_keep` 必须显式写进提示词，且**校验必须检查**——只写不查等于没写
- 反馈未提及的维度不得主动改动（提示词层面约束 + 校验层面抽查）
- 默认**不重新检索**（避免上下文漂移）；只有 `--re-retrieve` 时才重取

#### T1.4 边界与降级

| 情况 | 处理 |
|---|---|
| 上游不支持图生图 / 图片编辑接口 | 退化为"纯提示词重绘"（不带参考图），**在报告中注明** |
| 反馈结构化失败 | 降级为整段反馈，报告标注 |
| 基准 run 缺知识摘要 | 允许继续（只带需求+反馈），报告注明"无知识上下文" |
| `--must-change` 与 `--must-keep` 有交集 | 冲突项报错，要求人工消歧 |

#### T1.5 验收标准

1. 用**已知未通过的基准 run**（本机 `output/run_20260922_183750`，评分 58）跑一次真实重绘：
   反馈"图内标注改成中文；底部比例尺移到右下角；镶嵌片不要规则化"
   → 产出新图；报告**逐条**回应 3 条反馈，并给出 must_keep 检查结果。
2. 单测（mock，不产生费用）：
   - 反馈结构化：正常 JSON / 缺字段 / 返回非 JSON 三种输入
   - 基准 run 解析：完整 / 缺图片 / 缺摘要
   - `_rev` 链继承：rev2 的 keep 包含 rev1 的 keep
   - 报告渲染：must_change/must_keep 逐条判定的表格
3. 人工抽查 3 例：未指定的维度无肉眼可见漂移。

---

### T2【P0】图内文字语言约束 + 无字图模式

> **状态：✅ 代码完成（v1.1.0）。** `TEXT_MODE`（默认 `caption_only`）、提示词中英双语硬约束、
> 校验 `text_check` 判定（缺判定按保守处理计为不通过）、图注表（编号｜名称｜图上位置）写入
> 报告与 `caption_table.md`。
> ⏳ 验收（各跑一例真机）待确认后执行；两版都给教授挑。
> **待教授/用户拍板**：`caption_only` 目前按任务书严格执行"图内连阿拉伯数字也不出现"，
> 图注表用「图上位置」列对应图面引线。若更希望图内保留数字序号，需改 T2 判定规则一行。

**问题**：教授反馈"生成的图片是英文的"。

**根因（两层，都要覆盖）**：
1. 提示词里没有强制"图内文字必须中文"；
2. 文生图模型渲染中文本身不可靠（常出现乱码/伪汉字），**只靠提示词要中文不保险**。

**方案**：新增 `TEXT_MODE`（写进 `.env`，默认给 `caption_only`）：

| 模式 | 行为 | 校验要求 |
|---|---|---|
| `caption_only`（**建议默认**） | 图内**不出现任何文字**；标注信息改为**图下图注表**（编号 + 名称），输出到报告与知识摘要 | 图内出现任何文字即判**不通过** |
| `in_image` | 提示词强制"所有图内标注为简体中文、字体端正可读" | 校验新增"文字语言与可读性"项：非中文 / 有乱码 / 不可辨 → 不通过 |

**说明**：`caption_only` 不只是兜底——**考古线图的学术惯例本来就是图内标编号、图版说明里给名称**。
两条路都要能跑通，由教授选。

**验收**：
- `caption_only` 跑 1 例：图片无文字，报告含图注表（编号↔名称一一对应）
- `in_image` 跑 1 例：构造/复用一次含英文或乱码的失败用例，验证校验能识别并判不通过

---

### T3【P1】依赖 pin + 自检打印版本（跨机器可复现）

> **状态：✅ 完成（v1.1.0）。** `requirements.txt` 直接依赖加上限；`requirements.lock.txt` 已生成；
> `--check` 开头打印环境指纹（项目版本 / Python / 平台 / openai / httpx / httpx2 / requests /
> PyMuPDF / python-docx / ddgs）。
> 实测本机为 **openai 3.17 + httpx2 2.13**（即任务书里"另一方"那种组合），指纹里 httpx 显示"未安装"属正常。

**背景**：两台机器环境漂开（一方 `openai 2.x + httpx 0.28`，一方 `openai 3.17 + httpx2`），
产生了一整类"你这能跑我这不能跑"。根因是 `requirements.txt` 只写了下限（`openai>=1.40.0`）。

**方案**：
1. `requirements.txt` 钉到**实测过的版本区间**（直接依赖加上限）
2. 新增 `requirements.lock.txt`（完整 `pip freeze`，含间接依赖）
3. `--check` 输出中加入**环境指纹**：Python 版本、平台、关键库版本（openai / httpx / requests / PyMuPDF / python-docx / ddgs）

**验收**：新机器 `pip install -r requirements.txt` → `--check` 通过；`--check` 输出可直接贴给对方逐行对比。

---

### T4【P1】代理健壮性（NO_PROXY 的 `[::1]`）

> **状态：✅ 完成（v1.1.0）。** `config._sanitize_proxy_env()` 在模块导入时执行；单测覆盖清理与
> "代理变量不得被清空"。
> 注意：Windows 上 `NO_PROXY` 与 `no_proxy` 是**同一个**环境变量（大小写不敏感），写测试时别当成两个。

**问题**：`NO_PROXY` 含 `[::1]` 时（Cherry Studio 等工具会注入），httpx 0.28 在**构造客户端**时就崩：
`InvalidURL: Invalid port: ':1]'`——报错与网络无关，极难定位。

**方案**：`config.py` 增加 `_sanitize_proxy_env()`，在模块导入时剔除 `NO_PROXY`/`no_proxy` 里的 `[::1]`
项（保留合法的无括号 `::1`）。**同时不得清空 `HTTP_PROXY`/`HTTPS_PROXY`**——访问上游模型必须走代理。

**验收**：单测——构造 `NO_PROXY=...,[::1],...` 后 import config，断言 `[::1]` 已清理、代理变量仍在。

---

### T5【P1】LLM 客户端统一超时/重试

> **状态：✅ 完成（v1.1.0）。** `config.make_openai_client()` 为唯一构造点；
> `LLM_TIMEOUT` / `LLM_TIMEOUT_KNOWLEDGE` / `VISION_TIMEOUT` / `IMG_TIMEOUT` / `LLM_RETRIES` 全部可配，
> `--check` 打印生效值。

**问题**：客户端的超时/重试各写各的——`check.py` 用 `CHECK_TIMEOUT`、`generate.py` 写死 300s、
**`knowledge.py` 连超时都没设**。实测上游存在风控判定导致单次调用拖到 70s+ 的情况。

**方案**：`config.make_openai_client()` 统一工厂，参数化 `LLM_TIMEOUT`（知识阶段默认更长）/`LLM_RETRIES`；
所有调用点（knowledge / generate / verify / check）改用工厂。

**验收**：单测断言各调用点的超时与重试取自配置；`--check` 打印生效值。

---

### T6【P1】检索链路的可观测与降级

> **状态：✅ 完成（v1.1.0）。** 逐组查询打印 query/命中数/最高相似度；零命中自动确诊三种原因
> （`diagnose_retrieval_cause`）；部分查询失败不再静默——失败原因写入报告新增的「检索明细」表。

**方案**：
- 每次检索打印：query、命中数、最高相似度
- 命中为 0 时区分并提示**三种**情况：RAGFlow 不可达 / dataset 不存在 / 库空或阈值过高
- 检索失败时**明确报错**，不要静默返回空（静默空会让流水线以为"文献没写"）

**验收**：单测覆盖三种提示分支；手工制造 RAGFlow 不可达，确认提示准确。

---

### T7【P2】知识库入库收尾 ⏳ 未开始

- 论文组补齐（含 12 篇卡 `RUNNING` 的，**必须带 `--include-running`**）
- B 方案专著组开跑：`--group books_b --include-running --wave 5 --wait --auto-restart --stall-minutes 20`
- 全程带 `--auto-restart`（执行器静默停摆的自愈，见手册 §6 专节）

**验收**：`--group all --status-only` 显示 papers 与 books_b 全部 `DONE`（除已确认排除项）；无 `FAIL` 残留。

---

### T8【P2】检索回归集（改动护栏）⏳ 未开始（需教授提供 20 组历史需求）

需要教授提供 **20 组历史绘图需求** + 每组**期望命中的文献/页码**。
产出 `tests/regression/retrieval_cases.json` + 可重复脚本，输出命中率与前 k 命中明细。
**此后所有涉及检索/chunk/切片配置的改动，都必须先跑它。**

---

### T9【P2】引用页码抽检工具 ⏳ 未开始

随机抽 N 条引用，输出其 `document_name + page` 与原文片段，供人工核对页码是否准确
（页码口径：`positions[0][0]` 为 0 基，客户端 +1；**不要改**）。

---

## 5. 验收与提交规范

1. **单测**：现有 34 项必须全绿；每个任务补对应测试（§4 各条已列）
2. **自检**：改动涉及配置/客户端后，跑 `python main.py --check --skip-image`（零绘图费用）
3. **版本**：改 `version.py` 并同步 `CHANGELOG.md`（Keep a Changelog 格式），发布打 annotated tag
4. **提交**：中文 commit，说明**为什么**这样改（不只是改了什么）
5. **成本纪律**：mock 单测不访问网络；真实端到端跑动（含绘图）前先确认，绘图按张计费
6. **禁止**：把密钥写进任何被 git 跟踪的文件；把语料清单/断点快照提交到公开仓库（`knowledge/`、`references/`、`.env` 已 gitignore）

---

## 6. 需要用户 / 教授提供

| 项 | 说明 |
|---|---|
| 3 篇付费文献 | Ferrell 1969（纸本）、Grace 1961（Wiley）、Grace 1964（Chicago）——无合法免费源，需机构订阅 |
| 2 个坏文件重下 | `Pawley_Green_Chapter3.pdf`（13KB 截断）、`Pawley_Prehistory_Oceanic_Languages.pdf`（42KB 截断） |
| `Pawley_Green_Chapter3` 原始目标 | 疑似即已入库的《The Austronesians》第 3 章（Pawley & **Ross**），需教授确认 |
| 20 组检索回归集 | 见 T8 |
| `TEXT_MODE` 偏好 | 图内中文（`in_image`）还是图内无字 + 图注表（`caption_only`）——建议先各出一版给教授挑 |

---

## 7. 已知坑速查（动手前先看）

### 7.1 本轮新增（T1–T6 踩到的）

| 现象 | 原因 / 处置 |
|---|---|
| 单测里改 `NO_PROXY` 又改 `no_proxy`，断言互相覆盖 | Windows 环境变量大小写不敏感，两个名字是同一个变量 |
| `importlib.reload(config)` 之后 `assertRaises(ConfigError)` 失效 | reload 会**重建类对象**，import 时绑定的旧类不再匹配；测试里改用 `config_mod.X` 动态取 |
| 修订旧 run 时拿到了"别人的"知识上下文 | `knowledge/knowledge_summary.md` 是全局文件、每次运行覆盖；已改为 run 目录留快照，`--revise` 优先读快照 |
| 上游不支持 `images.edit` | 自动降级为纯提示词重绘，**降级事实必须写进报告**（`ImageResult.note`） |

### 7.2 原有


| 文档 | 内容 |
|---|---|
| `docs/HANDOFF.md` §6 | 8 条坑：含 **aixw 短输入风控**（探测提示词不能写短，超时别设太小） |
| `docs/RAGFLOW_INGEST_PLAYBOOK.md` §6 | **★ task_executor 静默停摆**（四条判定症状 + 自愈参数）；触发接口偶发 500 |
| `docs/RAGFLOW_INGEST_PLAYBOOK.md` §7 | 暂停/恢复（`--include-running` 是必须的） |
| `docs/RAGFLOW_ARCHITECTURE.md` §10 | RAGFlow v0.27.2 实测：TEI 独立容器吃 17GB、`paper` 切片仅支持 PDF、检索参数 `knn_top_k`、页码 0 基 |
| `D:\AI\RAGFlow\README.md` | 本机 RAGFlow 部署全记录（端口表、起停、13 条踩坑） |
