# 考古学论文插图生成助手

**v1.1.0**（2026-09-23）· 本版主题：**指定重绘 + 图内文字约束** · [更新日志](CHANGELOG.md)

把「参考文献 → 学术插图」变成一条可复核的自动化流水线：AI 先学习文献知识，产出结构化绘图规格与
提示词，调用绘图模型出图，再由视觉模型扮演"审稿人"逐项核对插图与文献——**不合格自动带问题清单
重绘，通过才交付**。知识来源可以是**几百篇文献的检索式知识库**（RAGFlow），
每条断言都能标注 `[文献 p.X]` 出处；**教授看着图提意见时，也能带着意见重绘新一版**。

```
① 描述需求 ──┐
             ├─→ ② 取知识 ─→ ③ 学习+汇总 ─→ ④ 绘图 ─→ ⑤ 审稿校验 ─→ ⑥ 通过则交付
文献进知识库 ─┘   （rag 检索 / local 直读）                    ↑              │
                                                             └─ 不合格重绘 ←┘
                    人工反馈 ─→ --revise（指定重绘）─→ 新一版 + 逐条判定报告
```

## 这个版本有什么不一样

| 能力 | 说明 |
|---|---|
| **指定重绘（新）** | `--revise output/run_xxx --feedback "…"`：按人工意见出修订版，**逐条回应**反馈、未指定的方面不得漂移；链式修订时保留项累积继承 |
| **图内文字策略（新）** | `TEXT_MODE` 默认 `caption_only`：图内不出现任何文字、标注改走**图下图注表**；另有 `in_image`（图内强制简体中文），两条路都能跑通 |
| **知识层双模式** | `local` 直读 `references/` 目录；`rag` 从 RAGFlow 文献库检索（`.env` 配齐即自动启用） |
| **引用可溯源** | rag 模式下需求先拆成 3-5 组检索词分别检索、合并去重，知识摘要断言带 `[文献 p.X]`，报告附引用文献列表（含页码） |
| **可诊断** | `--check` 打印环境指纹（跨机器逐行对比）；检索零命中会自动区分"不可达 / 库不存在 / 库空或阈值过高" |
| **真机验收** | 本机部署 RAGFlow v0.27.2 后 `--mode rag` 端到端跑通；单测 86 项全绿（详见 [CHANGELOG](CHANGELOG.md)） |

## 适用场景

