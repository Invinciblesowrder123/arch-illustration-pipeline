# -*- coding: utf-8 -*-
"""联网搜索补充知识（阶段③ 可选环节），失败时降级继续，不阻断流水线。"""
from __future__ import annotations

import logging

from errors import SearchError


def web_search(queries: list[str], top_k: int = 5, proxy: str | None = None) -> list[dict]:
    """返回 [{query, title, url, snippet}]。任何失败抛 SearchError，由调用方降级。

    proxy: http 代理地址（如 http://127.0.0.1:7890）。国内网络访问 startpage/brave
    等搜索引擎通常需要代理；留空则直连（读取系统环境变量代理，若设置）。
    """
    logger = logging.getLogger("painter")
    try:
        from ddgs import DDGS
    except ImportError as e:
        raise SearchError("未安装 ddgs，无法联网搜索", detail=str(e))

    results: list[dict] = []
    for q in queries:
        try:
            with DDGS(proxy=proxy) as ddgs:
                for r in ddgs.text(q, max_results=top_k):
                    results.append({
                        "query": q,
                        "title": r.get("title", ""),
                        "url": r.get("href", "") or r.get("url", ""),
                        "snippet": (r.get("body", "") or "")[:500],
                    })
        except Exception as e:  # 单个查询失败不拖垮整体
            logger.warning(f"搜索查询失败（跳过）: {q} -> {e}")
    if not results:
        raise SearchError("所有搜索查询均失败")
    return results


def format_search_results(results: list[dict]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(f"[{i}] {r['title']}\n来源: {r['url']}\n摘要: {r['snippet']}")
    return "\n\n".join(blocks)
