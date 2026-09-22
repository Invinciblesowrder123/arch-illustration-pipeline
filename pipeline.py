# -*- coding: utf-8 -*-
"""流水线编排：需求输入 → 文献摄取 → 知识学习(可搜索) → 绘图 → 校验 → 闭环重绘 → 产出。"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import generate
import knowledge
import verify
from config import Config
from errors import AppError, KnowledgeError, RagflowError, SearchError
from ingest import collect_references
from ragflow_client import RAGFlowClient, merge_chunks

logger = logging.getLogger("painter")


def _refs_block(refs: list[dict]) -> str:
    parts = []
    for r in refs:
        tag = f"{r['chars']}字" if not r.get("pages") else f"视觉直读{len(r['pages'])}页"
        parts.append(f"{r['name']}({tag})")
    return "、".join(parts)


def _retrieve_knowledge(cfg: Config, requirement: str) -> tuple[list[dict], list[str]]:
    """rag 模式阶段②：查询规划 → 逐组检索 → 合并去重。返回 (chunks, queries)。"""
    client = RAGFlowClient(cfg.ragflow_base_url, cfg.ragflow_api_key, timeout=cfg.ragflow_timeout)
    queries = knowledge.plan_queries(cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model, requirement)
    logger.info(f"[阶段②·rag] 检索规划 {len(queries)} 组查询: {queries}")
    results_per_query: list[list[dict]] = []
    for q in queries:
        try:
            hits = client.retrieve(
                q, cfg.ragflow_dataset_ids,
                top_k=cfg.retrieval_top_k,
                similarity_threshold=cfg.retrieval_sim_threshold,
                page_size=cfg.retrieval_page_size,
            )
        except RagflowError as e:
            logger.warning(f"[阶段②·rag] 查询「{q}」检索失败（跳过）: {e.message}")
            hits = []
        logger.info(f"[阶段②·rag] 「{q}」命中 {len(hits)} 片段")
        results_per_query.append(hits)
    chunks = merge_chunks(results_per_query, top_k=cfg.retrieval_top_k)
    if not chunks:
        raise RagflowError(
            "RAG 模式检索未命中任何片段：请检查 dataset 是否已入库、"
            "检索阈值是否过高（RETRIEVAL_SIM_THRESHOLD），或改用 --mode local。")
    logger.info(f"[阶段②·rag] 合并去重后共 {len(chunks)} 片段"
                f"（{len({c['document_name'] for c in chunks})} 篇文献）")
    return chunks, queries


def _citations_block(chunks: list[dict]) -> tuple[str, list[dict]]:
    """从命中片段汇总引用文献列表。返回 (markdown 文本, 去重后的文献列表)。"""
    docs: dict[str, set] = {}
    for c in chunks:
        docs.setdefault(c["document_name"], set()).add(c.get("page"))
    ordered = sorted(docs.items())
    lines = []
    for i, (name, pages) in enumerate(ordered, 1):
        pages = sorted(p for p in pages if p is not None)
        pages_txt = "，".join(f"p.{p}" for p in pages) if pages else "页码未标"
        lines.append(f"{i}. {name}（引用页码: {pages_txt}）")
    return "\n".join(lines), [{"name": n, "pages": sorted(p for p in ps if p is not None)} for n, ps in ordered]


def _write_report(
    out_dir: Path, requirement: str, refs: list[dict],
    attempts: list[dict], final_ok: bool, final_image: Path | None,
    mode: str = "local", chunks: list[dict] | None = None, queries: list[str] | None = None,
) -> Path:
    if mode == "rag":
        citations_md, _ = _citations_block(chunks or [])
        source_line = (
            f"- 知识来源: RAGFlow 检索（{len(queries or [])} 组查询，"
            f"命中 {len(chunks or [])} 个片段、{len({c['document_name'] for c in (chunks or [])})} 篇文献）"
        )
        if queries:
            source_line += f"\n- 检索词: {' / '.join(queries)}"
        citations_section = ["", "## 引用文献列表", citations_md, ""]
    else:
        source_line = f"- 参考文献({len(refs)}篇): {_refs_block(refs)}"
        citations_section = []
    lines = [
        "# 考古插图生成报告",
        f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 知识层模式: {mode}",
        f"- 绘图需求: {requirement}",
        source_line,
        f"- 最终结论: {'✅ 校验通过' if final_ok else '⚠️ 达到最大重试次数，仍有未解决问题（见下方明细）'}",
        f"- 最终插图: {final_image.name if final_image else '无'}",
        "",
        "## 绘图知识摘要",
        "见 knowledge/knowledge_summary.md",
        "",
        *citations_section,
        "## 各轮生成与校验明细",
    ]
    for a in attempts:
        verdict = "✅ 通过" if a["verdict"]["pass"] else f"❌ 未通过（评分 {a['verdict'].get('score', 0)}）"
        lines.append(f"### 第 {a['n']} 轮 — {verdict}")
        lines.append(f"- 图片: {a['image'].name}")
        if a["verdict"]["problems"]:
            lines.append("- 问题清单:")
            lines.extend(f"  {i+1}. {p}" for i, p in enumerate(a["verdict"]["problems"]))
        if a["verdict"].get("suggestions"):
            lines.append(f"- 修改建议: {a['verdict']['suggestions']}")
        lines.append("")
    report = out_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def run(cfg: Config, requirement: str, scan_policy: str = "auto") -> dict:
    """执行完整流水线，返回 {ok, final_image, report, attempts}。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = cfg.output_dir / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    refs: list[dict] = []
    chunks: list[dict] | None = None
    queries: list[str] | None = None

    if cfg.mode == "rag":
        # ---- 阶段②（rag 模式）：跳过目录摄取，改为查询规划 + RAGFlow 检索 ----
        chunks, queries = _retrieve_knowledge(cfg, requirement)
        refs = []  # rag 模式不直读 references/ 目录
    else:
        # ---- 阶段②：摄取参考文献 ----
        logger.info(f"[阶段②] 扫描参考文献目录: {cfg.refs_dir}")
        refs = collect_references(cfg.refs_dir, cfg.per_file_char_limit, scan_policy=scan_policy, logger=logger)
        for r in refs:
            kind = f"视觉直读 {len(r['pages'])} 页" if r.get("pages") else f"{r['chars']} 字"
            logger.info(f"  已读取: {r['name']}（{kind}）")
        total_chars = sum(r["chars"] for r in refs)
        n_visual = sum(1 for r in refs if r.get("pages"))
        logger.info(f"共 {len(refs)} 篇文献（其中视觉直读 {n_visual} 篇），文本合计 {total_chars} 字")

    # ---- 阶段③：知识学习 ----
    logger.info("[阶段③] 调用大模型学习需求与文献…")
    result = knowledge.learn(cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model, requirement, refs, chunks=chunks)

    # 阶段③ 的联网补充：仅 local 模式启用。rag 模式下知识来源应限于文献库，
    # 混入网络资料会破坏"断言可溯源到文献页码"的引用原则（见架构文档 §1.2）。
    if result.get("needs_search") and result.get("search_queries") and cfg.mode == "rag":
        logger.info("[阶段③] rag 模式下不启用联网补充（知识来源限于文献库）。"
                    "如需联网请改用 --mode local。")
    elif result.get("needs_search") and result.get("search_queries") and cfg.search_enabled:
        web_queries = result["search_queries"][:3]
        logger.info(f"[阶段③] 模型判定知识有缺口，联网补充: {web_queries}")
        try:
            from search import web_search
            results = web_search(web_queries, cfg.search_top_k, proxy=cfg.search_proxy)
            payload = {"results": results, "_first_summary": result.get("knowledge_summary_md", "")}
            result = knowledge.learn(
                cfg.llm_api_key, cfg.llm_base_url, cfg.llm_model,
                requirement, refs, search_results=payload,
            )
            logger.info(f"[阶段③] 已并入 {len(results)} 条联网资料")
        except SearchError as e:
            logger.warning(f"[阶段③] 联网搜索失败，降级用文献知识继续: {e.message}")
    elif result.get("needs_search"):
        logger.warning("[阶段③] 模型建议联网补充知识，但搜索已禁用，继续用现有知识。")

    knowledge_md = result.get("knowledge_summary_md", "")
    cfg.knowledge_dir.mkdir(parents=True, exist_ok=True)
    (cfg.knowledge_dir / "knowledge_summary.md").write_text(knowledge_md, encoding="utf-8")
    (cfg.knowledge_dir / "illustration_spec.json").write_text(
        json.dumps(result.get("illustration_spec", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[阶段③] 知识摘要与绘图规格已写入 {cfg.knowledge_dir}")

    # ---- 阶段④⑤：生成 + 校验闭环 ----
    attempts: list[dict] = []
    final_image: Path | None = None
    final_ok = False
    feedback = ""

    for n in range(1, cfg.max_attempts + 1):
        prompt = result.get("image_prompt_zh", "")
        prompt_en = result.get("image_prompt_en", "")
        if feedback:
            prompt = f"{prompt}\n\n上一轮审稿发现的问题，本轮必须修正：\n{feedback}"
        img_path = run_dir / f"illustration_attempt_{n}.png"
        logger.info(f"[阶段④] 第 {n}/{cfg.max_attempts} 轮绘图（模型: {cfg.img_model}, 尺寸: {cfg.img_size}）")
        generate.generate_image(cfg.img_api_key, cfg.img_base_url, cfg.img_model, cfg.img_size, prompt_en or prompt, img_path)
        logger.info(f"[阶段④] 图片已生成: {img_path.name}")

        logger.info(f"[阶段⑤] 视觉模型校验中（模型: {cfg.vision_model}）…")
        verdict = verify.verify_image(
            cfg.vision_api_key, cfg.vision_base_url, cfg.vision_model,
            str(img_path), requirement, knowledge_md + (f"\n\n上一轮问题（应已修正，请复核）:\n{feedback}" if n > 1 and feedback else ""),
        )
        attempts.append({"n": n, "image": img_path, "verdict": verdict})
        logger.info(f"[阶段⑤] 校验结论: {'通过' if verdict['pass'] else '未通过'} (评分 {verdict.get('score', 0)})")
        if not verdict["pass"]:
            for i, p in enumerate(verdict["problems"], 1):
                logger.warning(f"  问题{i}: {p}")

        if verdict["pass"]:
            final_image, final_ok = img_path, True
            break
        feedback = "\n".join(f"- {p}" for p in verdict["problems"])
        if verdict.get("suggestions"):
            feedback += f"\n- 建议: {verdict['suggestions']}"

    # ---- 阶段⑥：产出 ----
    if final_image:
        final_path = run_dir / "final_illustration.png"
        shutil.copyfile(final_image, final_path)
        final_image = final_path
        logger.info(f"[阶段⑥] 校验通过！最终插图: {final_path}")
    else:
        logger.error("[阶段⑥] 达到最大重试次数仍未通过校验，详见报告中的问题清单。")

    report = _write_report(run_dir, requirement, refs, attempts, final_ok, final_image,
                           mode=cfg.mode, chunks=chunks, queries=queries)
    logger.info(f"[阶段⑥] 报告: {report}")

    return {
        "ok": final_ok,
        "mode": cfg.mode,
        "final_image": final_image,
        "report": report,
        "attempts": attempts,
        "knowledge_summary": cfg.knowledge_dir / "knowledge_summary.md",
    }
