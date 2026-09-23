# 交接文档（HANDOFF）

> **当前版本：v1.1.0（2026-09-23，指定重绘 + 图内文字约束）** —— 版本细节见根目录 `CHANGELOG.md`；
> 版本号唯一来源 `version.py`（`python main.py --version`）。
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

## 2. 当前状态（2026-09-23，v1.1.0）

- ✅ **v1.1.0 已完成（任务书 T1–T6）**：
  - **指定重绘** `revise.py` + `main.py --revise --feedback …`：人工反馈 → 反馈结构化 →
    图生图重绘（不支持则降级并记入报告）→ must_change/must_keep **逐条校验** →
    `output/<run>_revN/` + 报告 + `revision.json`（链式 keep 累积继承）
  - **图内文字策略** `TEXT_MODE`（默认 `caption_only`：图内无文字 + 图注表；`in_image` 保留）
  - 客户端统一工厂 `config.make_openai_client()`、环境指纹 `--check`、依赖 pin + lock、
    `NO_PROXY` 的 `[::1]` 清洗、检索零命中三因确诊与「检索明细」报告
- ✅ 单测 **86 项全绿**（mock、零费用）；`--check --skip-image` 文本/读图通过
  （RAGFlow 项报 502 属预期——本机栈按计划已 `docker compose stop`）
- ⏳ **未做**：真实重绘端到端与图内文字两模式的真机各一例（按张计费，待确认）；
  任务书 T7/T8/T9（入库收尾、检索回归集、页码抽检工具）

- ✅ 六阶段流水线全部真机验收通过（含扫描版 PDF 视觉直读端到端）
- ✅ 双 Key 首启向导、连通性自检（`--check`：文本/读图/绘图三项 + 配置了 RAGFlow 时
  自动追加"RAGFlow 知识层"一项，校验鉴权 / dataset 存在性 / 真实检索通路）、
  MinerU 通用探测与解析（子进程已修 GBK 编码崩溃）
- ✅ 架构设计定稿：`docs/RAGFLOW_ARCHITECTURE.md`（方案 A：RAGFlow 只做知识层）
- ✅ **P2 代码完成 + 真机验收通过（2026-09-22）**：`ragflow_client.py` + `--mode rag/local`
  双模式 + 引用溯源报告；mock 单测 31 项全绿（`tests/test_rag_mode.py`）
- ✅ **本机已部署 RAGFlow v0.27.2 并打通全链路**：`python main.py --check` 4 项全绿、
  `--dry-run --mode rag` 真机检索命中。部署目录 `D:\AI\RAGFlow`（**不在 C 盘**），
  细节与踩坑见 `D:\AI\RAGFlow\README.md`；运维脚本已收进仓库
  `scripts/ragflow_bootstrap.py`（建号/取 Key/建库）与 `scripts/ragflow_ops.py`
  （上传/解析/状态/检索试跑）
- ⏳ **剩余**：① 教授侧 215 篇真实语料入库（本机 `references/` 为空，仅用 3 份样本验证链路）；
  ② **机器选型决策**——bge-m3 版 TEI 实测吃 17GB 内存，整栈约 20.5GB，16GB 服务器跑不动
  （详见架构文档 §10.1，三条备选路径，**必须在入库前定，换模型要重算全库**）；
  ③ 入库调优（§5.1）

## 3. 阅读顺序

1. `README.md` — 功能全景与快速开始
2. `docs/RAGFLOW_ARCHITECTURE.md` — **P2 的施工图**：双模式设计、模块改动清单（§4）、
   检索调用样例（§6）、入库调优环节（§5.1）、实施计划 P1-P4（§8）、风险对策（§9）
3. `docs/TODO.md` — 待开发清单（启动脚本编码规范、RAGFlow 接入等）
4. 代码：`main.py`（入口）→ `pipeline.py`（编排）→ `knowledge.py`（阶段③，P2 主改动点）
   → `config.py`（配置）→ 其余按需

## 4. P2 施工要点（代码已落地，以下为已实现状态）

- ✅ `ragflow_client.py`：retrieval / 批量上传 / dataset 管理（`_request` 统一 Bearer 鉴权
  与 code!=0 业务错误；`_norm_chunk` 兼容 page_number vs positions、content vs content_with_weight）
- ✅ `knowledge.learn()` 增加 chunks 输入通道（`CHUNKS_USER_PROMPT_TEMPLATE`，断言强制带
  `[文献 p.X]`）；新增查询规划器 `plan_queries()`（失败降级用需求原文）
- ✅ `pipeline.run()` mode 分支：rag 模式跳过阶段②摄取，改为「查询规划器 → 逐组检索 →
  merge_chunks 合并去重 → 注入」；报告新增知识层模式行 + 引用文献列表（含引用页码）
- ✅ `config.py`：`RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID /
  RETRIEVAL_TOP_K / RETRIEVAL_SIM_THRESHOLD / RAGFLOW_TIMEOUT`；模式解析优先级
  `--mode > PIPELINE_MODE > 自动`（RAGFlow 配置齐全自动用 rag）
- ✅ `main.py --mode rag/local`；rag 模式 dry-run 真实测检索连通性（不调语言/绘图模型）
- `ingest.py` 未动（local 模式原样保留）
- **剩余**：RAGFlow 版本迭代快，真机验收前先对目标版本核一遍 API（HANDOFF 坑 6 同源风险）
- 验收：✅ mock 单测 19 项全绿；⏳ RAGFlow 真机端到端（需 P1 完成）

## 5. 需交接人单独提供（不在仓库，也不该在）

