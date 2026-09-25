# 更新日志

本文件记录本项目的所有重要变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### Added（2026-09-26）——缺 embedding 模型时主动提示用户去添加（含服务商推荐）

- **新增 rag 模式预检**：`pipeline._ensure_embedding_ready()` 在检索前检查 RAGFlow 是否有可用的
  embedding 模型。明确缺失时**立即失败**并给出完整配置指引，而不是让每组查询各报一次晦涩错误。
- **三态判定**（`ragflow_client.check_embedding_ready()`）：`ready` 放行；`missing` 阻断；
  `local`（依赖本地 TEI、API 侧判断不了）只警告不阻断 —— 避免误伤正常流程。
- **新增 `embedding_guide.py`**：服务商推荐表与提示文案的唯一来源，供流水线预检与
  `ragflow_ops.py health` 共用，避免两处各写一份而走偏。
- **`health` 增加 embedding 可用性检查**：会打印已登记 provider，缺模型时直接输出服务商
  推荐与一键接入命令并返回非零。
- **服务商推荐写入手册**（入库手册 §7.1）：智谱 embedding-3（0.5 元/百万）为默认；
  阿里 text-embedding-v4（0.6 元/百万）为备选；并指出**云端 bge-m3 与旧库同模型，理论上可
  复用旧向量、免全量重解析（须先抽样验证）**。价格为 2026-09 查证的公开价，以官方为准。
- 新增单测 `tests/test_embedding_ready.py`（三态 + 阻断 + 指引内容），全套 **98 项全绿**。

### Added（2026-09-26）——云 embedding 上线：本地 TEI 关闭，改走 ZHIPU-AI

- **embedding 改由云端提供**：RAGFlow 登记 provider `ZHIPU-AI` / 实例 `zhipu-main` / 模型
  `embedding-3`，建库引用 `embedding-3@zhipu-main@ZHIPU-AI`。实测文字层文档解析 5.5s、
  图版 PDF 走完 DeepDoc OCR、检索页码正确，相似度 0.56–0.79。
- **本地 TEI 不再常驻**：整栈内存约 20.5GB → **约 8.4GB**。
- **`scripts/ragflow_ops.py embedding-init`**：一键登记云 provider + 实例 + 连通验证，幂等；
  只做登记与验证，不设租户默认模型、不切 dataset、不触发重解析。
- **`up` / `restart` 新增 `--with-tei`**：需要回退到本地嵌入时临时附带 TEI。
- **关本地 TEI 的正确姿势写入文档**：必须改 `ragflow/docker/.env` 的 `COMPOSE_PROFILES` 去掉
  `tei-cpu`；只停容器会让 bge-m3 仍指向 `http://tei:80`，报成 DNS 失败而非模型错误。

### Changed（2026-09-25）——云 embedding 迁移护栏与 RAGFlow 稳定性

- **云 embedding 迁移路径明确化**：embedding 仍由 RAGFlow provider 负责，Painter 不直接生成向量；新增 `RAGFLOW_EMBEDDING_REF` 作为迁移目标记录，仅供 `ragflow_ops.py health --embedding-ref` 校验，不会偷偷切换正式 dataset。
- **安全迁移约束写入入库手册**：正式库保留旧向量；云 provider 先建试验库、抽样重解析、跑检索回归，确认后再全量重算或切新正式库。扫描版继续走 DeepDoc OCR，不能为省内存把全部文献改成 `naive`。
- **RAGFlow 检索有限退避**：`ragflow_client.py` 仅对 GET 与 `/retrieval` 的连接异常、429、502、503、504 重试；上传/解析等有副作用的 POST 不重试，避免重复任务。
- **解析失败不再假绿**：`scripts/ragflow_ops.py wait` 在没有 RUNNING 但存在 FAIL 文档时返回非零。
- **配置新增**：`RAGFLOW_RETRIES`、`RAGFLOW_RETRY_BACKOFF`、`RAGFLOW_EMBEDDING_REF`。

### Fixed（2026-09-24）——审稿环节"未收到插图"假阴性

- **`verify.py` `_data_uri`：超过 400KB 的待审图自动压成 JPEG（q85）再送审**。
  根因：上游中转（实测 aixw，2026-09-24）对过大的 `image_url` 会**静默丢弃图片内容**，
  模型只看到 `image_url omitted`，审稿一律退化为"未收到插图、评分 0"的假阴性
  （1024px PNG 成图普遍 0.7~1.6MB，正好踩中）。压缩后 150~300KB，图内文字可辨读不受影响。
  压缩失败时原样发送，不阻断流程。
- 新增 `scripts/reverify.py`：对已有 run 的成图单独重跑审稿（不重绘图，用于事后复核）。

