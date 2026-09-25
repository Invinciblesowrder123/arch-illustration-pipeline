# RAGFlow 知识层整合架构设计

> 状态：设计定稿，待实施。前置决策：教授已有 200+ 篇考古论文，人工筛选费精力，
> 确认采用「RAGFlow 知识层 + 现有 Python 编排执行层」的方案 A，不整体迁移 RAGFlow Agent。

## 1. 目标

1. **免人工筛选**：需求进来自动检索全库，直接命中相关论文片段，教授不再挑文献。
2. **引用溯源**：知识摘要每条断言标注 `[文献名 p.X]`，审稿报告自动附引用列表——学术场景的硬需求。
3. **知识跨课题复用**：200 篇是长期资产，一次入库，所有后续插图需求共享。
4. **保留现有闭环**：绘图→审稿→重绘、MinerU 回退、连通性自检等执行层逻辑零重写。

## 2. 双模式设计

| 模式 | 触发 | 知识来源 | 适用场景 |
|---|---|---|---|
| `--mode rag`（新默认） | RAGFlow 已部署且配置了 dataset | 检索 200 篇库的 top-k 片段 | 日常：需求直接进，免挑文献 |
| `--mode local`（保留） | 显式指定或 RAGFlow 不可用 | `references/` 目录直读 | 新到单篇文献即时用 / 无 RAGFlow 环境 |

## 3. RAG 模式数据流

```
① 需求输入
② 查询规划器：需求 → 3-5 组检索词（器物名/时代/文化/形制特征）
③ RAGFlow /api/v1/retrieval：每组查询检索，合并去重，取 top-k 片段（带 document_name + page）
④ 知识学习：chunks（含出处）+ 需求 → 知识摘要（断言带 [文献 p.X]）→ 绘图规格 → 中英提示词
⑤ 绘图 → 审稿 → 重绘闭环（不变）
   可选增强：审稿模型对存疑断言发起二次检索核对
⑥ 输出：插图 + 报告（问题清单 + 自动生成的引用文献列表）
```

## 4. 模块改动清单

| 文件 | 改动 | 规模 |
|---|---|---|
| `ragflow_client.py` | **新增**：retrieval 检索 / 文档批量上传 / dataset 管理，封装 API Key 鉴权 | ~150 行 |
| `knowledge.py` | `learn()` 增加 chunks 输入通道（与现有 refs/pages 并列）；新增查询规划函数 | 小改 |
| `pipeline.py` | mode 分支：rag 模式跳过阶段②摄取，改为检索；报告生成引用列表 | 中改 |
| `main.py` | `--mode rag/local` 参数，rag 模式下无文献也不报错 | 小改 |
| `config.py` | `RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID / RETRIEVAL_TOP_K / RETRIEVAL_SIM_THRESHOLD` | 小改 |
| `ingest.py` | **不动**（local 模式原样保留） | 零 |
| `verify.py / generate.py / check.py / setup.py` | **不动** | 零 |

## 5. RAGFlow 侧准备（一次性）

1. **部署**：docker compose（自带 ES/MySQL/Redis/MinIO），建议装在实验室服务器或 16GB+ 常开机器；教授笔记本不必常驻。
2. **建库**：创建 dataset「考古文献库」，200+ 篇通过 Web 界面或 API 批量上传。
3. **解析配置**：
   - 切片方法：`paper`（期刊论文）/ `book`（报告专著）；DeepDoc 开启表格与图片识别
   - 嵌入模型：中文首选 `BAAI/bge-m3`（RAGFlow 内置）
   - 扫描版 PDF：DeepDoc 自带 OCR，与本项目 MinerU 链路互为冗余
4. **API Key**：系统设置里生成，填入本项目 `.env`。
5. **批量导入耗时预估**：DeepDoc 约数秒/页，215 篇后台跑数小时~一天，一次性成本。

## 5.1 入库调优环节：chunking 与 embedding 优化（P1.5）

不做"一把梭全量入库"——考古文献的检索质量高度依赖切片与嵌入配置，先小规模调参再全量：

**四步流程：**

