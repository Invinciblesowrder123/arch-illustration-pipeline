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

from openai import OpenAI

from errors import KnowledgeError

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


def _chat(client: OpenAI, model: str, system: str, user: str, temperature: float = 0.3) -> str:
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


def learn(
    api_key: str, base_url: str, model: str,
    requirement: str, refs: list[dict],
    search_results: list[dict] | None = None,
) -> dict:
    """知识学习主入口。无 search_results 时为首轮（可能要求搜索），有则为定稿轮。"""
    client = OpenAI(api_key=api_key, base_url=base_url)
    refs_text = "\n\n".join(
        f"### 文献 {i}: {r['name']}\n{r['text']}" for i, r in enumerate(refs, 1)
    )

    if search_results is None:
        user = USER_PROMPT_TEMPLATE.format(
            requirement=requirement, n_refs=len(refs), refs_text=refs_text, search_block=""
        )
        first = _parse_json(_chat(client, model, SYSTEM_PROMPT, user))
        first.setdefault("needs_search", False)
        first.setdefault("search_queries", [])
        return first

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
    return final