| 项 | 说明 |
|---|---|
| `.env` | 两个真实 API Key（LLM 组 + 绘图组），格式见 `.env.example`；含 SEARCH_PROXY |
| RAGFlow 访问信息 | 部署地址 / API Key / dataset ID。**本机测试实例已配好并写入 `.env` 的
  `RAGFLOW_*` 段**（base_url=`http://localhost`，Web 账号 `john@ragflow.local` /
  `Ragflow@2026`，仅本地测试用）；教授正式环境的地址/Key/ID 需另行提供 |
| 本机环境事实 | 见下方「机器环境备忘」，只对当前这台 Windows 机有效 |

### 机器环境备忘（Windows，当前开发机）

- Python 项目 venv：`D:/AI/Painter/.venv`（依赖已装好）
- MinerU：`D:/AI/R/.venv-mineru`（探测逻辑会自动发现，无需写死路径）
- 教授文献：215 篇在 `references/`（已被 .gitignore 排除，仅在本地）
- **代理坑（必读）**：shell 环境变量里的代理端口**会随本机代理软件变化**，别照抄旧端口。
  排查步骤：先 `curl -s -o /dev/null -w "%{http_code}" --max-time 20 https://github.com` 测代理，
  再 `curl ... --noproxy '*'` 测直连。**2026-09-23 实测：环境里的 `127.0.0.1:8041` 不通
  （HTTP 000 超时），直连可用（HTTP 200，1.5s）** —— 此时 git/gh 联网要显式绕开代理：
  `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY git push origin main --tags`。
  （历史记录里的 10939 / 7890 同理，都要当场测，不要盲信。）
- git 身份：本仓库用 repo-local config（Invinciblesowrder123 + noreply 邮箱）
- 发布流程：`git tag -a v<版本> -m "…"` → push main 与 tags → `gh release create v<版本> --notes-file <文件>`
  （`--notes-file` 用文件而不是 heredoc：命令行里出现 `powershell` 字样会被本机安全策略误判拦截）

## 6. 已知坑（都踩过，别再踩）

1. aixw 语言模型与绘图模型分属不同分组 → 必须双 Key；报 `not available for this group` 即分组没开通
2. MinerU 3.x+ 默认 backend 是 `hybrid-engine`（要 GPU）→ 调用必须 `-b pipeline`
3. MinerU CLI 内部起本地 API 回连 127.0.0.1 轮询 → 代理会劫持 localhost 导致 404
   （子进程已清代理变量，改动前先看 `mineru_detect.py` 的 clean_env）
4. 同一文件多个 Edit 不能并行发（后一个会基于旧版本覆盖前一个）——串行改，改完回读验证
5. 1×1 像素测试图会被多模态服务判无效图 → 用真实小图（`check.py` 里 `_tiny_png`）
6. Windows GBK 控制台 + 特殊 Unicode 输出会崩子进程 → 子进程 env 强制 UTF-8
7. **aixw 短输入风控（2026-09-22 新发现）**：上游会拒绝"极短提示词"，报
   `Upstream rejected illegal short-input distillation or heartbeat probing`（400）。
   触发条件：输入过短（实测 155 字那条 1 秒内被拒，267 字正常任务 16 秒通过），
   且**对重复出现的同一短提示词更敏感**。`check.py` 的探针已改为真实知识整理任务
   （约 270 字）+ 被拦时自动加长重试；**新增任何探测/健康检查逻辑时不要把提示词写短**。
   另注意：该风控判定会让响应延迟到 70s+，`check.py` 的 `CHECK_TIMEOUT=180`
   就是为此设的——超时设小了会把"上游慢"误报成"链路不通"。
8. **RAGFlow 部署与接入坑（2026-09-22 本机实测，完整清单见 `D:\AI\RAGFlow\README.md`）**：
   - Docker 镜像：`registry-mirrors` 自动选路会挂死，必须**显式带源名前缀**拉取再 retag
     （RAGFlow 主镜像走华为云 SWR 快，TEI 镜像只有 Docker Hub → 走 `docker.m.daocloud.io`）
   - `paper` 切片方法**只支持 PDF**，DOCX 报 `file type not supported yet(pdf supported)`
   - 检索参数 `top_k` 已更名 **`knn_top_k`**；文献名字段是 **`document_keyword`**；
     **页码在 `positions[0][0]` 且为 0 基，必须 +1**（`ragflow_client._norm_chunk` 已处理）
   - 检索报 `Provider not found for model .` = **dataset 的 `embd_id` 为空**（检索读 dataset
     字段、解析读租户默认，两处都要设，格式 `<模型名>@Builtin`）
   - 列表接口 `page_size` 有上限（传 500 被拒）
   - Web 接口与 SDK 接口在 v0.27 统一为 **`/api/v1`** 前缀（`/v1/...` 会 404）
   - 生成 API Key 需 RSA 加密密码 → 直接调容器内 `api.utils.crypt.crypt()`（本机免装依赖）
9. **v1.1.0（T1–T6）新增的坑**（完整表见 `DEV_TASKS.md` §7.1）：
   - Windows 上 `NO_PROXY` 与 `no_proxy` 是**同一个**环境变量（大小写不敏感），测试里当两个写会互相覆盖
   - `importlib.reload(config)` 会**重建类对象**，测试里 `assertRaises(前面 import 的 ConfigError)` 会失效 → 用 `config_mod.ConfigError` 动态取
   - `knowledge/knowledge_summary.md` 是**全局**文件、每次运行覆盖；修订旧 run 必须读 run 目录快照（已改为每次运行在 run 内留一份）
   - 上游多半不支持 `images.edit` → 图生图会降级为纯提示词重绘，**降级事实必须写进报告**（`generate.ImageResult.note`），否则等于静默降级
   - 反馈结构化/逐条判定都可能返回不全：一律"缺判定 = 不通过"，不许静默放行（"只写不查等于没写"）