1. **试切**：挑 10 篇代表性文献（覆盖：发掘简报、研究报告、英文论文、扫描版、含图版多的），用不同配置各切一遍：
   - chunk 方法：`paper` vs `book`
   - chunk token 上限：512 / 768 / 1024
   - 分隔符策略、表格与图版说明是否独立成块
2. **评估**：建 20 组检索回归集（从教授历史需求出发，人工标注每组应命中的文献与页码），逐配置跑命中率（top-3 / top-5 命中率 + 关键片段排名）。
3. **调参**：按评估结果定最终配置；考古术语建关键词/同义词表（RAGFlow 术语重写），如"绿松石龙形器/龙形器/绿龙"。
4. **全量导入**：以调优后的配置批量入库，完成后用同一回归集做全库抽检。

**embedding 效率优化：**

- 模型：中文优先 `BAAI/bge-m3`（多语、长文本、RAGFlow 内置）；备选 `bge-large-zh-v1.5`
- 批量与并发：RAGFlow 任务并发按机器 CPU/显存调；纯 CPU 机器 215 篇预计数小时~一天，过夜跑
- 增量入库：后续新文献随到随传，不重算全库
- 质量护栏：每次调整 chunking/embedding 配置后，重跑 20 组回归集对比，命中率和以前持平或更好才切换

**产出物**：调参记录（配置 × 命中率对比表）+ 回归集文件，进仓库 `docs/rag_eval/`，
后续任何知识库配置变更都以它为验收标准。

## 6. 检索调用样例

```
POST {RAGFLOW_BASE_URL}/api/v1/retrieval
Authorization: Bearer {RAGFLOW_API_KEY}
{
  "question": "二里头 绿松石龙形器 匚形 镶嵌结构",
  "dataset_ids": ["<考古文献库 ID>"],
  "top_k": 12,
  "similarity_threshold": 0.2,
  "page_size": 12
}
→ 返回 chunks: [{content, document_name, page_number, similarity}, ...]
```

查询规划器把教授需求拆成多组检索词分别检索后合并去重——单次语义查询容易漏
（例：需求说"绿松石龙形器"，但某篇关键文献通篇叫"龙形器"而无"绿松石"前缀）。

## 7. 审稿增强（阶段二，可选）

审稿模型对"拿不准"的断言（如"龙身呈匚形"）发起二次检索，用原文页码核对；
引用列表直接进 `report.md`。这让问题清单从"AI 觉得不对"升级为"第 X 页原文与此不符"。

## 8. 实施计划

| 阶段 | 内容 | 产出 |
|---|---|---|
| P1 | RAGFlow 部署 + 215 篇入库 + 检索质量抽查（5 组典型需求人工核对命中） | 可用的知识库 |
| P1.5 | **入库调优环节**（§5.1）：试切-评估-调参-全量，embedding 模型选型与并发优化 | 调参记录 + 检索回归集 |
| P2 | `ragflow_client.py` + rag 模式接入 pipeline，端到端真机验收 | `--mode rag` 可用 |
| P3 | 引用溯源进报告 + 审稿二次检索（可选） | 学术级溯源报告 |
| P4 | README/使用说明更新，推送 GitHub | 交付 |

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 检索漏召（术语不一致："绿松石龙形器" vs "龙形器"） | 查询规划器多组检索词 + RAGFlow 术语重写/关键词表 |
| 切片把图版说明切碎 | 调大 chunk token 上限；关键文献用 `book` 方法人工抽查 |
| 200 篇中混有低质量文献污染检索 | RAGFlow 支持按文档开关检索权限，发现噪源即关 |
| RAGFlow 服务不在教授本机 | retrieval 是 HTTP API，CLI 在任何机器都能远程调用 |
| 与康复项目 Dify+Weaviate 栈并存 | 长期建议评估统一到 RAGFlow，一个 RAG 栈服务两条业务线 |

## 10. 实测补充（2026-09-22 本机部署验证，RAGFlow v0.27.2）

本机已完整部署一套 RAGFlow 并把 P2 客户端打到真机（部署细节见
`D:\AI\RAGFlow\README.md`，运维脚本已收进本仓库 `scripts/ragflow_bootstrap.py`
与 `scripts/ragflow_ops.py`）。**以下是与原设计假设不符、必须在 P1 前明确的事实。**

