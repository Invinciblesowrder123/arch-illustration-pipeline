# -*- coding: utf-8 -*-
"""T1【P0】指定重绘：人工反馈驱动的插图修订。

问题背景：原来只有"模型自己判不通过 → 自动重绘"这一条路。教授人工看图后提出修改
意见时**没有任何入口**能把意见带进重绘——只能改 `requirement.txt` 从零重跑，
知识摘要与已通过的方面全丢。

本模块提供的通路：
  基准 run + 一段自然语言反馈 → 反馈结构化 → 组装"只改这些、其余不许动"的提示词
  → 以基准图为参考图重绘（上游不支持图生图则降级并注明）→ **逐条**校验
  （must_change 是否满足 / must_keep 是否漂移）→ `output/<原run名>_rev<N>/` + 报告。

三条防跑偏的硬规则（见 DEV_TASKS.md T1.3）：
1. `must_keep` 必须显式写进提示词，**且校验必须逐条检查**（只写不查等于没写）；
2. 反馈未提及的维度不得主动改动——提示词层面约束 + 校验层面回看上一版抽查；
3. 默认**不重新检索**（避免上下文漂移），只有 `--re-retrieve` 才重取。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config as config_mod
import generate
import knowledge
import verify
from config import Config
from errors import AppError, KnowledgeError, ReviseError

logger = logging.getLogger("painter")

_REV_SUFFIX = re.compile(r"^(?P<base>.+)_rev(?P<n>\d+)$")
_ATTEMPT_NUM = re.compile(r"illustration_attempt_(\d+)\.png$")


# ---------- 基准 run 的解析 ----------

def split_rev_name(name: str) -> tuple[str, int]:
    """`run_x_rev2` → ("run_x", 2)；普通 run 名 → (name, 0)。"""
    m = _REV_SUFFIX.match(name)
    if m:
        return m.group("base"), int(m.group("n"))
    return name, 0


def next_rev_dir(output_dir: Path, base_name: str) -> tuple[Path, int]:
    """算出下一个修订目录 `<base>_rev<N>`（N = 现有最大编号 + 1）。"""
    top = 0
    if output_dir.exists():
        for p in output_dir.iterdir():
            if not p.is_dir():
                continue
            b, n = split_rev_name(p.name)
            if b == base_name and n > top:
                top = n
    return output_dir / f"{base_name}_rev{top + 1}", top + 1


def _field(text: str, name: str) -> str:
    m = re.search(rf"^-\s*{re.escape(name)}[:：]\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def _attempt_num(p: Path) -> int:
    m = _ATTEMPT_NUM.search(p.name)
    return int(m.group(1)) if m else 0


@dataclass
class RunContext:
    run_dir: Path
    base_name: str
    rev_index: int                       # 0=原始 run，N=该 run 的第 N 版修订
    requirement: str
    knowledge_md: str
    knowledge_source: str                # 知识摘要来自哪里（run 快照 / 全局 knowledge/）
    spec: dict
    mode: str
    images: list[Path] = field(default_factory=list)
    base_image: Path | None = None
    inherited_keep: list[str] = field(default_factory=list)   # 链式继承的 must_keep
    parent_feedback: str = ""


def pick_base_image(run_dir: Path, images: list[Path], refine_from: str) -> Path:
    """按 `--refine-from` 选基准图：final / last / <图片文件名>。"""
    if refine_from == "final":
        p = run_dir / "final_illustration.png"
        if not p.exists():
            raise ReviseError(
                f"该 run 没有 final_illustration.png（说明它当时未通过校验）。"
                f"请改用 --refine-from last，或直接指定图片文件名。"
                f"目录内可用文件: {[q.name for q in images]}")
        return p
    if refine_from in ("last", "", None):
        if not images:
            raise ReviseError(f"{run_dir} 内没有任何 illustration_attempt_N.png，无从修订。")
        return images[-1]
    p = run_dir / refine_from
    if not p.exists():
        raise ReviseError(
            f"指定的基准图不存在: {refine_from}。目录内可用文件: {[q.name for q in images]}")
    return p


def load_run_context(run_dir: Path, knowledge_dir: Path, *,
                     refine_from: str = "last", requirement_fallback: str = "") -> RunContext:
    """读取基准 run 的产物。缺关键产物 → 明确报错并列出可用路径，不静默降级。"""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ReviseError(f"基准 run 不存在或不是目录: {run_dir}")

    available = sorted(p.name for p in run_dir.iterdir())
    # 只把 illustration_attempt_N.png 当作候选基准（final 是其中某一张的副本，
    # 若也放进来会干扰 --refine-from last 的语义）
    images = sorted((p for p in run_dir.glob("illustration_attempt_*.png")), key=_attempt_num)

    # 需求与模式：优先 report.md，其次命令行给的 requirement
    requirement = requirement_fallback
    mode = ""
    report_p = run_dir / "report.md"
    if report_p.exists():
        text = report_p.read_text(encoding="utf-8")
        requirement = _field(text, "绘图需求") or requirement
        mode = _field(text, "知识层模式")
    if not requirement:
        raise ReviseError(
            f"无法确定基准 run 的绘图需求：{run_dir} 下没有可解析的 report.md，"
            f"且命令行未给 --requirement。目录内容: {available}")

    if not images:
        raise ReviseError(
            f"基准 run 内没有插图（找不到 illustration_attempt_*.png），无从修订。"
            f"目录内容: {available}")

    # 知识上下文：优先 run 目录内的快照（pipeline 每次运行会落一份），
    # 回落到全局 knowledge/（老版本 run 没有快照）。缺失时允许继续，但要注明。
    knowledge_md, source = "", ""
    snapshot = run_dir / "knowledge_summary.md"
    global_summary = knowledge_dir / "knowledge_summary.md"
    if snapshot.exists():
        knowledge_md, source = snapshot.read_text(encoding="utf-8"), "run 目录快照"
    elif global_summary.exists():
        knowledge_md, source = global_summary.read_text(encoding="utf-8"), \
            "全局 knowledge/knowledge_summary.md（该 run 无本地快照，若此后跑过其它 run 可能与基准不符）"
    else:
        source = "无（未找到知识摘要，本次修订只带需求与反馈）"

    spec = {}
    for cand in (run_dir / "illustration_spec.json", knowledge_dir / "illustration_spec.json"):
        if cand.exists():
            try:
                spec = json.loads(cand.read_text(encoding="utf-8"))
            except ValueError:
                spec = {}
            break

    base_name, rev_index = split_rev_name(run_dir.name)
    inherited_keep, parent_feedback = [], ""
    rev_record = run_dir / "revision.json"
    if rev_record.exists():
        try:
            rec = json.loads(rev_record.read_text(encoding="utf-8"))
            inherited_keep = [str(x) for x in rec.get("must_keep_effective", [])]
            parent_feedback = str(rec.get("feedback", ""))
        except ValueError:
            logger.warning(f"{rev_record} 解析失败，链式继承的 must_keep 将丢失")

    base_image = pick_base_image(run_dir, images, refine_from)
    return RunContext(
        run_dir=run_dir, base_name=base_name, rev_index=rev_index,
        requirement=requirement, knowledge_md=knowledge_md, knowledge_source=source,
        spec=spec, mode=mode, images=images, base_image=base_image,
        inherited_keep=inherited_keep, parent_feedback=parent_feedback,
    )


# ---------- 反馈结构化（一次轻量 LLM 调用） ----------

FEEDBACK_SYSTEM = """你是考古学插图修订任务的需求分析员。你的职责是把教授的一句自然语言反馈，
拆成"必须改"与"必须保持"两组可判定的条目，供后续修订与校验使用。
原则：**不要新增教授没提的改动要求**；宁可把条目写具体，也不要写成"优化画面"这类空话。
严格输出 JSON，不要输出 JSON 以外的文字。"""

FEEDBACK_PROMPT = """## 教授的原始绘图需求
{requirement}

