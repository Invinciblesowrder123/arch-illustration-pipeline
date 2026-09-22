# -*- coding: utf-8 -*-
"""阶段③：调用大模型学习需求 + 参考文献，产出知识摘要与绘图规格。

输出统一 JSON 结构：
{
  "knowledge_summary_md": "...",       # 知识汇总（Markdown）
  "needs_search": bool,                # 是否需要联网补充知识
  "search_queries": ["..."],           # 需要搜索的查询词
  "illustration_spec": {               # 结构化绘图规格
      "subject": "...", "period": "...", "key_elements": [...],
      "style": "...", "annotations": [...], "constraints": [...]
  },
  "image_prompt_zh": "...",            # 中文绘图提示词
  "image_prompt_en": "..."             # 英文绘图提示词（多数绘图模型英文效果更稳）
}
"""
from __future__ import annotations

import json
import logging

from openai import OpenAI

from errors import KnowledgeError

logger = logging.getLogger("painter")

SYSTEM_PROMPT = """你是考古学学术论文插图的资深顾问，精通考古类型学、地层学、器物图谱与学术出版规范。
你的任务是根据教授的绘图需求和参考文献，提炼准确的绘图知识，并给出可直接用于 AI 绘图模型的提示词。
原则：
1. 一切器物形制、纹饰、场景细节必须严格以参考文献为准，文献未提及的细节宁可留白，不得编造。
2. 学术插图讲究准确性优先于美观：比例、视角、标注位置都要符合学术论文惯例。
3. 严格输出 JSON，不要输出任何 JSON 以外的文字（包括 markdown 代码围栏以外的解释）。"""

USER_PROMPT_TEMPLATE = """## 教授的绘图需求
{requirement}

## 参考文献内容（共 {n_refs} 篇）
{refs_text}

{search_block}

请完成：
1. 汇总文献中与绘图直接相关的专业知识（knowledge_summary_md，Markdown 格式，包含：器物/场景的形制特征、时代背景、构图要素、学术标注要求）。
2. 判断仅凭上述知识能否画出学术上站得住脚的插图。若有明显知识缺口（如某器物形制描述缺失、某场景无史料支撑），设 needs_search=true 并给出 3 条以内精准的搜索查询词；若知识已足够，设 needs_search=false。
3. 产出结构化绘图规格 illustration_spec 与中/英文绘图提示词。提示词要求：主体明确、要素逐一列举、注明学术插图风格（如白描线图/科学复原图/剖面图）、比例参照、必要时包含文字标注内容。"""

REFINE_SEARCH_PROMPT_TEMPLATE = """## 教授的绘图需求
{requirement}

## 参考文献要点（首轮汇总）
{first_summary}

## 联网补充的资料
{search_text}

请基于首轮知识汇总与联网补充资料，完成与首轮相同格式的 JSON 输出：
- 更新 knowledge_summary_md（把补充资料中可靠的知识并入，标注来源 URL）
- needs_search 必须为 false
- 产出最终版 illustration_spec、image_prompt_zh、image_prompt_en
严格输出 JSON。"""


# ---------- RAG 模式（P2）：查询规划与片段注入 ----------

QUERY_PLANNER_PROMPT = """你是考古学文献检索专家。教授给出如下绘图需求，
请把它拆解为 3-5 组适合在考古学文献库中做向量+全文混合检索的查询词。

要求：
1. 覆盖不同角度：器物名（含全称与简称）、时代/文化、形制与工艺特征、场景主题。
2. 注意考古文献中同一器物可能有多种称呼（如"绿松石龙形器"常被简称"龙形器"），
   不同称呼要各出一组查询，避免单一叫法漏召。
3. 每组查询 4-12 个字，中文，不使用标点。

严格输出 JSON：{{"queries": ["查询1", "查询2", ...]}}。

## 绘图需求
{requirement}"""

CHUNKS_USER_PROMPT_TEMPLATE = """## 教授的绘图需求
{requirement}

## 从文献库检索到的相关知识片段（共 {n_chunks} 条，已按相关度排序）
{chunks_text}

以上片段全部来自真实文献，片段标题中的 [文献名 p.X] 即出处。

请完成：
1. 汇总片段中与绘图直接相关的专业知识（knowledge_summary_md，Markdown 格式）。
   **每条实质性断言必须紧跟出处标注 [文献名 p.X]**（片段未标页码的写 [文献名]）；
   片段中没有的知识宁可留白，不得编造，也不得混入你的背景知识。
2. 判断仅凭上述片段能否画出学术上站得住脚的插图。若有明显知识缺口，设
   needs_search=true 并给出 3 条以内精准的搜索查询词；若知识已足够，设 needs_search=false。
3. 产出结构化绘图规格 illustration_spec 与中/英文绘图提示词（主体明确、要素逐一列举、
   注明学术插图风格、比例参照、必要的文字标注）。"""


def format_chunks(chunks: list[dict]) -> str:
    """把检索片段格式化为带出处的文本块。"""
    blocks = []
    for i, c in enumerate(chunks, 1):
        page = f" p.{c['page']}" if c.get("page") else ""
        sim = c.get("similarity", 0.0)
        blocks.append(f"### 片段 {i} [来源: {c['document_name']}{page}]（相关度 {sim:.2f}）\n{c['content']}")
    return "\n\n".join(blocks)