### 10.1 嵌入模型不再内置在 ragflow 镜像里（影响机器选型）

> **当前架构边界（2026-09-25）**：embedding 的执行位置属于 RAGFlow provider 层，不属于 Painter。无论使用本地 TEI 还是云端 provider，文档入库向量化和 `/retrieval` 的 query embedding 都由 RAGFlow 完成；Painter 只负责查询规划、发送 `question`、消费带页码的 chunks。把 embedding API 直接塞进 `knowledge.py` 会形成第二套检索后端，当前不采用。
>
> **模型迁移不可无损复用旧向量**：更换 embedding 模型/provider 后，旧向量与新向量不在同一向量空间，已有文档必须重新解析/向量化。正式迁移采用“新建试验 dataset → 抽样回归 → 全量重算或切换新 dataset → 保留旧库回退”的顺序。

架构文档 §5 原假设"嵌入模型中文首选 BAAI/bge-m3（RAGFlow 内置）"。v0.27.2 已改为
**独立 TEI 容器**（`infiniflow/text-embeddings-inference:cpu-1.8`，11GB 镜像，
内含 bge-m3 与 Qwen3-Embedding-0.6B），需要：

1. `COMPOSE_PROFILES` 追加 `tei-cpu`（源码识别内置嵌入的条件是 `"tei-" in COMPOSE_PROFILES`）
2. `TEI_MODEL` 与镜像内模型名一致
3. 租户默认嵌入 + **dataset 的 `embd_id`**（格式 `<模型名>@Builtin`）

**内存实测（bge-m3 / CPU / 8 workers，默认参数）：TEI 独占 17.1GB**，整栈约 20.5GB。

### 10.1.1 ★ 2026-09-24 更正：17GB 不是模型本身，是 TEI 的并发缓冲区

默认 `command` 只有 `--model-id` 和 `--auto-truncate`，TEI 会按
`--max-concurrent-requests=512` / `--max-batch-tokens=16384` 的默认值**预分配批处理缓冲区**——
这才是那 17GB 的绝大部分。给 `docker-compose-base.yml` 的 `tei-cpu` 段加上限后：

```yaml
command: ["--model-id", "/data/${TEI_MODEL}", "--auto-truncate",
          "--max-concurrent-requests", "8",
          "--max-batch-tokens", "4096",
          "--max-client-batch-size", "16"]
```

实测结果（同一模型 bge-m3、同一批已入库向量）：

| | 默认参数 | 加上限后 |
|---|---|---|
| TEI 常驻内存 | **17.1 GB** | **4.98 GB** |
| 整栈 | 约 20.5 GB | 约 8.2 GB |
| 宿主可用内存（本机 32GB） | 0.5 GB（占用 98%，容器随时 OOM kill / Exit 137） | 11.7 GB（占用 62%） |
| 入库吞吐 | 3-5 页/分钟（还伴随 OOM 风险） | **4.2-6.5 页/分钟** |

**结论重写**：bge-m3 完全可以在 16GB 服务器上跑（整栈 ~8GB + 系统开销），
**不需要换 0.6B 模型，也不需要 ≥32GB 机器**。之前"16GB 跑不动"的判断是把
"默认配置的 TEI"当成了"bge-m3 模型本身"的开销。

> 注意：改的是并发上限，**不是模型**——已入库向量全部有效，无需重算。
> 吞吐不降反升，因为不再跟 pagefile 抢内存。

#### ⚠️ 并发数别调太小（2026-09-24 踩到）

一度把 `--max-concurrent-requests` 设成 `8`，结果 **RAGFlow 解析时的并发嵌入请求被拒**，
文档直接 FAIL 并报：

```
[ERROR][Exception]: HTTPConnectionPool(host='tei', port=80): ...
```

**内存大头其实是 `--max-batch-tokens`（16384 → 4096 就够降压），不是并发数**——
把并发从 8 提到 **64**，TEI 内存仍是 **4.97GB**（几乎不变），health 200，也不再 FAIL。
结论：`--max-concurrent-requests 64 --max-batch-tokens 4096 --max-client-batch-size 16`。
若要继续压内存，下一步应动 `--max-batch-tokens`（会拉长长文档的嵌入耗时），而不是并发。