### 部署实测更正（2026-09-24）——改写了"16GB 机器跑不动"的结论

- **TEI 常驻内存 17.1GB → 4.98GB，整栈 20.5GB → 约 8.2GB**：bge-m3 那 17GB
  **不是模型本身，而是 TEI 的并发缓冲区**（默认 `--max-concurrent-requests=512` /
  `--max-batch-tokens=16384` 会预分配巨大批处理缓冲）。给 `docker-compose-base.yml`
  的 `tei-cpu.command` 加上 `--max-concurrent-requests 8 --max-batch-tokens 4096
  --max-client-batch-size 16` 即可**无损**降压——模型不变，**已入库向量全部有效，无需重算**。
- **结论改写**：**16GB 内存的服务器就能跑 bge-m3**，既不必换 `Qwen/Qwen3-Embedding-0.6B`，
  也不必强求 ≥32GB。此前 §10.1 的判断是把"默认配置的 TEI"当成了"bge-m3 模型本身"的开销。
  这条同时改变了给教授清单里"服务器规格"那一栏的口径（已同步更新 MD 与 PDF）。
- 宿主可用内存 0.5GB（占用 98%，容器随时被 OOM kill / Exit 137）→ **11.7GB（62%）**；
  入库吞吐 3-5 页/分钟 → **4.2-6.5 页/分钟**（不再与 pagefile 抢内存，反而更快）。

### 进行中

- **T7 论文组补齐**：`--group papers --include-running --retry-fail --wait --auto-restart`
  已启动（143 → 目标 168 篇）。专著组 `books_b`（14 本 / 5108 页）待排期。

---

## [1.1.0] — 2026-09-23 · 指定重绘 + 图内文字约束

教授拿到图之后能"提意见"了。此前只有"模型自己判不通过 → 自动重绘"一条路，
人工看图后想改某处**没有任何入口**——只能改 `requirement.txt` 从零重跑，
知识摘要与已通过的方面全部丢失。本版补上这条路，并同时解决"图里写的是英文"的问题。

### 亮点

- **指定重绘（T1，本次核心）**：给一次 run 加一段自然语言反馈，产出新一版插图。
  反馈先被拆成 `must_change[]` / `must_keep[]`，逐条落实、逐条校验，
  未指定的方面不得漂移；链式修订（`_rev1` → `_rev2`）时 `must_keep` **累积继承**。
- **图内文字策略（T2）**：新增 `TEXT_MODE`，默认 `caption_only`——图内不出现任何文字，
  标注改走**图下图注表**（编号↔名称一一对应）。这既是考古线图的学术惯例，
  也绕开了"文生图模型渲染中文常出伪汉字"的可靠性问题；`in_image` 模式保留，
  提示词强制简体中文、校验新增文字语言与可读性判定。

### 新增

**指定重绘（`revise.py`）**
- `main.py --revise <run目录> --feedback "<教授的修改意见>"`，配套
  `--refine-from final|last|<文件名>`、`--must-change`、`--must-keep`、
  `--text-mode`、`--max-attempts`、`--re-retrieve`。
- 反馈结构化：**一次轻量 LLM 调用**拆出 must_change / must_keep / open；
  模型返回不可解析时降级为"整段反馈并入 must_change"，并在报告里标注降级。
- 修订提示词 = 原始需求 + 上一轮知识摘要（**引用标注保留，默认不重新检索**）
  + 反馈约束 + 硬约束 + 一句明确要求："除 must_change 列出的项外，其余内容必须与上一版保持一致"。
- 修订校验（`verify.verify_revision_image`）：**同时送上一版与本轮新版两张图**，
  逐条给出 `满足/部分满足/未满足` 与 `未漂移/漂移` + **图面证据**；
  模型漏判某条时补"未判定"并强制不通过（**只写不查等于没写**）。
- 落盘 `output/<原run名>_rev<N>/`：新图 + `report.md`（含反馈原文、结构化结果、
  逐条判定表、逐轮明细）+ `revision.json`（供链式继承）+ `caption_table.md`。

**图内文字（T2）**
- `TEXT_MODE` 配置项（`caption_only` 默认 / `in_image`），提示词层追加**中英双语**硬约束
  （中文约束必要、英文提示词更稳，两条都写）。
- 校验层新增 `text_check` 判定：caption_only 下出现任何文字/字母/数字/仿汉字即不通过；
  in_image 下出现英文/乱码/不可辨伪汉字即不通过。**模型未返回该判定时按保守处理计为不通过**，
  不允许静默放行。
