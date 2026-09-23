# -*- coding: utf-8 -*-
"""集中配置：全部来自环境变量，启动时校验、快速失败。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(Exception):
    """配置缺失或非法。"""


def _sanitize_proxy_env() -> None:
    """剔除 NO_PROXY/no_proxy 里带方括号的 IPv6 回环项（如 `[::1]`）。

    为什么必须做：Cherry Studio 等工具会往环境里注入 `NO_PROXY=...,[::1],...`，
    而 httpx 0.28 解析 no_proxy 时**在构造客户端的那一刻**就抛
    `InvalidURL: Invalid port: ':1]'`——报错与网络毫无关系，现场极难定位
    （见 docs/HANDOFF.md 坑清单）。

    只清理带方括号的写法，合法的无括号 `::1` 保留；`HTTP_PROXY`/`HTTPS_PROXY`
    一律不动——访问上游模型必须走代理。
    """
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        kept = []
        for item in raw.split(","):
            token = item.strip()
            if not token:
                continue
            # 命中 [::1] / [::1]:8080 这类带方括号的写法
            if token.startswith("[::1]") or token.startswith("[::1]:"):
                continue
            kept.append(token)
        cleaned = ",".join(kept)
        if cleaned != raw.strip():
            os.environ[name] = cleaned


# 模块导入即生效：早于任何 HTTP 客户端的构造
_sanitize_proxy_env()


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
    # 图内文字策略：caption_only=图内不出现文字、标注改走图注表；in_image=图内标注强制简体中文
    text_mode: str
    # 客户端统一超时（秒）与重试次数（T5：所有调用点都从这里取，不再各写各的）
    llm_timeout: int
    knowledge_timeout: int
    vision_timeout: int
    image_timeout: int
    llm_retries: int
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
    # 知识层模式：rag=RAGFlow 检索 200+ 篇库；local=references/ 目录直读
    mode: str
    # RAGFlow 知识层（rag 模式必填）
    ragflow_base_url: str
    ragflow_api_key: str
    ragflow_dataset_ids: list[str]
    retrieval_top_k: int
    retrieval_sim_threshold: float
    retrieval_page_size: int
    ragflow_timeout: int


# 默认服务商：aixw（OpenAI 兼容中转）
DEFAULT_BASE_URL = "https://api.aixw.org/v1"
DEFAULT_LLM_MODEL = "gpt-5.6-sol"
DEFAULT_IMG_MODEL = "gpt-image-2"

TEXT_MODES = ("caption_only", "in_image")


def make_openai_client(
    api_key: str,
    base_url: str,
    *,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> OpenAI:
    """OpenAI 兼容客户端的统一工厂（T5）。

    为什么必须集中：此前超时/重试各写各的——`check.py` 用 `CHECK_TIMEOUT`、
    `generate.py` 写死 300s、`knowledge.py` 连超时都没设。而上游（实测 aixw）
    存在风控判定把单次调用拖到 70s+ 的情况，超时设小会把"上游慢"误判成
    "链路不通"。统一工厂后，超时与重试都来自 `.env`，`--check` 可直接打印生效值。
    """
    kwargs: dict = {"api_key": api_key, "base_url": base_url}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return OpenAI(**kwargs)


def _require(name: str, fallback: str | None = None) -> str:
    val = os.environ.get(name, "").strip() or (fallback or "").strip()
    if not val:
        raise ConfigError(f"缺少必需环境变量: {name}。请复制 .env.example 为 .env 并填写。")
    return val


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ConfigError(f"{name} 必须是整数，当前值: {raw!r}")
    if val < minimum:
        raise ConfigError(f"{name} 必须 >= {minimum}，当前值: {val}")
    return val


def load_config(
    refs_dir: Path | None = None,
    output_dir: Path | None = None,
    max_attempts: int | None = None,
    no_search: bool = False,
    require_llm: bool = True,
    mode: str | None = None,
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

    # ---- 图内文字策略（T2）----
    # 考古线图的学术惯例本来就是"图内标编号、图版说明里给名称"，故默认 caption_only：
    # 图内不出现任何文字，标注信息改走图注表。文生图模型渲染中文本身不可靠，
    # 只靠提示词要求"写中文"并不保险。
    text_mode = os.environ.get("TEXT_MODE", "").strip().lower() or "caption_only"
    if text_mode not in TEXT_MODES:
        raise ConfigError(f"TEXT_MODE 非法: {text_mode}，可选 {' / '.join(TEXT_MODES)}。")

    try:
        attempts = int(max_attempts if max_attempts is not None else os.environ.get("MAX_ATTEMPTS", "3"))
        if attempts < 1:
            raise ValueError
    except ValueError:
        raise ConfigError("MAX_ATTEMPTS 必须是 >= 1 的整数。")

    # ---- 知识层模式与 RAGFlow 配置 ----
    # 模式解析优先级：显式 --mode > 环境变量 PIPELINE_MODE > 自动（RAGFlow 配置齐全则 rag，否则 local）
    env_dataset = os.environ.get("RAGFLOW_DATASET_ID", "").strip()
    ragflow_dataset_ids = [d.strip() for d in env_dataset.replace("；", ";").split(";") if d.strip()]
    ragflow_base_url = os.environ.get("RAGFLOW_BASE_URL", "").strip().rstrip("/")
    ragflow_api_key = os.environ.get("RAGFLOW_API_KEY", "").strip()
    ragflow_ready = bool(ragflow_base_url and ragflow_api_key and ragflow_dataset_ids)
    resolved_mode = (mode or os.environ.get("PIPELINE_MODE", "").strip() or ("rag" if ragflow_ready else "local")).lower()
    if resolved_mode not in {"rag", "local"}:
        raise ConfigError(f"mode 非法: {resolved_mode}，可选 rag / local。")
    if resolved_mode == "rag" and not ragflow_ready:
        raise ConfigError(
            "mode=rag 需要 RAGFlow 配置齐全：RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID。"
            "请检查 .env，或改用 --mode local。"
        )

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
        text_mode=text_mode,
        llm_timeout=_int_env("LLM_TIMEOUT", 300, minimum=10),
        knowledge_timeout=_int_env("LLM_TIMEOUT_KNOWLEDGE", 600, minimum=10),
        vision_timeout=_int_env("VISION_TIMEOUT", 300, minimum=10),
        image_timeout=_int_env("IMG_TIMEOUT", 300, minimum=10),
        llm_retries=_int_env("LLM_RETRIES", 2, minimum=0),
        max_attempts=attempts,
        search_enabled=(not no_search) and os.environ.get("SEARCH_ENABLED", "1").strip() not in {"0", "false", "False"},
        search_top_k=int(os.environ.get("SEARCH_TOP_K", "5")),
        search_proxy=os.environ.get("SEARCH_PROXY", "").strip() or None,
        per_file_char_limit=int(os.environ.get("PER_FILE_CHAR_LIMIT", "30000")),
        refs_dir=refs_dir or PROJECT_ROOT / "references",
        output_dir=output_dir or PROJECT_ROOT / "output",
        knowledge_dir=PROJECT_ROOT / "knowledge",
        log_dir=PROJECT_ROOT / "logs",
        mode=resolved_mode,
        ragflow_base_url=ragflow_base_url,
        ragflow_api_key=ragflow_api_key,
        ragflow_dataset_ids=ragflow_dataset_ids,
        retrieval_top_k=int(os.environ.get("RETRIEVAL_TOP_K", "12")),
        retrieval_sim_threshold=float(os.environ.get("RETRIEVAL_SIM_THRESHOLD", "0.2")),
        retrieval_page_size=int(os.environ.get("RETRIEVAL_PAGE_SIZE", "12")),
        ragflow_timeout=int(os.environ.get("RAGFLOW_TIMEOUT", "60")),
    )
    for d in (cfg.refs_dir, cfg.output_dir, cfg.knowledge_dir, cfg.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