### 10.2 已核验的检索 API 差异（P2 客户端已按此修正）

对照 v0.27.2 源码（`api/apps/restful_apis/chunk_api.py`）+ 真机验证：

| 项 | 原实现假设 | v0.27.2 实际 | 处理 |
|---|---|---|---|
| 候选池参数 | `top_k` | **`knn_top_k`**（旧名可用但记 deprecated） | 客户端改发 `knn_top_k`；老版本忽略未知字段回落默认值 |
| 文献名 | `document_name` | **`document_keyword`**（内部 `docnm_kwd`） | 客户端两者兼容 |
| 页码 | `page_number` | **`positions[[0][0]]`，0 基**（`extract_positions` 做了 `-1`） | **一律 +1**（真机验证命中 p.2/p.3 正确） |
| 正文 | `content` | `content`（内部 `content_with_weight`） | 客户端两者兼容 |
| 列表分页 | 任意 page_size | **有上限**（500 被拒） | 运维脚本用 ≤100 |

### 10.3 解析环节的硬约束

- **`paper` 切片方法只支持 PDF**：DOCX 会报 `file type not supported yet(pdf supported)`。
  教授语料是 PDF，保持 `paper`；若日后要入 DOCX/TXT 需另建 `naive` 方法的 dataset。
- **`POST /datasets/{id}/documents/parse` 必须显式传 `document_ids`**。
- 解析失败原因看文档的 `progress_msg` 字段（如 `No default embedding model is set.`），
  比服务端日志更快定位。
- 扫描版 PDF 走 DeepDoc OCR 成功（本机用 4MB 扫描件验证通过）。

### 10.4 消费侧验收结论

- `python main.py --check`：4 项全绿（文本 / 读图 / RAGFlow 知识层探测）。
- `python main.py --dry-run --mode rag`：真机检索命中，片段带正确页码与相似度。
- mock 单测 31 项全绿。
- **待办**：教授侧 215 篇真实语料入库后重跑验收，并做 §5.1 的 chunking/embedding 调优。

## 11. 云 embedding 上线实测（2026-09-26）

**已在本机跑通**：embedding 改由云端 ZHIPU-AI 提供，本地 TEI 不再启动。

| 项 | 值 |
|---|---|
| provider / 实例 / 模型 | `ZHIPU-AI` / `zhipu-main` / `embedding-3` |
| 建库引用 | `embedding-3@zhipu-main@ZHIPU-AI` |
| 整栈内存 | 约 8.4GB（无 TEI），此前约 20.5GB |

### 11.1 模型引用的格式（源码口径）

`api/db/joint_services/tenant_model_service.py::split_model_name` 用 `rsplit("@", 2)`，
格式是 `{model}@{instance}@{provider}`；两段式 `{model}@{provider}` 时 instance 记作 `default`，
且当该 provider 只有一个活跃实例时会回退到它（日志有 warning）。

### 11.2 本地 TEI 的判定条件（坑）

`is_tei_builtin_embedding` 要求**容器内** `COMPOSE_PROFILES` 含 `tei-`、模型名等于 `TEI_MODEL`、
provider 为 `Builtin` 或空。因此：

- 只在命令行覆盖 profile、不删 `.env` 里的 `tei-cpu` → 容器没起 TEI，但代码仍把 bge-m3 指向
  `http://tei:80` → 报 `NameResolutionError` / `HTTPConnectionPool(host='tei')`，非常误导。
- 正确做法：改 `D://AI//RAGFlow//ragflow//docker//.env` 的 `COMPOSE_PROFILES` 去掉 `tei-cpu` 再重启。

### 11.3 旧 bge-m3 库在无 TEI 下的行为

检索报 `LookupError('Provider  not found for model BAAI/bge-m3.')` —— **明确失败**，
不会像以前那样长时间挂住。这也意味着旧库与新 embedding **不能共存**：
旧库要么重解析到云模型，要么临时用 `up --with-tei` 拉回本地 TEI。

### 11.4 边界不变

扫描件继续走 DeepDoc OCR（实测 OCR 1.32s + layout 1.05s，检索页码正确）；
Painter 不直接调用任何 embedding API，向量仍由 RAGFlow provider 层负责。