def plan_queries(api_key: str, base_url: str, model: str, requirement: str) -> list[str]:
    """查询规划器：需求 → 3-5 组检索词。解析失败时降级为 [需求原文]。"""
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        raw = _chat(client, model, SYSTEM_PROMPT,
                    QUERY_PLANNER_PROMPT.format(requirement=requirement), temperature=0.2)
        queries = _parse_json(raw).get("queries") or []
    except KnowledgeError as e:
        logger.warning(f"查询规划失败，降级用需求原文作为检索词: {e.message}")
        return [requirement]
    queries = [str(q).strip() for q in queries if str(q).strip()]
    if not queries:
        return [requirement]
    return queries[:5]


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
    raise KnowledgeError("模型未返回可解析的 JSON", detail=raw[:800])


def _build_refs_content(refs: list[dict]) -> list[dict]:
    """把文献列表构造成多模态 content：普通文献走文本，视觉直读文献附逐页图片。"""
    parts: list[dict] = []
    n_imgs = 0
    for i, r in enumerate(refs, 1):
        header = f"### 文献 {i}: {r['name']}\n"
        if r.get("pages"):
            header += "（该文献为扫描版、无文字层，以下是逐页扫描图片，请直接阅读图片中的内容并作为知识依据）\n"
            parts.append({"type": "text", "text": header})
            parts.extend({"type": "image_url", "image_url": {"url": uri}} for uri in r["pages"])
            n_imgs += len(r["pages"])
        else:
            parts.append({"type": "text", "text": header + r["text"]})
    if n_imgs:
        parts.append({"type": "text", "text": f"（以上共附扫描页图片 {n_imgs} 张，请务必以图片内容为准提炼知识）"})
    return parts


def _chat(client: OpenAI, model: str, system: str, user, temperature: float = 0.3) -> str:
    """user 可为 str（纯文本）或 list[dict]（多模态 content 数组）。"""
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content
    if not content:
        raise KnowledgeError("模型返回空内容")
    return content


FIX_PROMPTS_PROMPT = """基于以下绘图知识与需求，生成可直接用于绘图模型的提示词。

## 需求
{requirement}

## 绘图知识
{knowledge}

严格输出 JSON：{{"image_prompt_zh": "中文提示词", "image_prompt_en": "English prompt"}}。"""


def _ensure_prompts(client: OpenAI, model: str, requirement: str, result: dict) -> dict:
    """校验绘图提示词字段；缺失或为空时调用模型自动补齐（比整轮重试便宜）。"""
    if result.get("image_prompt_zh") and result.get("image_prompt_en"):
        return result
    raw = _chat(client, model, SYSTEM_PROMPT, FIX_PROMPTS_PROMPT.format(
        requirement=requirement,
        knowledge=result.get("knowledge_summary_md", "") or json.dumps(result.get("illustration_spec", {}), ensure_ascii=False),
    ))
    fixed = _parse_json(raw)
    result["image_prompt_zh"] = result.get("image_prompt_zh") or fixed.get("image_prompt_zh", "")
    result["image_prompt_en"] = result.get("image_prompt_en") or fixed.get("image_prompt_en", "")
    if not result["image_prompt_en"] and not result["image_prompt_zh"]:
        raise KnowledgeError("模型未能生成有效的绘图提示词", detail=raw[:500])
    return result


def learn(
    api_key: str, base_url: str, model: str,
    requirement: str, refs: list[dict],
    search_results: list[dict] | None = None,
    chunks: list[dict] | None = None,
) -> dict:
    """知识学习主入口。

    - chunks 非空（rag 模式）：以检索片段为知识来源，断言带 [文献 p.X] 出处；
    - search_results 为 None 时为首轮（可能要求搜索），有则为定稿轮。
    """
    client = OpenAI(api_key=api_key, base_url=base_url)

    # ---- RAG 模式：检索片段通道 ----
    if chunks:
        user = CHUNKS_USER_PROMPT_TEMPLATE.format(
            requirement=requirement, n_chunks=len(chunks), chunks_text=format_chunks(chunks))
        first = _parse_json(_chat(client, model, SYSTEM_PROMPT, user))
        first.setdefault("needs_search", False)
        first.setdefault("search_queries", [])
        return _ensure_prompts(client, model, requirement, first)

    has_images = any(r.get("pages") for r in refs)

    if search_results is None:
        user = USER_PROMPT_TEMPLATE.format(
            requirement=requirement, n_refs=len(refs),
            refs_text="（见下方文献内容）" if has_images else "",
            search_block="",
        )
        if has_images:
            # 扫描版文献以逐页图片形式附在 content 数组里，交给多模态模型直读
            user_content: str | list = [{"type": "text", "text": user}, *_build_refs_content(refs)]
        else:
            user_content = user + "\n" + "\n\n".join(
                f"### 文献 {i}: {r['name']}\n{r['text']}" for i, r in enumerate(refs, 1)
            )
        first = _parse_json(_chat(client, model, SYSTEM_PROMPT, user_content))
        first.setdefault("needs_search", False)
        first.setdefault("search_queries", [])
        return _ensure_prompts(client, model, requirement, first)

    # 定稿轮：并入搜索结果
    from search import format_search_results
    first_summary = search_results.get("_first_summary", "")
    user = REFINE_SEARCH_PROMPT_TEMPLATE.format(
        requirement=requirement,
        first_summary=first_summary or "（无）",
        search_text=format_search_results(search_results["results"]),
    )
    final = _parse_json(_chat(client, model, SYSTEM_PROMPT, user))
    final["needs_search"] = False
    return _ensure_prompts(client, model, requirement, final)
