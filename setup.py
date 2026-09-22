# -*- coding: utf-8 -*-
"""首次启动向导：让用户填两个 API Key（LLM 与绘图模型分属不同分组/服务商时各自填写）。

- LLM Key：语言模型（需求理解 + 知识学习 + 读图校验），默认 aixw gpt-5.6-sol
- 绘图 Key：T2I 绘图模型，默认 aixw gpt-image-2（直接回车则复用 LLM Key）
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import DEFAULT_BASE_URL, DEFAULT_IMG_MODEL, DEFAULT_LLM_MODEL, PROJECT_ROOT

ENV_TEMPLATE = """# 本文件由首次启动向导自动生成。视觉校验模型默认复用 LLM 密钥。
LLM_BASE_URL={base_url}
LLM_API_KEY={llm_key}
LLM_MODEL={llm_model}

IMG_BASE_URL=
IMG_API_KEY={img_key}
IMG_MODEL={img_model}
IMG_SIZE=1024x1024

VISION_BASE_URL=
VISION_API_KEY=
VISION_MODEL=

MAX_ATTEMPTS=3
SEARCH_ENABLED=1
SEARCH_TOP_K=5
PER_FILE_CHAR_LIMIT=30000
"""


def _valid_key(value: str) -> bool:
    """密钥需 >= 8 位且不含中文（中文说明还是占位符）。"""
    v = value.strip()
    return len(v) >= 8 and not any("\u4e00" <= ch <= "\u9fff" for ch in v)


def _env_keys_filled(env_path: Path) -> bool:
    if not env_path.exists():
        return False
    filled: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        for name in ("LLM_API_KEY", "IMG_API_KEY"):
            if line.startswith(name + "="):
                filled[name] = line.split("=", 1)[1].strip()
    if not _valid_key(filled.get("LLM_API_KEY", "")):
        return False
    img = filled.get("IMG_API_KEY", "")
    # IMG 为空 = 复用 LLM key，视为已填；非空则必须本身有效
    return img == "" or _valid_key(img)


def _prompt_key(label: str, allow_blank_reuse: bool = False) -> str:
    suffix = "（直接回车则复用上一个 Key）" if allow_blank_reuse else ""
    while True:
        try:
            value = input(f"{label}{suffix}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(2)
        if value:
            if _valid_key(value):
                return value
            print("  密钥无效（需至少 8 个字符），请重新输入。")
        elif allow_blank_reuse:
            return ""
        else:
            print("  不能为空，请输入。")


def ensure_env(interactive_ok: bool = True) -> bool:
    """确保 .env 存在且两个密钥就绪。首次运行返回 False（调用方应接着跑连通性检查）。"""
    env_path = PROJECT_ROOT / ".env"
    if _env_keys_filled(env_path):
        return True

    print("=" * 60)
    print("首次使用，需要一次性配置两个密钥。")
    print(f"服务商: aixw（{DEFAULT_BASE_URL}）")
    print(f"语言模型: {DEFAULT_LLM_MODEL} | 绘图模型: {DEFAULT_IMG_MODEL}")
    print("（若两个模型在同一分组，两个 Key 填同一个即可；")
    print("  第二个直接回车也可复用第一个。）")
    print("=" * 60)

    if not interactive_ok or not sys.stdin.isatty():
        print("[提示] 当前是非交互环境，无法输入密钥。")
        print(f"[提示] 请手动把 .env.example 复制为 {env_path} 并填入两个密钥。")
        sys.exit(2)

    llm_key = _prompt_key("第 1/2 步 — LLM Key（gpt-5.6-sol 所在分组）")
    img_key = _prompt_key("第 2/2 步 — 绘图 Key（gpt-image-2 所在分组）", allow_blank_reuse=True) or llm_key

    env_path.write_text(
        ENV_TEMPLATE.format(base_url=DEFAULT_BASE_URL, llm_key=llm_key, img_key=img_key,
                            llm_model=DEFAULT_LLM_MODEL, img_model=DEFAULT_IMG_MODEL),
        encoding="utf-8",
    )
    print(f"已写入配置: {env_path}（密钥已保存，下次无需再填）")
    return False
