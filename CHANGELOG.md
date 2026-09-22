# 更新日志

本文件记录本项目的所有重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] — 2026-09-22 · RAGFlow 知识层接入

首个正式版本。此前的开发阶段（本地文献直读闭环）未打标签，本版把「知识来源」从
"直读目录里的几篇文献"升级为"可承载几百篇文献的检索式知识库"，并在真机上完成端到端验收。

### 亮点

- **知识层双模式**：`local` 直读 `references/`；`rag` 从 RAGFlow 文献库检索。
  `.env` 里 RAGFlow 三项配齐即自动切到 rag，也可用 `--mode` 强制指定。
- **引用可溯源**：rag 模式下，需求先拆成 3-5 组检索词分别检索 → 合并去重 → 注入知识学习；
  知识摘要里的断言带 `[文献 p.X]` 出处，报告自动附「引用文献列表（含引用页码）」。
- **真机验收通过**：本机部署 RAGFlow v0.27.2 后，`--check` 4 项全绿、
  `--dry-run --mode rag` 检索命中、`--mode rag` 端到端跑通。
- **语料工程化**：教授侧 246 个 PDF 完成体检 → 去重 → 清单化 → 双库（论文/专著）入库。

### 新增

**知识层（核心）**
- `ragflow_client.py`：RAGFlow 客户端——检索（含字段归一化）、批量上传、dataset 管理、
  防御式解析跨版本字段差异。
- `knowledge.py`：新增检索规划器（把一句需求拆成多组检索词），以及把检索片段注入知识学习的通道；
  知识摘要强制带 `[文献 p.X]` 引用标注。
- `pipeline.py`：新增 rag/local 双模式分支，报告自动附引用文献列表与检索词记录。
- `main.py`：新增 `--mode rag|local`、rag 模式的 `--dry-run`（真机检索连通性测试，零模型费用）。
- `version.py` + `main.py --version`：版本号单一来源，启动日志打出版本便于判断现场版本。

**语料工程**
- `knowledge/corpus_report.md`（本地产物）：语料体检——逐篇页数、是否扫描件、
  文字层字符数、体积；本批 246 篇中 211 篇有文字层、35 篇扫描（需 OCR）、共 13882 页。
- `knowledge/ingest_manifest.json`（本地产物）：入库清单——按 `paper`/`book` 切片分成两组，
  记录去重保留结果、坏文件、缺失文献与逐篇页数（供 ETA 估算）。

**运维脚本（`scripts/`）**
- `ragflow_bootstrap.py`：无浏览器初始化——建用户 → 登录 → 生成 API Key → 建 dataset。
- `ragflow_ops.py`：日常运维——上传 / 触发解析 / 解析状态 / 检索试跑 / 设置默认模型 / 改库属性。
- `ragflow_ingest.py`：批量入库——按文件名去重可续跑、自动挑 `UNSTART/FAIL` 重试、
  按页数估 ETA、中断安全。

**自检**
- `check.py`：新增「RAGFlow 知识层」探测项（鉴权 → dataset 是否存在 → 真实检索一次，
  相似度阈值设 0 以区分"通路故障"与"库为空"），配置齐全时自动纳入 `--check`。

**文档**
- `docs/RAGFLOW_ARCHITECTURE.md` §10「实测补充」：TEI 嵌入容器、内存实测、
  检索 API 字段差异对照表、解析环节硬约束、消费侧验收结论。
- `docs/RAGFLOW_INGEST_PLAYBOOK.md`：入库与测试操作手册（顺序 / 命令 / 判读要点 /
  实测吞吐 ETA / 失败处置表 / 缺失文献清单）。
- `docs/HANDOFF.md`：已知坑清单扩到 8 条（新增 RAGFlow 部署与接入类）。

### 变更

- **rag 模式不再启用联网补充**：知识来源应限于文献库——混入网络资料会破坏
  "每条断言都可溯源到文献页码"的引用原则。`local` 模式行为不变（`--no-search` 仍可关）。