- 考古器物复原图、线描图、剖面图、场景复原图等学术论文插图
- 任何"有明确文献依据、画面细节必须忠实于文献"的插画需求
- 默认接入 [aixw](https://aixw.org)（OpenAI 兼容协议）：语言/校验模型 `gpt-5.6-sol`、
  绘图模型 `gpt-image-2`，都可换任意 OpenAI 兼容服务商

## 快速开始

### 1. 装依赖

```powershell
pip install -r requirements.txt
```

### 2. 填密钥（首次运行向导）

```powershell
python main.py
```

向导会要**两个密钥**（aixw 的语言模型与绘图模型分属不同分组）：

| 步骤 | 输入 | 用途 |
|---|---|---|
| 1/2 | **LLM Key** | `gpt-5.6-sol`：读文献、学知识、审稿校验 |
| 2/2 | **绘图 Key**（回车 = 复用第一个） | `gpt-image-2`：生成插图 |

密钥写入 `.env`（已被 `.gitignore` 排除），下次不再询问；向导结束后自动跑一次连通性自检。

### 3. 选知识层模式

```powershell
# A. 本地模式：直接把文献放进 references/（开箱即用，适合几十篇以内）
python main.py --mode local

# B. 检索模式：文献先入库 RAGFlow，再按需检索（适合几百篇，见下方「知识层部署」）
python main.py --mode rag
```

不指定 `--mode` 时自动判断：`.env` 里 RAGFlow 三项配齐 → `rag`，否则 → `local`。

### 4. 随时自检

```powershell
python main.py --check              # 文本 / 读图 / 绘图 /（配了 RAGFlow 则追加知识层）各实测一次
python main.py --check --skip-image # 跳过绘图测试，不产生绘图费用
```

## 两种知识层模式

| | `local` | `rag` |
|---|---|---|
| 知识来源 | 直读 `references/` 里的 PDF/DOCX/TXT/MD | RAGFlow 文献库（可千篇级） |
| 触发检索 | 无（全量塞进上下文） | 需求拆 3-5 组检索词 → 分别检索 → 合并去重 |
| 上下文占用 | 随文献数线性增长，几十篇就到上限 | 恒定（只取命中的片段） |
| 引用标注 | 文献名 | **文献名 + 页码** `[文献 p.X]` |
| 联网补充 | 可用（`--no-search` 关闭） | **不启用**——保证断言只来自文献库 |
| 依赖 | 无 | RAGFlow 服务（见下） |

## 逐步教学：生成第一张插图

### 第 1 步：准备文献

- **local 模式**：把文献放进 `references/`，支持 PDF / DOCX / TXT / MD。
  **扫描版 PDF（无文字层）也能读**，三级回退链自动处理：
  `MinerU 本地解析（免费）→ 视觉直读（页面渲图给多模态模型，耗 token）→ 跳过并记录`。
  可用 `--scan-policy mineru|visual|skip` 强制指定。
- **rag 模式**：文献先入库（见「知识层部署」），运行时按检索词取片段。

### 第 2 步：写需求

写进根目录 `requirement.txt`（推荐，方便反复改）或用 `-r "需求"` 传入。需求越具体越好，
建议含：**对象、时代/文化、视角、风格、必须出现的要素、需标注的文字与比例尺**。

```text
绘制二里头文化绿松石龙形器的科学复原图：俯视 45 度角，白描线图风格，
表现 2000 余片绿松石的镶嵌结构，龙身曲置呈"匚"形，右侧引线标注吻部、菱形主纹、尾尖，
下方附 5 厘米比例尺。依据文献描述，不得添加文献未记载的纹饰。
```

### 第 3 步：先免费试跑，再正式出图

```powershell
python main.py --dry-run --mode rag   # 只测检索/文献读取通路，不调用模型、零费用
python main.py --mode rag             # 正式运行（默认最多 3 轮重绘）
python main.py --max-attempts 5       # 需要更多轮次
```

### 第 4 步：看产物

产物在 `output/run_时间戳/`：

| 文件 | 说明 |
|---|---|
| `final_illustration.png` | ✅ 通过校验的最终插图 |
| `report.md` | 生成报告：知识层模式、检索词、**引用文献列表（含页码）**、每轮问题清单与评分 |
| `illustration_attempt_N.png` | 各轮历史版本 |

另有 `knowledge/knowledge_summary.md`（AI 汇总的绘图知识，带引用标注，**建议人工核对**）
与 `knowledge/illustration_spec.json`（结构化绘图规格）。

> 若报告显示"达到最大重试次数仍未通过"，说明插图与文献确实存在出入，问题已逐条列出——
> 据此修改需求或补充文献后重跑即可。

## 指定重绘：教授看了图提意见之后（v1.1.0 新增）

> 需要交给教授的清单（要提供什么资料、要拍板什么、怎么给意见）见
> [`docs/PROFESSOR_CHECKLIST.md`](docs/PROFESSOR_CHECKLIST.md)。

自动重绘只解决"模型自己判不通过"。**教授人眼看着某处不对**时，用 `--revise` 把意见带进去：

```powershell
python main.py --revise output/run_20260922_183750 `
  --feedback "图内标注改成中文；底部比例尺移到右下角；镶嵌片不要规则化"
```

产物在 `output/run_20260922_183750_rev1/`：新图 + 报告。报告逐条写明
**反馈原文 → 结构化后的 must_change / must_keep → 每条改了没有、证据在图面什么位置 → 最终结论**。

| 参数 | 作用 |
|---|---|
| `--refine-from final\|last\|<文件名>` | 以哪一版为修订基准（默认 `last`=最后一张尝试图） |
| `--must-change "A,B"` | 手工指定必须改的项（不给则由模型从反馈里拆） |
| `--must-keep "构图,视角,器物形制"` | 手工指定**必须保持**的项；链式修订时会累积继承 |
| `--text-mode in_image\|caption_only` | 本次图内文字策略（覆盖 `.env`） |
| `--re-retrieve` | 反馈涉及"文献依据有问题"时才重新检索（默认沿用原知识上下文，避免漂移） |

三条防跑偏的设计：① 未提及的维度写进提示词禁止改动，**并且校验会真的检查**；
② 修订校验同时看上一版与本轮新版两张图，漂移判定有依据；
③ 上游不支持图生图时降级为纯提示词重绘，**降级事实写进报告**，不静默。

> 链式修订：`--revise output/run_xxx_rev1` 可继续出 `_rev2`，`must_keep` 会累积继承。

## 图内文字：`caption_only` 还是 `in_image`

| 模式 | 出品 | 校验 |
|---|---|---|
| `caption_only`（默认） | 图内**不出现任何文字**，标注改走**图下图注表**（编号｜名称｜图上位置） | 图内出现任何文字/字母/数字/仿汉字即不通过 |
| `in_image` | 标注写在图上，**强制简体中文** | 出现英文/乱码/不可辨伪汉字即不通过 |

建议**两种各出一版给教授挑**——比争论哪个更好更快。理由：`caption_only` 正是考古线图的
学术惯例（图内标编号、图版说明给名称），也绕开了"文生图模型渲染中文常出错字"的可靠性问题。

## 命令速查

| 命令 | 作用 |
|---|---|
| `python main.py` | 正式生成插图（模式自动判定） |
| `python main.py --version` | 查看版本 |
| `python main.py --check` | 连通性自检（文本/读图/绘图；配了 RAGFlow 还含知识层检索） |
| `python main.py --dry-run` | 只验通路，不调用模型、零费用 |
| `python main.py --mode rag` / `--mode local` | 指定知识层模式 |
| `python main.py --revise output/run_xxx --feedback "…"` | **指定重绘**：按人工意见出修订版（报告逐条回应） |
| `python main.py --text-mode in_image` | 图内文字策略：写进图内（默认 `caption_only` 图内无字 + 图注表） |
| `python main.py --no-search` | local 模式禁用联网补充 |
| `python main.py --scan-policy visual` | 扫描件强制走视觉直读 |
| `python main.py --max-attempts 5` | 增加自动重绘轮数 |
| `python scripts/ragflow_bootstrap.py --email … --password … --dataset 考古文献库` | 初始化 RAGFlow（建号 / 取 API Key / 建库） |
| `python scripts/ragflow_ingest.py --group trial\|papers\|books\|all --wait` | 批量入库（可续跑） |
| `python scripts/ragflow_ingest.py --group all --status-only` | 查看入库进度 |
| `python scripts/ragflow_ops.py retrieve "检索词"` | 单点检索抽查 |

## 工作原理

```mermaid
flowchart LR
    A["需求 + 知识来源"] --> B{"知识层模式"}
    B -->|rag| C["查询规划 3-5 组检索词<br/>RAGFlow 检索 → 合并去重"]
    B -->|local| D["直读 references<br/>（扫描件走回退链）"]
    C --> E["知识学习 LLM<br/>产出知识摘要 + 绘图规格<br/>断言带 文献 p.X"]
    D --> E
    E --> F["中英双语提示词"]
    F --> G["绘图模型"]
    G --> H["视觉模型审稿<br/>逐项对照知识"]
    H -- 不通过 -->|问题清单注入提示词| G
    H -- 通过 --> I["final_illustration.png<br/>+ report.md（含引用列表）"]
```

- **知识来源阶段**：`rag` 用检索片段、`local` 用整篇文献文本，交给语言模型产出知识摘要与
  结构化绘图规格；`local` 模式下模型判定知识有缺口时可联网补充（rag 模式不联网）。
- **校验阶段**：视觉模型读图，对照需求与知识摘要逐项核对，输出 JSON（通过与否 / 评分 /
  问题清单 / 建议）；未通过则把问题清单注入提示词自动重绘。
- **原则**：**文献没写的细节宁可留白，不允许编造**——审稿提示词按同一约束执行。

## 配置（`.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | aixw / — / `gpt-5.6-sol` | 知识学习与审稿（须支持读图） |
| `IMG_BASE_URL` / `IMG_API_KEY` / `IMG_MODEL` | 复用 LLM / `gpt-image-2` | 绘图模型，可独立换服务商 |
| `IMG_SIZE` | `1024x1024` | 绘图尺寸（先小尺寸试跑省钱） |
| `VISION_*` | 复用 LLM | 校验模型单独指定（当 LLM 不支持读图时） |
| `MAX_ATTEMPTS` | `3` | 最大重绘轮数 |
| `TEXT_MODE` | `caption_only` | 图内文字策略：`caption_only`=图内无字+图注表 / `in_image`=图内强制简体中文 |
| `LLM_TIMEOUT` / `LLM_TIMEOUT_KNOWLEDGE` / `VISION_TIMEOUT` / `IMG_TIMEOUT` | 300 / 600 / 300 / 300 | 各阶段客户端超时（秒）；知识阶段默认更长 |
| `LLM_RETRIES` | `2` | 客户端失败自动重试次数 |
| `SEARCH_ENABLED` / `SEARCH_TOP_K` / `SEARCH_PROXY` | 1 / 5 / 空 | local 模式联网补充；国内需代理 |
| `PER_FILE_CHAR_LIMIT` | `30000` | 单篇文献注入上限（local 模式） |
| `MINERU_CMD` / `MINERU_BACKEND` / `MINERU_TIMEOUT` | 自动探测 / `pipeline` / 900 | 扫描件本地解析 |
| `RAGFLOW_BASE_URL` / `RAGFLOW_API_KEY` / `RAGFLOW_DATASET_ID` | 空 | 知识层；dataset 多个用分号分隔 |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_PAGE_SIZE` / `RETRIEVAL_SIM_THRESHOLD` | 12 / 12 / 0.2 | 检索候选池 / 返回条数 / 相似度阈值 |
| `RAGFLOW_TIMEOUT` | `60` | 检索超时（秒） |
| `PIPELINE_MODE` | 空（自动） | `rag` / `local` |

## 知识层部署（RAGFlow）

本版在**本机完整验证过** RAGFlow v0.27.2 的部署、入库与检索接入。核心结论：

- v0.27 起**嵌入模型不再随 ragflow 镜像分发**，独立为 TEI 容器；
  `COMPOSE_PROFILES` 需含 `tei-cpu`，`TEI_MODEL` 与镜像内模型名一致。
- **bge-m3（CPU）实测占 17.1GB 内存**，整栈约 20.5GB —— 16GB 服务器跑不动，
  可换 `Qwen/Qwen3-Embedding-0.6B`（约 5GB）或走外部嵌入 API。
  **换模型要重算全库向量，必须在批量入库前定。**
- 国内拉镜像：`registry-mirrors` 自动选路会挂死，需**显式带源名前缀**拉取再打回原名。
- 首次入库不要一次提交几百篇（会打爆 TEI，触发 30s 读超时失败），按 20-30 篇分批。

详细文档：

| 文档 | 内容 |
|---|---|
| `docs/RAGFLOW_INGEST_PLAYBOOK.md` | 入库与测试操作手册（命令、判读要点、失败处置、实测 ETA） |
| `docs/RAGFLOW_ARCHITECTURE.md` | 知识层架构设计 + §10 实测补充（API 字段差异对照） |
| `docs/HANDOFF.md` | 交接文档：项目状态、已知坑清单 |
| `D:\AI\RAGFlow\README.md` | 本机 RAGFlow 实例的部署全记录（端口表、起停、13 条踩坑） |

## 项目结构

```
├── main.py                 # 命令行入口（--check / --dry-run / --mode / --revise / --version）
├── setup.py                # 首次启动向导（填入 API Key）
├── version.py              # 版本号单一来源
├── config.py               # 集中配置（环境变量，启动时快速失败）
├── pipeline.py             # 六阶段编排器（rag/local 双模式）
├── ingest.py               # ② 文献摄取（local：PDF/DOCX/TXT/MD + 扫描件回退链）
├── mineru_detect.py        # MinerU 自动探测
├── knowledge.py            # ③ 知识学习（检索规划器 + 结构化规格 + 提示词 + 图注表）
├── ragflow_client.py       # RAGFlow 客户端（检索 / 上传 / dataset 管理）
├── search.py               # ③ 联网补充搜索（local 模式，可选）
├── generate.py             # ④ 绘图模型调用（文生图 / 图生图）
├── verify.py               # ⑤ 视觉模型审稿（含图内文字判定、指定重绘逐条判定）
├── revise.py               # ★ 指定重绘：人工反馈 → 修订版 + 逐条判定报告
├── check.py                # 连通性自检（文本/读图/绘图/知识层）
├── logger.py / errors.py   # 日志与异常
├── scripts/                # RAGFlow 运维：bootstrap / ops / ingest
├── tests/                  # mock 单测（不访问网络、不产生费用）
├── docs/                   # 架构、入库手册、交接文档、任务书与待办
├── references/             # 参考文献目录（_broken/ 与 _duplicates/ 为归档）
├── knowledge/              # 知识摘要、绘图规格、语料体检报告与入库清单
└── output/                 # 每次运行的插图与报告
```

## 测试

```powershell
python -m unittest discover -s tests -t .
```

全部为 mock 单测：不访问网络、不产生费用。覆盖 RAGFlow 客户端字段归一化、双模式解析、
检索策略与零命中确诊、探针重试、报告引用、**指定重绘全链路（反馈结构化 / 逐条判定 /
链式 must_keep 继承）**、图内文字判定、代理清洗、客户端工厂等。当前 **86 项全绿**。

## 常见问题

- **报 `not available for this group`**：密钥分组没开通该模型（aixw 语言/绘图分属不同分组）。
  换分组或换 key，或给绘图模型单独配服务商。
- **报 `references 目录为空`**：local 模式下文献没放进去；或用 `--mode rag`。
- **联网搜索超时**：搜索走 ddgs（境内需代理）——`.env` 设 `SEARCH_PROXY=http://127.0.0.1:端口`。
- **扫描件报"内容过少"**：会自动走 MinerU → 视觉直读 → 跳过；全失败可查 MinerU 安装或
  `--scan-policy visual`。
- **MinerU 报 404/连接失败**：多为系统代理劫持 localhost（本项目已为子进程清理代理变量）；
  仍失败请检查代理软件的绕过设置。
- **rag 模式自检失败**：`--check` 的失败信息会区分三种情况——服务不可达 / dataset 不存在 /
  接口通但库内零命中（尚未解析完或库为空），按提示处理即可。
- **想换服务商**：只改 `.env` 对应行的地址、密钥、模型名，无需改代码。

## 费用提示

- `--check` 会真实生成 1 张测试图（`--skip-image` 可免）；`--dry-run` 零费用。
- 正式运行每次至少：1-2 次语言模型调用 + 1 张图；每多一轮重绘多一张图。
- **指定重绘（`--revise`）按张计费**：反馈结构化 1 次轻量调用 + 每轮 1 张图（+1 次读图校验）。
- 绘图按张计费，建议先用 `IMG_SIZE` 小尺寸试跑。

## 边界与已知限制

- 插图是**研究辅助**：审稿由视觉模型判断，**不能替代人工核对与学术责任**。
- rag 模式的引用页码来自 RAGFlow 的 `positions`（0 基 +1 已在客户端处理），
  但**页码正确性仍建议抽查**。
- 检索质量取决于语料与切片配置；本项目的检索回归集尚未建立（见 [CHANGELOG](CHANGELOG.md)）。
- 知识摘要由模型生成，可能漏读或误读文献，正式使用前请人工核对关键形制与尺寸。

## 版本历史

见 [CHANGELOG.md](CHANGELOG.md)。当前版本 **v1.1.0**（2026-09-23）。

## License

MIT
