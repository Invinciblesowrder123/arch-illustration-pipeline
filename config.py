# -*- coding: utf-8 -*-
"""集中配置：全部来自环境变量，启动时校验、快速失败。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """配置缺失或非法。"""


@dataclass
class Config:
    # 文本大模型（知识学习 / 需求理解）
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    # 绘图模型
    img_base_url: str
    img_api_key: str
    img_model: str
    img_size: str
    # 视觉校验模型（默认复用文本模型）
    vision_base_url: str
    vision_api_key: str
    vision_model: str
    # 流程参数
    max_attempts: int
    search_enabled: bool
    search_top_k: int
    search_proxy: str | None
    per_file_char_limit: int
    refs_dir: Path
    output_dir: Path
    knowledge_dir: Path
    log_dir: Path


# 默认服务商：aixw（OpenAI 兼容中转）
DEFAULT_BASE_URL = "https://api.aixw.org/v1"
DEFAULT_LLM_MODEL = "gpt-5.6-sol"
DEFAULT_IMG_MODEL = "gpt-image-2"


def _require(name: str, fallback: str | None = None) -> str:
    val = os.environ.get(name, "").strip() or (fallback or "").strip()
    if not val:
        raise ConfigError(f"缺少必需环境变量: {name}。请复制 .env.example 为 .env 并填写。")
    return val


def load_config(
    refs_dir: Path | None = None,
    output_dir: Path | None = None,
    max_attempts: int | None = None,
    no_search: bool = False,
    require_llm: bool = True,
) -> Config:
    load_dotenv(PROJECT_ROOT / ".env")

    llm_base_url = _require("LLM_BASE_URL", DEFAULT_BASE_URL)
    llm_api_key = _require("LLM_API_KEY") if require_llm else os.environ.get("LLM_API_KEY", "").strip()
    llm_model = _require("LLM_MODEL", DEFAULT_LLM_MODEL)

    img_base_url = os.environ.get("IMG_BASE_URL", "").strip() or llm_base_url
    img_api_key = os.environ.get("IMG_API_KEY", "").strip() or llm_api_key
    img_model = os.environ.get("IMG_MODEL", "").strip() or DEFAULT_IMG_MODEL
    img_size = os.environ.get("IMG_SIZE", "").strip() or "1024x1024"

    vision_base_url = os.environ.get("VISION_BASE_URL", "").strip() or llm_base_url
    vision_api_key = os.environ.get("VISION_API_KEY", "").strip() or llm_api_key
    vision_model = os.environ.get("VISION_MODEL", "").strip() or llm_model

    if img_size not in {"1024x1024", "1024x1536", "1536x1024", "512x512", "1792x1024", "1024x1792"}:
        raise ConfigError(f"IMG_SIZE 非法: {img_size}，可选 1024x1024 / 1024x1536 / 1536x1024 等。")

    try:
        attempts = int(max_attempts if max_attempts is not None else os.environ.get("MAX_ATTEMPTS", "3"))
        if attempts < 1:
            raise ValueError
    except ValueError:
        raise ConfigError("MAX_ATTEMPTS 必须是 >= 1 的整数。")

    cfg = Config(
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        img_base_url=img_base_url,
        img_api_key=img_api_key,
        img_model=img_model,
        img_size=img_size,
        vision_base_url=vision_base_url,
        vision_api_key=vision_api_key,
        vision_model=vision_model,
        max_attempts=attempts,
        search_enabled=(not no_search) and os.environ.get("SEARCH_ENABLED", "1").strip() not in {"0", "false", "False"},
        search_top_k=int(os.environ.get("SEARCH_TOP_K", "5")),
        search_proxy=os.environ.get("SEARCH_PROXY", "").strip() or None,
        per_file_char_limit=int(os.environ.get("PER_FILE_CHAR_LIMIT", "30000")),
        refs_dir=refs_dir or PROJECT_ROOT / "references",
        output_dir=output_dir or PROJECT_ROOT / "output",
        knowledge_dir=PROJECT_ROOT / "knowledge",
        log_dir=PROJECT_ROOT / "logs",
    )
    for d in (cfg.refs_dir, cfg.output_dir, cfg.knowledge_dir, cfg.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
