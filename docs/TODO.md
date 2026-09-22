# 待开发清单（TODO）

> 约定：事项按优先级排列；完成即移入文末「已完成」并注明提交号。
> 需要拍板的事项标 ⏸，被其他方案替代而关闭的标 ✕。

## 高优先级

- [x] ✓ **RAGFlow 知识层接入（P2）—— 代码完成 + 真机验收通过（2026-09-22）**
  `ragflow_client.py`（retrieval / 批量上传 / dataset 管理）、`knowledge.py` 查询规划器 +
  chunks 注入通道（断言带 `[文献 p.X]`）、`pipeline.py` rag/local 双模式 + 报告引用列表、
  `main.py --mode rag|local`。本机部署 RAGFlow v0.27.2 后真机打通：
  `--check` 4 项全绿、`--dry-run --mode rag` 检索命中、`--mode rag` 端到端跑通；
  mock 单测 31 项全绿。
  **源码核验修正两处**：候选池参数 `top_k`→`knn_top_k`；页码在 `positions[0][0]` 且为 0 基
  （必须 +1，否则引用整体少一页）。

- [ ] ⏸ **RAGFlow 嵌入模型/机器选型决策（阻塞 215 篇入库）**
  实测 bge-m3 版 TEI 独占 17.1GB 内存，整栈约 20.5GB → **16GB 服务器跑不动**。
  三选一：① 服务器 ≥32GB；② 改用 `Qwen/Qwen3-Embedding-0.6B`（预期约 5GB）；
  ③ 嵌入走外部 API。**必须在入库前定，换模型要重算全库向量**。
  详见 `docs/RAGFLOW_ARCHITECTURE.md` §10.1。

- [ ] **教授侧 215 篇入库 + 入库调优（P1/P1.5）**
  本机 `references/` 为空（仅 3 份样本验证链路），真实语料在教授侧。
  入库前先定上一条的模型选型；入库后按 §5.1 做「试切-评估-调参-全量」。
  运维脚本已备：`scripts/ragflow_ops.py`（上传/解析/状态/检索试跑）、
  `scripts/ragflow_bootstrap.py`（建号/取 Key/建库）。

- [ ] ⏸ **本机 RAGFlow 常驻策略**
  当前栈常驻约 20.5GB 内存（TEI 17GB）。需拍板：跑完测试后是否 `docker compose stop`
  停栈、是否设为开机自启、以及 `restart: unless-stopped` 是否改掉。停栈命令见
  `D:\AI\RAGFlow\README.md` §3。

- [ ] **入库调优环节（P1.5，配合 RAGFlow）**
  全量入库前先做「试切-评估-调参」：挑 10 篇代表性文献试切，用 20 组典型需求
  作为检索回归集测命中率，调优 chunk 方法/长度/分隔符与 embedding 模型后再全量导入。
  设计详见 `docs/RAGFLOW_ARCHITECTURE.md` §7。

- [ ] **跨平台启动脚本与编码规范**
  教授侧曾出现 .bat 用 UTF-8 保存中文导致 cmd 按 GBK 读取乱码、行尾混入双回车、
  uv 不在 PATH 等问题（本地已手工修复，仓库侧无此脚本）。
  待开发：① 仓库提供 `start.bat`（GBK/ASCII 编码 + CRLF，自动补 PATH）
  与 `start.sh`（UTF-8 + LF），并加 CI 行尾检查；② .editorcal / .gitattributes
  统一文本编码与行尾；③ Python 侧统一 ruff/black 格式规范。

## 已被替代 / 暂缓

- [x] ✕ **MinerU 4.x Windows 控制台 Unicode 崩溃**
  根因：MinerU 子进程在 GBK 控制台打印特殊 Unicode 字符报
  "No mapping for the Unicode character ... multi-byte code page"。
  已顺手加固：子进程强制 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`（local 模式兜底）。
  主路径由 RAGFlow DeepDoc 解析替代，不再依赖 MinerU。
