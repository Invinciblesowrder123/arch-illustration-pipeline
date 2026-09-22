# -*- coding: utf-8 -*-
"""阶段④：调用绘图模型生成插图。"""
from __future__ import annotations

import base64
from pathlib import Path

import requests
from openai import OpenAI

from errors import ImageGenError


def _download(url: str, out_path: Path) -> None:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)


def generate_image(
    api_key: str, base_url: str, model: str, size: str,
    prompt: str, out_path: Path,
) -> Path:
    """调用 OpenAI 兼容 images API，保存 PNG 并返回路径。兼容 b64 与 url 两种返回。"""
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
    try:
        kwargs = {"model": model, "prompt": prompt, "n": 1, "size": size}
        try:
            resp = client.images.generate(**kwargs, response_format="b64_json")
        except Exception:
            # gpt-image-1 等新模型不接受 response_format，回退默认调用
            resp = client.images.generate(**kwargs)
        item = resp.data[0]
        if getattr(item, "b64_json", None):
            out_path.write_bytes(base64.b64decode(item.b64_json))
        elif getattr(item, "url", None):
            _download(item.url, out_path)
        else:
            raise ImageGenError("绘图模型返回中既无 b64_json 也无 url")
    except ImageGenError:
        raise
    except Exception as e:
        raise ImageGenError(f"绘图模型调用失败: {type(e).__name__}: {e}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ImageGenError(f"图片未成功写入: {out_path}")
    return out_path