- **报告新增「检索词」行**：把模型拆出的检索词原样列进报告，便于判断检索是否跑偏。
- **自检探针改造**：探测提示词由「只回复 ok」改为真实知识整理任务——上游（实测 aixw）
  会对极短输入做风控，报 `illegal short-input distillation or heartbeat probing`（400）；
  被拦时自动加长重试一次。
- **自检超时 60s → 180s**：上述风控判定本身可耗 70s+，超时设小会把"上游慢"误报成"链路不通"。
- `.env.example` 补 `RETRIEVAL_PAGE_SIZE`、`PIPELINE_MODE`。

### 修复

- **引用页码整体少一页**（严重）：RAGFlow 返回的 chunk 没有 `page_number`，页码在
  `positions[[0][0]]`，而 DeepDoc 的 `extract_positions()` 已做 `-1`（**0 基**）。
  原实现仅在"页码为 0"时 +1，会让**所有**引用的页码偏小。已改为一律 +1，
  真机验证命中页与原文一致（p.2 / p.3）。
- **检索候选池参数已更名**：v0.27 起 `top_k` → **`knn_top_k`**（旧名仍可用但服务端记
  deprecated 警告）。客户端改发新名：新版本按新参数生效，老版本忽略未知字段回落默认值。
- **文献名字段**：响应里的文献名在 `document_keyword`（内部 `docnm_kwd`），
  而非 `document_name`；客户端两者兼容。
- **报告里的检索词数被覆盖**：联网搜索分支的局部变量名与检索规划的 `queries` 同名，
  导致报告把 5 组检索写成 3 组（搜索词数量）。已分离变量名。
- MinerU 子进程在中文 Windows 下的编码崩溃（子进程 env 强制 UTF-8）。
- 绘图提示词字段缺失时自动补齐（模型偶尔漏字段导致出图失败）。

### 部署与运维：RAGFlow v0.27.2 实测清单

配套部署文档见 `D:\AI\RAGFlow\README.md`。以下为本机实测踩过并已验证的坑：

| # | 现象 | 原因 | 处置 |
|---|---|---|---|
| 1 | `docker pull` 数分钟无输出（挂死） | `registry-mirrors` 自动选路失效 | **显式带源名前缀**拉取再 `docker tag` 回原名；RAGFlow 主镜像走华为云 SWR 快，TEI 镜像只有 Docker Hub |
| 2 | MySQL 端口冲突 | 宿主已有原生 mysqld 占 3306 | 改 `EXPOSE_MYSQL_PORT`（本机 33061） |
| 3 | 解析报 `No default embedding model is set.` | 租户默认嵌入未设 | `PATCH /api/v1/users/me/models`（`tenant_id` 等于用户 id） |
| 4 | 检索报 `Provider  not found for model .` | dataset 的 `embd_id` 为空（检索读 dataset、解析读租户默认） | 设 `BAAI/bge-m3@Builtin`（**必须带 `@provider`**） |
| 5 | v0.27 嵌入模型不再随 ragflow 镜像分发 | 改为独立 TEI 容器 | `COMPOSE_PROFILES` 追加 `tei-cpu`；`TEI_MODEL` 与镜像内模型名一致 |
| 6 | **TEI 内存占用 17.1GB**（bge-m3 / CPU / 8 workers） | CPU 上按核数加载模型 | 整栈约 20.5GB；**16GB 服务器跑不动**，可换 `Qwen/Qwen3-Embedding-0.6B`（约 5GB）或走外部嵌入 API |
| 7 | DOCX 解析报 `file type not supported yet(pdf supported)` | `paper` 切片方法只支持 PDF | 论文语料保持 PDF；DOCX/TXT 另建 `naive` 方法的库 |
| 8 | `POST .../documents/parse` 报 `document_ids is required` | 接口要求显式传参 | 传 `{"document_ids": [...]}` |
| 9 | 列表接口传 `page_size=500` 被拒 | 分页上限 | 用 ≤100 |
| 10 | 一次性提交 159 篇后有文档解析失败 | 并发拉起 40+ 任务 → TEI 排队 16s → 超过 RAGFlow 侧 **30s 读超时** | 首次入库按 20-30 篇分批；失败文档重跑脚本会自动重试 |
| 11 | Web/API 路径 404 | v0.27 起 Web 与 SDK 接口统一为 **`/api/v1`** | 用 `/api/v1`（`/v1/...` 已废） |
| 12 | API Key 无法用脚本生成 | 注册/登录密码需 RSA 加密 | 直接调**容器内** `api.utils.crypt.crypt()`（本机免装 pycryptodome）；会话 token 在登录响应 `Authorization` 头 |
| 13 | C 盘可用空间骤降约 10GB | 栈吃 20GB 内存后 Windows 撑大 `C:\pagefile.sys`（Docker 自身在 C 盘仅占几十 MB） | 把页面文件移到 D 盘，或跑完 `docker compose stop` |

