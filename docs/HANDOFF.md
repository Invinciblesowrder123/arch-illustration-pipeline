# 交接文档（HANDOFF）

> 给下一手开发 agent / 开发者。先读完本文，再按「阅读顺序」看仓库内文档。
> 本文档不含任何密钥。密钥与机器环境事实由交接人单独提供（见 §5）。

## 1. 项目一句话

考古学论文插图生成流水线：需求 + 参考文献 → LLM 学习文献知识 → 绘图模型出图 →
视觉模型审稿校验 → 不合格自动带问题清单重绘 → 最终插图 + 报告。
当前正在演进为「RAGFlow 知识层 + 本仓库 Python 编排执行层」的双层架构。

- 仓库：https://github.com/Invinciblesowrder123/arch-illustration-pipeline
- 技术栈：Python 3.13 + openai SDK（OpenAI 兼容协议）+ PyMuPDF + python-docx + ddgs
- 线上模型（aixw 中转，OpenAI 兼容）：语言 `gpt-5.6-sol`（支持读图）、绘图 `gpt-image-2`、
  **两模型分属不同分组，需要两个不同的 API Key**

## 2. 当前状态（2026-09-22）

- ✅ 六阶段流水线全部真机验收通过（含扫描版 PDF 视觉直读端到端）
- ✅ 双 Key 首启向导、连通性自检（`--check`）、MinerU 通用探测与解析（子进程已修 GBK 编码崩溃）
- ✅ 架构设计定稿：`docs/RAGFLOW_ARCHITECTURE.md`（方案 A：RAGFlow 只做知识层）
- ⏳ **下一步 = P2**：`ragflow_client.py` + `--mode rag` 接入 pipeline
  （前置 P1/P1.5：教授侧部署 RAGFlow + 215 篇入库 + 入库调优，均未开始）

## 3. 阅读顺序

1. `README.md` — 功能全景与快速开始
2. `docs/RAGFLOW_ARCHITECTURE.md` — **P2 的施工图**：双模式设计、模块改动清单（§4）、
   检索调用样例（§6）、入库调优环节（§5.1）、实施计划 P1-P4（§8）、风险对策（§9）
3. `docs/TODO.md` — 待开发清单（启动脚本编码规范、RAGFlow 接入等）
4. 代码：`main.py`（入口）→ `pipeline.py`（编排）→ `knowledge.py`（阶段③，P2 主改动点）
   → `config.py`（配置）→ 其余按需

## 4. P2 施工要点（接手后第一件事）

- 新增 `ragflow_client.py`：封装 retrieval / 批量上传 / dataset 管理（~150 行）
- `knowledge.learn()` 增加 chunks 输入通道（与现有 refs/pages 并列，断言带 `[文献 p.X]` 引用）
- `pipeline.run()` 加 mode 分支：rag 模式跳过阶段②摄取，改为「查询规划器 → 检索 → 注入」
- `config.py` 加 `RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID / RETRIEVAL_TOP_K`
- `ingest.py` **不动**（local 模式原样保留）
- 检索 API 样例见架构文档 §6；RAGFlow 版本迭代快，动手前先对目标版本核一遍 API
- 验收：mock 单测 + RAGFlow 真机端到端（需 P1 完成）

## 5. 需交接人单独提供（不在仓库，也不该在）

| 项 | 说明 |
|---|---|
| `.env` | 两个真实 API Key（LLM 组 + 绘图组），格式见 `.env.example`；含 SEARCH_PROXY |
| RAGFlow 访问信息 | 部署地址 / API Key / dataset ID（P1 完成后才有） |
| 本机环境事实 | 见下方「机器环境备忘」，只对当前这台 Windows 机有效 |

### 机器环境备忘（Windows，当前开发机）

- Python 项目 venv：`D:/AI/Painter/.venv`（依赖已装好）
- MinerU：`D:/AI/R/.venv-mineru`（探测逻辑会自动发现，无需写死路径）
- 教授文献：215 篇在 `references/`（已被 .gitignore 排除，仅在本地）
- **代理坑（必读）**：shell 环境变量代理指向 127.0.0.1:10939（死端口）；
  实际可用代理是 **127.0.0.1:7890**。git/gh 联网操作需前缀
  `HTTPS_PROXY=http://127.0.0.1:7890`；gh 首次 push 若被 LFS locks 断连，
  执行 `git config lfs.<仓库url>/info/lfs.locksverify false`
- git 身份：本仓库用 repo-local config（Invinciblesowrder123 + noreply 邮箱）

## 6. 已知坑（都踩过，别再踩）

1. aixw 语言模型与绘图模型分属不同分组 → 必须双 Key；报 `not available for this group` 即分组没开通
2. MinerU 3.x+ 默认 backend 是 `hybrid-engine`（要 GPU）→ 调用必须 `-b pipeline`
3. MinerU CLI 内部起本地 API 回连 127.0.0.1 轮询 → 代理会劫持 localhost 导致 404
   （子进程已清代理变量，改动前先看 `mineru_detect.py` 的 clean_env）
4. 同一文件多个 Edit 不能并行发（后一个会基于旧版本覆盖前一个）——串行改，改完回读验证
5. 1×1 像素测试图会被多模态服务判无效图 → 用真实小图（`check.py` 里 `_tiny_png`）
6. Windows GBK 控制台 + 特殊 Unicode 输出会崩子进程 → 子进程 env 强制 UTF-8
