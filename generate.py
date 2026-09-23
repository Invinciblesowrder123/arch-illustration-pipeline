# -*- coding: utf-8 -*-
"""阶段④：调用绘图模型生成插图。

支持两种调用形态：
- 文生图（默认）：`client.images.generate`
- **图生图（T1 指定重绘用）**：带 `input_image` 时先试 `client.images.edit`，
  把上一版图片作为参考图送进去；上游不支持该接口时自动退化为纯提示词重绘，
  并把降级事实通过返回值告诉调用方（**必须写进报告**，不能静默降级）。
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

import config as config_mod
from errors import ImageGenError

logger = logging.getLogger("painter")


@dataclass
class ImageResult:
    """一次绘图调用的结果与降级事实。"""

    path: Path
    used_reference: bool = False   # 是否真的把参考图送进了上游
    note: str = ""                 # 降级说明（无降级时为空）


def _save_item(item, out_path: Path) -> None:
    if getattr(item, "b64_json", None):
        out_path.write_bytes(base64.b64decode(item.b64_json))
    elif getattr(item, "url", None):
        resp = requests.get(item.url, timeout=120)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
    else:
        raise ImageGenError("绘图模型返回中既无 b64_json 也无 url")


def _assert_written(out_path: Path) -> None:
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ImageGenError(f"图片未成功写入: {out_path}")


def generate_image(
    api_key: str, base_url: str, model: str, size: str,
    prompt: str, out_path: Path,
    *,
    timeout: int | None = None,
    max_retries: int | None = None,
    input_image: Path | str | None = None,
) -> ImageResult:
    """调用 OpenAI 兼容 images API，保存 PNG。

    带 `input_image` 时按图生图处理；上游不支持图片编辑接口时自动降级为
    纯提示词重绘（结果里的 note 会说明原因，调用方应记入报告）。
    """
    client = config_mod.make_openai_client(api_key, base_url, timeout=timeout, max_retries=max_retries)
    out_path = Path(out_path)

    if input_image is not None:
        ref = Path(input_image)
        if not ref.exists():
            raise ImageGenError(f"参考图不存在: {ref}")
        try:
            with open(ref, "rb") as fh:
                resp = client.images.edit(model=model, image=fh, prompt=prompt, n=1, size=size)
            _save_item(resp.data[0], out_path)
            _assert_written(out_path)
            logger.info(f"已按图生图生成（参考图: {ref.name}）: {out_path.name}")
            return ImageResult(path=out_path, used_reference=True)
        except Exception as e:
            note = (f"上游图片编辑接口不可用（{type(e).__name__}: {e}），"
                    f"本次已退化为纯提示词重绘（未带参考图）")
            logger.warning(note)

    try:
        kwargs = {"model": model, "prompt": prompt, "n": 1, "size": size}
        try:
            resp = client.images.generate(**kwargs, response_format="b64_json")
        except Exception:
            # gpt-image-1 等新模型不接受 response_format，回退默认调用
            resp = client.images.generate(**kwargs)
        _save_item(resp.data[0], out_path)
    except ImageGenError:
        raise
    except Exception as e:
        raise ImageGenError(f"绘图模型调用失败: {type(e).__name__}: {e}")
    _assert_written(out_path)
    note = ("上游图片编辑接口不可用，本次已退化为纯提示词重绘（未带参考图）"
            if input_image is not None else "")
    return ImageResult(path=out_path, used_reference=False, note=note)