## 上一版插图的绘图知识摘要（用于判断反馈涉及哪些方面）
{knowledge}

## 教授本轮的人工反馈（原文）
{feedback}

请把反馈拆成三组，严格输出 JSON：
{{
  "must_change": ["必须修改的项，逐条列出，保留教授原话中的关键信息"],
  "must_keep": ["教授明确要求保持、或从反馈意图可推出必须保持的项"],
  "open": ["反馈未提及、本轮修订容易被牵连的方面（仅供提示，不要当成修改要求）"]
}}

要求：
1. must_change 每一条都要具体、可判定（例："底部比例尺从居中移到右下角"），
   不要写成"整体优化""更准确"这类无法判定的表述；
2. 教授没有明说"要保持"时，也要从反馈范围反推隐含的"不许动"范围——
   例如只要求改图内文字语言，则构图、视角、器物形制、比例、风格都应进 must_keep；
3. must_change 与 must_keep 不得语义重叠；同一条只能出现在一组里；
4. 不要在 must_change 里加入教授未要求的改动。"""


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                return obj
            except json.JSONDecodeError:
                continue
    raise KnowledgeError("模型未返回可解析的 JSON")


def _clean_list(val) -> list[str]:
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


def structure_feedback(cfg: Config, requirement: str, knowledge_md: str, feedback: str) -> dict:
    """把自然语言反馈拆成 must_change / must_keep / open。

    模型返回不可解析时**降级**为"整段反馈并入 must_change"，并在返回值里标记
    （报告必须写明降级，不能静默）。
    """
    client = config_mod.make_openai_client(
        cfg.llm_api_key, cfg.llm_base_url, timeout=cfg.llm_timeout, max_retries=cfg.llm_retries)
    try:
        resp = client.chat.completions.create(
            model=cfg.llm_model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": FEEDBACK_SYSTEM},
                {"role": "user", "content": FEEDBACK_PROMPT.format(
                    requirement=requirement,
                    knowledge=(knowledge_md or "（本 run 无知识上下文）")[:6000],
                    feedback=feedback)},
            ],
        )
        parsed = _parse_json(resp.choices[0].message.content or "")
        change = _clean_list(parsed.get("must_change"))
        keep = _clean_list(parsed.get("must_keep"))
        open_items = _clean_list(parsed.get("open"))
        if not change:
            raise KnowledgeError("反馈结构化结果里 must_change 为空")
        return {"must_change": change, "must_keep": keep, "open": open_items,
                "degraded": False, "degraded_reason": ""}
    except Exception as e:
        reason = f"{type(e).__name__}: {getattr(e, 'message', e)}"
        logger.warning(f"反馈结构化失败，降级为整段反馈并入 must_change: {reason}")
        return {"must_change": [feedback.strip()], "must_keep": [], "open": [],
                "degraded": True, "degraded_reason": reason}


# ---------- 修订提示词 ----------

REVISION_PROMPT_TEMPLATE = """## 绘图需求（原始需求，不得改动其学术要求）
{requirement}

