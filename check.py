# -*- coding: utf-8 -*-
"""连通性自检：文本对话、视觉读图、绘图三个环节各实测一次，替代手工排查。

用法:
  python main.py --check               # 全部检查（含真实生成一张测试图）
  python main.py --check --skip-image  # 跳过绘图测试（不产生绘图费用）
首次启动向导写入密钥后会自动执行一次。
"""
from __future__ import annotations

import base64
import logging
import struct
import zlib

from openai import OpenAI

logger = logging.getLogger("painter")


def _tiny_png(w: int = 64, h: int = 64, rgb: tuple = (200, 60, 60)) -> bytes:
    """程序生成一张真实的小 PNG（1x1 会被部分服务商判为无效图）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _report(name: str, ok: bool, detail: str) -> None:
    mark = "✅ 通过" if ok else "❌ 失败"
    logger.info(f"  {mark} — {name}" + (f"：{detail}" if detail else ""))


def check_llm(cfg) -> tuple[bool, str]:
    """文本对话 + 结构化输出能力（知识学习阶段依赖）。"""
    try:
        client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url, timeout=60)
        resp = client.chat.completions.create(
            model=cfg.llm_model,
            max_tokens=200,
            messages=[{"role": "user", "content": '只回复 JSON: {"ok": true}'}],
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return False, "模型返回空内容"
        return True, f"模型 {cfg.llm_model} 正常响应"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_vision(cfg) -> tuple[bool, str]:
    """视觉读图能力（阶段⑤校验依赖）。"""
    try:
        client = OpenAI(api_key=cfg.vision_api_key, base_url=cfg.vision_base_url, timeout=60)
        data_uri = "data:image/png;base64," + base64.b64encode(_tiny_png()).decode()
        resp = client.chat.completions.create(
            model=cfg.vision_model,
            max_tokens=200,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "这张图片是什么颜色？只回答颜色词。"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}],
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return False, "模型返回空内容"
        return True, f"读图正常（模型 {cfg.vision_model}）"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}（校验模型须支持读图；若失败请在 .env 单独配 VISION_MODEL）"


def check_image(cfg) -> tuple[bool, str]:
    """真实生成一张小图（会产生一次绘图调用费用）。"""
    try:
        client = OpenAI(api_key=cfg.img_api_key, base_url=cfg.img_base_url, timeout=300)
        resp = client.images.generate(
            model=cfg.img_model,
            prompt="a simple black line drawing of a pottery vase, academic illustration style",
            n=1, size=cfg.img_size,
        )
        item = resp.data[0]
        has_data = bool(getattr(item, "b64_json", None) or getattr(item, "url", None))
        if not has_data:
            return False, "返回中既无 b64_json 也无 url"
        return True, f"绘图模型 {cfg.img_model} 正常（尺寸 {cfg.img_size}）"
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "not available for this group" in str(e):
            msg += (
                " | 该密钥所属分组未开通此绘图模型：请到服务商控制台把令牌换到"
                "包含该模型的分组（或新建令牌/联系站方开通）；"
                "也可在 .env 的 IMG_BASE_URL/IMG_API_KEY/IMG_MODEL 三行换其他绘图服务商。"
            )
        return False, msg


def run_checks(cfg, skip_image: bool = False) -> bool:
    logger.info("开始连通性自检（3 项）…")
    results = []
    results.append(("文本模型（知识学习）", check_llm(cfg)))
    results.append(("视觉读图（审稿校验）", check_vision(cfg)))
    if skip_image:
        logger.info("  ⏭ 跳过 — 绘图模型（按要求跳过，未产生费用）")
    else:
        results.append(("绘图模型（生成插图）", check_image(cfg)))

    all_ok = all(ok for _, (ok, _) in results)
    for name, (ok, detail) in results:
        _report(name, ok, detail)

    if all_ok:
        logger.info("全部通过，可以正式运行：python main.py")
    else:
        logger.error("存在失败项。请核对 .env 中的密钥与模型名；绘图/读图失败通常是该模型在当前服务商不可用。")
    return all_ok