### 语料与数据（截至本版）

- 原始 246 个 PDF（1.5GB）→ **186 篇唯一有效文献 / 10568 页**。
- 剔除：58 个 md5 相同的重复文件、3 个不可用文件（1 个 0 页损坏 + 2 个截断残留），
  已归档到 `references/_broken/` 与 `references/_duplicates/`（**只移动未删除**）。
- 分库：论文/简报 165 篇（`paper` 切片，库「考古文献库」）、
  专著/大型报告 21 篇（`book` 切片，库「考古专著」）。
- 入库进度：论文组执行中（本机约 0.9-1.4 篇/分钟）；专著组按需方要求暂缓。
- 更正错名：`3_Grace_1961_…pdf` 实际内容是 Barker 1962《Topical Report 1. Linguistics》
  （同一份文件被复制成两个 Grace 文件名）→ 按「以 PDF 自身标题为准」改名。
- 缺失文献（均为付费/纸本，无合法免费源）：Ferrell 1969（444 页，纸本 NT$400）、
  Grace 1961（*American Anthropologist* 63:359-368，Wiley 付费墙）、
  Grace 1964（*Current Anthropology* 5:361-368，Chicago 付费墙）。

### 已知问题

- 论文组有 2 篇因 TEI 读超时 `FAIL`，重跑 `ragflow_ingest.py` 会自动重试。
- 专著组（21 篇 / 7546 页）尚未入库；扫描件占比高，预计数小时。
- 检索回归集（20 组历史需求 + 人工标注命中）未建立，检索质量目前靠人工抽查。
- 本机 RAGFlow 栈常驻约 20.5GB 内存；是否常驻/自启待定。
- 生成插图仅供研究辅助：审稿校验是模型判断，**不可替代人工核对与学术责任**。

### 升级 / 部署指引

从"本地模式"升级到本版：

1. 若只需本地模式：无需任何操作，`python main.py` 行为与之前一致。
2. 启用知识层：
   - 部署 RAGFlow（见 `D:\AI\RAGFlow\README.md`），用
     `scripts/ragflow_bootstrap.py` 建号/取 Key/建库；
   - 把 Key 与 dataset ID 填入 `.env` 的 `RAGFLOW_*` 三项（多个库用分号分隔）；
   - `scripts/ragflow_ingest.py` 批量入库，`--status-only` 随时看进度；
   - `python main.py --check` 应看到 4 项全绿。
3. **注意**：更换嵌入模型会导致已入库向量失效，需整库重跑解析——
   故模型选型应在批量入库**之前**定。

### 环境基线

| 项 | 版本 |
|---|---|
| Python | ≥3.10（开发环境 3.13） |
| OpenAI SDK | ≥1.40.0 |
| RAGFlow | v0.27.2 |
| 嵌入模型 | BAAI/bge-m3（TEI 容器内置，Builtin provider） |
| 检索 API | `/api/v1/retrieval`（`knn_top_k` / `page_size` / `similarity_threshold`） |

[1.0.0]: https://github.com/Invinciblesowrder123/arch-illustration-pipeline/releases/tag/v1.0.0
