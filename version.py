# -*- coding: utf-8 -*-
"""项目版本的唯一来源：版本号只在这里改。

发布流程：改 __version__ / __release_date__ → 同步 CHANGELOG.md 与 README 顶部版本行
→ git commit → git tag -a v<版本> -m "<版本> <一句话>" → git push origin main --tags
"""
from __future__ import annotations

__version__ = "1.1.0"
__release_date__ = "2026-09-23"
__release_name__ = "指定重绘 + 图内文字约束"

# 本次发布配套的外部依赖版本（人工核对，供部署时对照）
COMPAT = {
    "ragflow": "v0.27.2",
    "embedding_model": "BAAI/bge-m3 (TEI, Builtin provider)",
    "python": ">=3.10（开发环境 3.13）",
    "openai_sdk": ">=2.0.0,<4.0.0（实测 3.17 + httpx2 与 2.x + httpx 0.28 均可用）",
    "text_mode": "caption_only（默认）/ in_image",
}
