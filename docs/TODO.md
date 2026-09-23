# 待开发清单（TODO）

> **⚠️ 开发需求以 `docs/DEV_TASKS.md`（v1.1.0 开发任务书）为准**，本文件仅作进度记录与补充。
> 项目已转向 **RAGFlow 知识层新框架**：与旧框架（本地重型解析：MinerU / 结构感知选段 /
> 递归扫描 references 等）相关的事项**一律不纳入开发清单**（详见任务书 §1.2）。

> 约定：事项按优先级排列；完成即注明版本；需要拍板的事项标 ⏸，被其他方案替代而关闭的标 ✕。

## 已完成（v1.1.0 · 2026-09-23）

对应任务书 §4 的 T1–T6，代码 + mock 单测（86 项全绿）已完成，真机验收项另行标注：

- [x] ✓ **T1 指定重绘（人工反馈驱动）** —— 新增 `revise.py` + `main.py --revise/--feedback/
  --refine-from/--must-change/--must-keep/--text-mode/--re-retrieve`；反馈结构化（含降级）→
  组装修订提示词 → 图生图重绘（不支持则降级并记入报告）→ **must_change/must_keep 逐条判定**
  → `output/<run>_revN/` + 报告 + `revision.json`（链式 keep 累积继承）。
- [x] ✓ **T2 图内文字策略** —— `TEXT_MODE`（默认 `caption_only`：图内无文字 + 图注表）；
  `in_image` 保留（提示词强制简体中文）。校验新增 `text_check` 判定，缺判定按保守处理。
- [x] ✓ **T3 依赖 pin + 环境指纹** —— `requirements.txt` 加上限、`requirements.lock.txt`；
  `--check` 打印项目版本/Python/平台/关键库版本。
- [x] ✓ **T4 代理健壮性** —— `config._sanitize_proxy_env()` 导入时清理 `NO_PROXY` 里的 `[::1]`，
  不动 `HTTP_PROXY`/`HTTPS_PROXY`。
- [x] ✓ **T5 客户端统一超时/重试** —— `config.make_openai_client()`；`LLM_TIMEOUT` /
  `LLM_TIMEOUT_KNOWLEDGE` / `VISION_TIMEOUT` / `IMG_TIMEOUT` / `LLM_RETRIES`。
- [x] ✓ **T6 检索可观测与降级** —— 逐组查询打印命中/最高相似度；零命中确诊三因；
  报告新增「检索明细」；检索失败不再静默。
- [x] ✓ **附带修复** —— run 目录落知识快照（修订旧 run 不再拿错上下文）、
  报告新增「图注表」、`generate_image` 支持参考图与降级说明。

## 待确认 / 待拍板（逐条列全）

| # | 事项 | 说明 / 影响 |
|---|---|---|
| 1 | ⏸ **是否现在跑真机重绘验收（T1.5 第 1 条）** | 用 `output/run_20260922_183750` + 三段反馈真实跑一次，**按张计费**；跑完才能做"人工抽查 3 例漂移"。要用户点头 |
| 2 | ⏸ **`caption_only` 是否允许图内阿拉伯数字序号** | 现按任务书严格执行"图内零文字（含数字）"，图注表用「图上位置」列对应图面引线。若希望图内留编号数字，改判定规则一行 |
| 3 | ⏸ **`TEXT_MODE` 最终选型** | 建议两种各出一版真机图给教授挑（`caption_only` 更贴学术惯例，`in_image` 风险是中文渲染不可靠） |
| 4 | ⏸ **RAGFlow 服务器内存规格**（已从"必须换模型"降级为"确认一下"） | **2026-09-24 实测更正**：bge-m3 那 17.1GB 是 TEI 的**并发缓冲区**（默认 512 并发 × 16384 token 批），不是模型本身；给 TEI 加并发上限后 **4.98GB**、整栈 ~8.2GB → **16GB 机器就能跑，不必换模型**（换模型会让已入库向量全部作废重算）。详见 `docs/RAGFLOW_ARCHITECTURE.md` §10.1.1。现在只差知道目标服务器实际内存 |
| 5 | ⏸ **本机 RAGFlow 常驻策略** | 跑完测试后是否 `docker compose stop`、是否开机自启、`restart: unless-stopped` 是否改掉。（TEI 降配后整栈 ~8GB，常驻的代价已大幅降低） |
| 6 | ⏸ **B 方案专著组（books_b）何时开跑** | 论文组补齐**已在跑**（2026-09-24 00:5x 启动，预计 1-2 小时）；专著组 14 本 / **5108 页**，按实测 2.3-6 页/分钟估算需 **14-37 小时机时**——占机器，需排期（可分波跑：每波 6 本，跑完可停） |
| 7 | ⏸ **T8 检索回归集** | 需教授提供 20 组历史绘图需求 + 期望命中文献/页码；建立后所有检索/切片改动必须先跑它 |
| 8 | ⏸ **T9 引用页码抽检工具**是否要做 | 随机抽 N 条引用输出原文片段供人工核对页码 |

## 遗留（非本机可闭环）

- [ ] **教授侧 3 篇付费文献**：Ferrell 1969（纸本）、Grace 1961（Wiley）、Grace 1964（Chicago）——无合法免费源
- [ ] **2 个坏文件重下**：`Pawley_Green_Chapter3.pdf`（13KB 截断）、
  `Pawley_Prehistory_Oceanic_Languages.pdf`（42KB 截断）
- [ ] **`Pawley_Green_Chapter3` 原始目标确认**：疑似即已入库的《The Austronesians》第 3 章
  （Pawley & **Ross**），需教授确认

## 已被替代 / 暂缓

- [x] ✕ **跨平台启动脚本与编码规范（.bat / .sh / 行尾）**
  原属旧架构交付形态（双击 bat 启动本地流水线）。新框架下启动还需先起 RAGFlow
  容器栈（`docker compose up -d --pull never`），单纯一个 bat 解决不了启动问题，
  且用户已明确：**与旧框架相关的不纳入开发清单**，故关闭。
  如日后需要"一键起栈 + 跑流水线"，按新框架重新提需求。

- [x] ✕ **MinerU 4.x Windows 控制台 Unicode 崩溃**
  根因：MinerU 子进程在 GBK 控制台打印特殊 Unicode 字符报
  "No mapping for the Unicode character ... multi-byte code page"。
  已顺手加固：子进程强制 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`（local 模式兜底）。
  主路径由 RAGFlow DeepDoc 解析替代，不再依赖 MinerU。