- 图注表：`knowledge.caption_table_md()` 把 `illustration_spec.annotations`
  归一化渲染成「编号｜名称｜图上位置」表格，兼容对象数组 / 字符串数组 / 映射三种模型输出；
  写入报告、`caption_table.md`，并把它写进 `illustration_spec` 的产出要求。

**工程（T3 / T5 / T6）**
- `config.make_openai_client()` 统一客户端工厂：`LLM_TIMEOUT` / `LLM_TIMEOUT_KNOWLEDGE` /
  `VISION_TIMEOUT` / `IMG_TIMEOUT` / `LLM_RETRIES` 全部来自 `.env`，
  知识 / 绘图 / 校验 / 自检四处调用点统一改走它；`--check` 打印**生效值**。
- `--check` 打印**环境指纹**（项目版本、Python 与平台、openai/httpx/httpx2/requests/
  PyMuPDF/python-docx/ddgs 版本），两台机器可直接逐行对比。
- **依赖 pin**：`requirements.txt` 直接依赖加上限（`openai>=2.0.0,<4.0.0` 等），
  新增 `requirements.lock.txt`（本机 `pip freeze` 全量）。
- **检索可观测（T6）**：逐组查询打印命中数与最高相似度；零命中时自动确诊**三种**原因
  （RAGFlow 不可达 / dataset 不存在 / 库空或阈值过高）并明确报错；
  部分查询失败不再静默——失败原因写进报告新增的「检索明细」表。

**知识快照**
- 每次运行在 run 目录同时落一份 `knowledge_summary.md` / `illustration_spec.json` /
  `caption_table.md`（`--revise` 修订历史 run 时必须拿到**那一次**的知识上下文）。

### 变更

- `knowledge.learn()` / `plan_queries()` 增加 `text_mode` / `timeout` / `max_retries` 参数；
  公开签名向后兼容（新参数均有默认值）。
- `generate.generate_image()` 返回 `ImageResult(path, used_reference, note)` 而非 `Path`
  （流水线忽略返回值，故不影响既有调用）；新增 `input_image` 参数走图生图。
- `verify.verify_image()` 新增可选 `text_mode` / `timeout` / `max_retries`；
  不传 `text_mode` 时行为与 1.0.0 完全一致。
- `knowledge/illustration_spec.json` 的 `annotations` 字段要求改为对象数组
  （兼容旧格式，无需重跑历史 run）。

### 修复

- **`NO_PROXY` 含 `[::1]` 时 httpx 构造客户端即崩**（T4）：`config` 模块导入时
  剔除带方括号的 IPv6 回环项（合法的 `::1` 保留），**不动** `HTTP_PROXY`/`HTTPS_PROXY`。
  此前这类报错（`InvalidURL: Invalid port: ':1]'`）与网络无关，现场极难定位。
- **`knowledge_summary.md` 是全局文件、会被后续每次运行覆盖**：修订旧 run 时会拿到
  别人的知识上下文。改为 run 目录留快照，`--revise` 优先读快照并在报告中注明来源。
- 检索失败此前只打 warning 就跳过，可能出现"静默返回空"被误读成"文献里没写"；
  现在区分部分失败与全部失败，全部失败直接报错并附确诊结论。
- `caption_only` 下模型漏给 `text_check` 时曾可能静默通过 —— 现在强制计为不通过。

### 验收

- 单测：**86 项全绿**（原有 34 项无退化 + 新增 52 项，全部 mock、不访问网络、不产生费用）
- 自检：`python main.py --check --skip-image` → 文本 ✅ / 读图 ✅ / 绘图 ⏭ /
  RAGFlow ❌（本机栈按计划已 `docker compose stop`，报 502 属预期）
- ⏳ 真实重绘端到端（T1.5 验收第 1 条）**尚未执行**：按张计费，待确认后跑
- ⏳ 人眼抽查 3 例漂移（T1.5 验收第 3 条）依赖上一条

### 未纳入本版（见 `docs/DEV_TASKS.md` §4）

- T7 知识库入库收尾（论文组补齐 + B 方案专著组）、T8 检索回归集（需教授提供 20 组历史需求）、
  T9 引用页码抽检工具。

### 已知问题

- `caption_only` 下"图内零文字"与"图注表按编号对应"之间存在一个取舍：本实现要求图内
  **连阿拉伯数字也不出现**（严格照任务书），图注表因此用「图上位置」描述来对应图面引线。
  若教授更希望图内保留编号数字，需按任务书 T2 修订一行判定规则。
- 生成插图仅供研究辅助：审稿校验是模型判断，**不可替代人工核对与学术责任**。

[1.1.0]: https://github.com/Invinciblesowrder123/arch-illustration-pipeline/releases/tag/v1.1.0

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