## 绘图知识（来自参考文献，方括号内为出处，不得引入文献外的细节）
{knowledge}

## 本轮必须修改的项（逐条落实，不得遗漏）
{must_change}

## 本轮必须保持不变的项（漂移即判定不通过）
{must_keep}

## 上一版的绘图提示词（作为本次的基准，除上述必须修改项外一律不要改动）
{base_prompt}

## 硬约束
1. **除"必须修改"列出的项以外，其余内容必须与上一版保持一致**：构图、视角、器物形制、
   纹饰、比例、线条风格、标注位置等未提及的方面，一律不得主动改动或"顺手优化"。
2. 只依据上述文献知识绘图；文献未记载的细节宁可留白，不得添加。
3. 不要在画面里加入任何说明性文字块。
{text_mode_clause}"""


def build_revision_prompt(ctx: RunContext, must_change: list[str], must_keep: list[str],
                          text_mode: str, knowledge_md: str | None = None) -> str:
    """组装本次修订的绘图提示词（T1.2.3）。"""
    base_prompt = (ctx.spec.get("image_prompt_zh") or "").strip()
    if ctx.spec.get("image_prompt_en"):
        base_prompt = f"{base_prompt}\n（英文版基准提示词）{ctx.spec['image_prompt_en']}".strip()
    if not base_prompt:
        base_prompt = "（基准 run 未留下提示词，请严格按需求与知识绘图，并以基准图为唯一参照）"
    clause = knowledge.TEXT_MODE_DRAW_CLAUSES.get(
        text_mode, knowledge.TEXT_MODE_DRAW_CLAUSES["caption_only"])
    return REVISION_PROMPT_TEMPLATE.format(
        requirement=ctx.requirement,
        knowledge=(knowledge_md if knowledge_md is not None else ctx.knowledge_md)
        or "（本 run 无知识上下文，请严格按需求与基准图修订，不得补充文献外细节）",
        must_change="\n".join(f"{i}. {m}" for i, m in enumerate(must_change, 1)) or "（无）",
        must_keep="\n".join(f"{i}. {m}" for i, m in enumerate(must_keep, 1)) or "（无）",
        base_prompt=base_prompt,
        text_mode_clause=clause,
    )


# ---------- 报告 ----------

def _result_table(title: str, results: list[dict], ok_status: str) -> list[str]:
    if not results:
        return [f"### {title}", "（无）", ""]
    lines = [f"### {title}", "| # | 条目 | 判定 | 证据 |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        mark = "✅" if r["status"] == ok_status else "❌"
        item = str(r["item"]).replace("|", "／")
        ev = str(r["evidence"]).replace("|", "／")
        lines.append(f"| {i} | {item} | {mark} {r['status']} | {ev} |")
    lines.append("")
    return lines


def _write_revision_report(out_dir: Path, ctx: RunContext, structured: dict, must_change: list[str],
                           must_keep: list[str], inherited: list[str], attempts: list[dict],
                           final_ok: bool, final_image: Path | None, *,
                           text_mode: str, re_retrieve: bool, knowledge_note: str,
                           caption_table: str) -> Path:
    last = attempts[-1]["verdict"] if attempts else {}
    lines = [
        f"# 考古插图指定重绘报告（{ctx.base_name}_rev{ctx.rev_index + 1}）",
        f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "- 触发方式: **人工反馈驱动**（`--revise`，非自动重绘）",
        f"- 基准 run: {ctx.run_dir}",
        f"- 基准图: {ctx.base_image.name if ctx.base_image else '无'}",
        f"- 链式来源: {'是（基准 run 本身是一次修订，must_keep 已累积继承）' if ctx.rev_index else '否（基准为原始 run）'}",
        f"- 绘图需求: {ctx.requirement}",
        f"- 知识层模式: {ctx.mode or '未标注'}",
        f"- 知识上下文来源: {ctx.knowledge_source}",
        f"- 是否重新检索: {'是（--re-retrieve）' if re_retrieve else '否（默认，避免上下文漂移）'}",
        f"- 图内文字策略: {text_mode}",
        f"- 最终结论: {'✅ 通过（反馈项逐条落实、未指定方面未漂移）' if final_ok else '⚠️ 达到最大重试次数，仍有反馈项未落实或存在漂移（见下方逐条判定）'}",
        f"- 最终插图: {final_image.name if final_image else '无'}",
        "",
        "## 人工反馈原文",
        "```",
        structured["raw"],
        "```",
        "",
        "## 反馈结构化结果",
        f"- 状态: {'⚠️ 降级（模型返回不可解析，整段反馈并入 must_change）: ' + structured['degraded_reason'] if structured['degraded'] else '✅ 正常（模型拆分成功）'}",
        "",
        "### must_change（本轮必须改动）",
    ]
    lines += [f"{i}. {m}" for i, m in enumerate(must_change, 1)] or ["（无）"]
    lines += ["", "### must_keep（本轮必须保持）"]
    lines += [f"{i}. {m}" for i, m in enumerate(must_keep, 1)] or ["（无）"]
    if inherited:
        lines += ["", "### 继承的 must_keep（来自上一版修订，本轮同样生效）"]
        lines += [f"{i}. {m}" for i, m in enumerate(inherited, 1)]
    if structured.get("open"):
        lines += ["", "### 反馈未提及、但容易牵连的方面（仅供人工复核，未作为修改要求）"]
        lines += [f"{i}. {m}" for i, m in enumerate(structured["open"], 1)]
    lines += [
        "",
        "## 逐条判定结果（以最后一轮为准）",
    ]
    lines += _result_table("must_change 落实情况", last.get("must_change_results", []), "满足")
    lines += _result_table("must_keep 漂移检查", last.get("must_keep_results", []), "未漂移")
    if last.get("text_check") is not None:
        tc = last["text_check"]
        lines += ["### 图内文字判定", f"- 结果: {'✅ 通过' if tc.get('pass') else '❌ 不通过'}",
                  f"- 发现: {tc.get('found') or '（无）'}", f"- 说明: {tc.get('evidence', '')}", ""]
    lines += [
        "## 图注表（图版说明）",
        caption_table or "（无）",
        "",
        "## 各轮修订与校验明细",
    ]
    for a in attempts:
        v = a["verdict"]
        verdict_txt = "✅ 通过" if v["pass"] else f"❌ 未通过（评分 {v.get('score', 0)}）"
        lines.append(f"### 第 {a['n']} 轮 — {verdict_txt}")
        lines.append(f"- 图片: {a['image'].name}")
        if a.get("note"):
            lines.append(f"- 降级说明: {a['note']}")
        if a.get("degraded"):
            lines.append("- ⚠️ 本轮逐条判定存在未判定项（模型未逐条给出判定，已按保守处理计为不通过）")
        lines += _result_table("    must_change", v.get("must_change_results", []), "满足")
        lines += _result_table("    must_keep", v.get("must_keep_results", []), "未漂移")
        if v["problems"]:
            lines.append("- 问题清单:")
            lines.extend(f"  {i+1}. {p}" for i, p in enumerate(v["problems"]))
        if v.get("suggestions"):
            lines.append(f"- 修改建议: {v['suggestions']}")
        lines.append("")
    if knowledge_note:
        lines += ["## 知识上下文变化", knowledge_note, ""]
    lines += [
        "## 引用文献",
        "沿用基准 run 的知识上下文（报告见 " + str(ctx.run_dir / "report.md") + "）。",
        "",
    ]
    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


# ---------- 主编排 ----------

def parse_list_arg(raw: str | None) -> list[str]:
    """把 `--must-change`/`--must-keep` 的逗号串拆成条目（兼容中英文标点）。"""
    if not raw:
        return []
    parts = re.split(r"[,，;；\n]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _refresh_knowledge(cfg: Config, ctx: RunContext, feedback: str) -> tuple[str, dict, dict]:
    """`--re-retrieve`：重新取知识上下文（默认不启用）。返回 (knowledge_md, spec, learned)。"""
    query = f"{ctx.requirement}\n补充约束：{feedback}"
    if cfg.mode == "rag":
        from pipeline import _retrieve_knowledge
        chunks, queries, diag = _retrieve_knowledge(cfg, query)
        learned = knowledge.learn(
            cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model, ctx.requirement, [],
            chunks=chunks, text_mode=cfg.text_mode,
            timeout=cfg.knowledge_timeout, max_retries=cfg.llm_retries)
        logger.info(f"[revise] 已重新检索（{len(queries)} 组查询、命中 {len(chunks)} 片段）")
    else:
        from ingest import collect_references
        refs = collect_references(cfg.refs_dir, cfg.per_file_char_limit, scan_policy="auto", logger=logger)
        learned = knowledge.learn(
            cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model, ctx.requirement, refs,
            text_mode=cfg.text_mode, timeout=cfg.knowledge_timeout, max_retries=cfg.llm_retries)
        logger.info(f"[revise] 已重新读取 references/（{len(refs)} 篇）")
    return (learned.get("knowledge_summary_md", "") or ctx.knowledge_md,
            learned.get("illustration_spec", {}) or ctx.spec, learned)


def revise_run(
    cfg: Config, base_run: Path, feedback: str, *,
    refine_from: str = "last",
    must_change_arg: str | None = None,
    must_keep_arg: str | None = None,
    text_mode: str | None = None,
    max_attempts: int | None = None,
    re_retrieve: bool = False,
    requirement_fallback: str = "",
) -> dict:
    """对某次 run 按人工反馈重绘，返回 {ok, final_image, report, ...}。"""
    if not feedback or not feedback.strip():
        raise ReviseError("指定重绘必须给出 --feedback（教授的人工反馈原文）。")
    feedback = feedback.strip()
    mode = text_mode or cfg.text_mode
    attempts_limit = max_attempts or cfg.max_attempts

    ctx = load_run_context(Path(base_run), cfg.knowledge_dir,
                           refine_from=refine_from, requirement_fallback=requirement_fallback)
    out_dir, rev_no = next_rev_dir(cfg.output_dir, ctx.base_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx.rev_index = rev_no - 1
    logger.info(f"[revise] 基准 run: {ctx.run_dir}（基准图 {ctx.base_image.name}）→ 输出 {out_dir.name}")

    # 1) 反馈结构化
    structured = structure_feedback(cfg, ctx.requirement, ctx.knowledge_md, feedback)
    structured["raw"] = feedback

    # 2) 命令行手工指定的项优先（--must-change / --must-keep 覆盖模型拆分结果）
    cli_change = parse_list_arg(must_change_arg)
    cli_keep = parse_list_arg(must_keep_arg)
    must_change = cli_change or structured["must_change"]
    if cli_change:
        logger.info(f"[revise] must_change 由命令行指定（{len(cli_change)} 条），忽略模型拆分结果")
    # 链式继承：本轮的 keep = 继承的 keep + 本轮新增（去重保序）
    fresh_keep = cli_keep or structured["must_keep"]
    must_keep, seen = [], set()
    for k in [*ctx.inherited_keep, *fresh_keep]:
        if k not in seen:
            seen.add(k)
            must_keep.append(k)

    conflict = [x for x in must_change if x in must_keep]
    if conflict:
        raise ReviseError(
            f"--must-change 与 --must-keep 存在冲突项，请人工消歧后再跑: {conflict}")
    logger.info(f"[revise] must_change {len(must_change)} 条 / must_keep {len(must_keep)} 条"
                f"（其中继承 {len(ctx.inherited_keep)} 条）")

    # 3) 知识上下文（默认沿用基准 run，避免漂移）
    knowledge_md, spec = ctx.knowledge_md, ctx.spec
    knowledge_note = ""
    if re_retrieve:
        old = knowledge_md
        knowledge_md, spec, _ = _refresh_knowledge(cfg, ctx, feedback)
        knowledge_note = ("已按 --re-retrieve 重新检索/重读文献并重建知识摘要；"
                          "上一版知识摘要已作为比对基准送入校验（页码引用口径不变）。")
        (out_dir / "knowledge_summary.md").write_text(knowledge_md, encoding="utf-8")
        (out_dir / "illustration_spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # 快照一份到修订目录，便于链式修订与复核
        if ctx.knowledge_md:
            (out_dir / "knowledge_summary.md").write_text(ctx.knowledge_md, encoding="utf-8")
        if ctx.spec:
            (out_dir / "illustration_spec.json").write_text(
                json.dumps(ctx.spec, ensure_ascii=False, indent=2), encoding="utf-8")

    caption_table = knowledge.caption_table_md(spec.get("annotations"))
    (out_dir / "caption_table.md").write_text(
        f"# 图注表（指定重绘 {out_dir.name}）\n\n{caption_table}\n", encoding="utf-8")

    base_prompt_md = build_revision_prompt(ctx, must_change, must_keep, mode, knowledge_md)

    # 4) 生成 → 逐条校验闭环
    attempts: list[dict] = []
    final_image, final_ok = None, False
    extra_problems = ""
    for n in range(1, attempts_limit + 1):
        prompt = base_prompt_md
        if extra_problems:
            prompt += f"\n\n## 上一轮修订未落实/漂移的问题，本轮必须解决\n{extra_problems}"
        img_path = out_dir / f"illustration_attempt_{n}.png"
        logger.info(f"[revise] 第 {n}/{attempts_limit} 轮重绘（模型 {cfg.img_model}，参考图 {ctx.base_image.name}）")
        gen = generate.generate_image(
            cfg.img_api_key, cfg.img_base_url, cfg.img_model, cfg.img_size, prompt, img_path,
            timeout=cfg.image_timeout, max_retries=cfg.llm_retries, input_image=ctx.base_image)
        note = getattr(gen, "note", "")
        if note:
            logger.warning(f"[revise] {note}")

        logger.info(f"[revise] 逐条校验中（模型 {cfg.vision_model}）…")
        verdict = verify.verify_revision_image(
            cfg.vision_api_key, cfg.vision_base_url, cfg.vision_model,
            str(img_path), ctx.requirement, knowledge_md,
            must_change=must_change, must_keep=must_keep,
            previous_image=ctx.base_image,
            previous_knowledge=(ctx.knowledge_md if re_retrieve else ""),
            text_mode=mode, timeout=cfg.vision_timeout, max_retries=cfg.llm_retries,
        )
        attempts.append({"n": n, "image": img_path, "verdict": verdict, "note": note,
                         "degraded": verdict.get("verdict_degraded", False)})
        logger.info(f"[revise] 第 {n} 轮结论: {'通过' if verdict['pass'] else '未通过'}"
                    f"（must_change 满足 {sum(1 for r in verdict.get('must_change_results', []) if r['status'] == '满足')}"
                    f"/{len(must_change)}）")
        if verdict["pass"]:
            final_image, final_ok = img_path, True
            break
        lines = [f"- {p}" for p in verdict["problems"]]
        if verdict.get("suggestions"):
            lines.append(f"- 建议: {verdict['suggestions']}")
        extra_problems = "\n".join(lines)

    if final_image:
        final_path = out_dir / "final_illustration.png"
        shutil.copyfile(final_image, final_path)
        final_image = final_path

    # 5) 落盘结构化记录（供链式修订继承 must_keep）
    record = {
        "base_run": str(ctx.run_dir), "base_name": ctx.base_name,
        "rev_index": rev_no, "parent_rev_index": ctx.rev_index,
        "feedback": feedback, "feedback_degraded": structured["degraded"],
        "must_change": must_change, "must_keep_fresh": fresh_keep,
        "must_keep_inherited": ctx.inherited_keep, "must_keep_effective": must_keep,
        "open": structured.get("open", []),
        "refine_from": refine_from, "base_image": ctx.base_image.name if ctx.base_image else "",
        "re_retrieve": re_retrieve, "text_mode": mode,
        "knowledge_source": ctx.knowledge_source,
        "final_ok": final_ok,
        "final_image": final_image.name if final_image else "",
        "attempts": [{"n": a["n"], "image": a["image"].name, "pass": a["verdict"]["pass"],
                      "score": a["verdict"].get("score", 0),
                      "must_change_results": a["verdict"].get("must_change_results", []),
                      "must_keep_results": a["verdict"].get("must_keep_results", []),
                      "problems": a["verdict"].get("problems", []),
                      "suggestions": a["verdict"].get("suggestions", ""),
                      "note": a.get("note", "")} for a in attempts],
    }
    (out_dir / "revision.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    report = _write_revision_report(out_dir, ctx, structured, must_change, must_keep,
                                    ctx.inherited_keep, attempts, final_ok, final_image,
                                    text_mode=mode, re_retrieve=re_retrieve,
                                    knowledge_note=knowledge_note, caption_table=caption_table)
    logger.info(f"[revise] 报告: {report}")

    return {
        "ok": final_ok,
        "final_image": final_image,
        "report": report,
        "attempts": attempts,
        "out_dir": out_dir,
        "must_change": must_change,
        "must_keep": must_keep,
        "revision_record": out_dir / "revision.json",
    }
