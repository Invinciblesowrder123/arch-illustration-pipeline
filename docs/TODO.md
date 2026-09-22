# 待开发清单（TODO）

> 约定：事项按优先级排列；完成即移入文末「已完成」并注明提交号。
> 需要拍板的事项标 ⏸，被其他方案替代而关闭的标 ✕。

## 高优先级

- [ ] **RAGFlow 知识层接入（P2）—— 代码完成 ✅，待真机验收 ⏳**
  `ragflow_client.py`（retrieval / 批量上传 / dataset 管理，防御式解析 page_number vs
  positions 等版本差异）、`knowledge.py` 查询规划器 + chunks 注入通道（断言带 `[文献 p.X]`）、
  `pipeline.py` rag/local 双模式分支 + 报告自动附引用文献列表、`main.py --mode rag|local`
  （rag 模式 dry-run 实测检索连通性）。mock 单测 19 项全绿（`tests/test_rag_mode.py`）。
  **剩余**：教授侧完成 P1（RAGFlow 部署 + 215 篇入库）后做真机端到端验收；
  动手前先对照实际部署的 RAGFlow 版本核一遍 API 字段（见 HANDOFF 坑清单）。
  注：实测 references 目录已积累 215 篇（直塞上下文不可行），P1 完成后本项为第一优先。

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
