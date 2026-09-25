# -*- coding: utf-8 -*-
"""阶段⑤：视觉大模型校验生成的插图是否准确、是否符合文献与知识汇总。

三条校验通道：
- `verify_image`：常规出图的审稿校验（T2 起附带**图内文字语言判定**）；
- `verify_revision_image`：**指定重绘（T1）**的逐条判定——人工反馈的每一条
  必须给出"满足/部分满足/未满足 + 图面证据"，`must_keep` 的每一条必须给出
  "未漂移/漂移 + 证据"。带上一版图片时同时送入，漂移判定才有依据。
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path

import config as config_mod
from errors import VerifyError

logger = logging.getLogger("painter")

VERIFY_SYSTEM = """你是考古学论文插图的审稿人。你将看到：教授的绘图需求、经文献汇总的绘图知识、一张 AI 生成的插图。
你的职责是逐项核对插图与知识是否一致，输出严格 JSON：
{
  "pass": true/false,
  "score": 0-100,                     # 学术准确性打分
  "problems": ["具体问题1", "..."],   # pass=false 时必填，指出与知识冲突或缺失的要素
  "suggestions": "对下一轮绘图的修改建议"
}
原则：宁严勿松。凡是编造的器物形制、时代错乱的元素、缺失的关键要素、不合学术出版惯例的构图，都要指出。
严格输出 JSON，不要输出其他文字。"""

# ---- 图内文字语言判定（T2）----
TEXT_CHECK_BLOCKS = {
    "caption_only": """## 额外的强制判定项：图内文字（本次策略 caption_only）
本次要求图内**不出现任何文字**。请在 JSON 中额外给出：
  "text_check": {"pass": true/false,
                 "found": ["图中出现的每一处文字/字母/数字/仿汉字的装饰符号，逐处列出"],
                 "evidence": "这些文字出现在图面的哪个位置"}
判定规则：只要图中出现任何可辨认的文字、字母、数字或仿汉字符号 → pass=false。
注意：细引线、比例尺刻度线不算文字；纯装饰性线条不算文字。""",
    "in_image": """## 额外的强制判定项：图内文字（本次策略 in_image）
本次要求**图内标注全部为简体中文、字体端正可读**。请在 JSON 中额外给出：
  "text_check": {"pass": true/false,
                 "found": ["图中出现的每一处标注文字（如实抄录）"],
                 "evidence": "文字位置与可读性说明（是否乱码、是否伪汉字、是否英文）"}
判定规则：出现英文/其他外文、乱码、无法辨认的伪汉字、或字号小到不可读 → pass=false。
只有全部标注均为清晰可读的简体中文才判 pass=true。""",
}

# ---- 指定重绘的逐条判定（T1）----
REVISION_RULES = """## 本次为「指定重绘」：人工反馈驱动的修订版

上图是本轮新生成的版本{ref_hint}。请**在完成常规审稿之外**，额外完成两组逐条判定，
并把它们放进同一个 JSON：

  "must_change_results": [
      {{"item": "反馈原文中的这一条", "status": "满足|部分满足|未满足",
        "evidence": "图面上支撑该判定的具体位置（如'右下角引线未出现'、'画面上方1/3处的文字仍为英文'）"}}
  ],
  "must_keep_results": [
      {{"item": "必须保持的这一条", "status": "未漂移|漂移",
        "evidence": "与上一版对比的具体依据（如'构图与视角与上一版一致'）"}}
  ]

判定要求：
1. **每一条 must_change 都必须单独给出一条结果，逐条对应，不得合并、不得遗漏**；
2. 证据必须指到图面位置或与上一版的对比事实，不接受"看起来很符合""基本一致"这类空话；
3. must_keep 的漂移判定要保守：只有你能明确说出差异时才判"漂移"；
4. 除 must_change 列出的项之外，其余方面若与上一版出现了肉眼可见的变化，也要在
   must_keep_results 里反映出来（这正是"越改越跑偏"的检查点）。"""

REVISION_USER_TEMPLATE = """## 教授的绘图需求
{requirement}

## 绘图知识汇总（来自参考文献）
{knowledge}
{previous_block}
## 本轮必须改动的项（must_change）
{must_change}

## 本轮必须保持不变的项（must_keep）
{must_keep}

## 待审插图
{image_hint}

请逐条判定并输出 JSON 结论（含 must_change_results 与 must_keep_results）。"""


def _data_uri(image_path: str | Path) -> str:
    p = Path(image_path)
    if not p.exists():
        raise VerifyError(f"待审图片不存在: {p}")
    # 大图先压成 JPEG 再发：上游中转（实测 aixw，2026-09-24）对过大的 image_url
    # 会静默丢弃图片内容，模型只看到 "image_url omitted"，审稿全部退化为
    # "未收到插图"的假阴性。阈值取 400KB——1024px PNG 成图普遍 0.7~1.6MB，
    # 压到 JPEG q85 后 150~300KB，图内文字仍可辨读（实测不受影响）。
    payload = p.read_bytes()
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "image/png"
    if len(payload) > 400 * 1024:
        try:
            import fitz  # PyMuPDF，项目已依赖

            pix = fitz.Pixmap(str(p))
            if pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            payload = pix.tobytes("jpeg", jpg_quality=85)
            mime = "image/jpeg"
            logger.info("待审图片 %.0fKB 超阈值，已压缩为 JPEG %.0fKB 后送审",
                        p.stat().st_size / 1024, len(payload) / 1024)
        except Exception as e:  # 压缩失败则原样发送，不阻断审稿
            logger.warning("图片压缩失败（%s），按原图送审", e)
            payload = p.read_bytes()
            mime = mime or "image/png"
    b64 = base64.b64encode(payload).decode()
    return f"data:{mime};base64,{b64}"


def _extract_json(raw: str) -> dict:
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
                if not isinstance(obj.get("pass"), bool):
                    raise VerifyError("校验结果缺少布尔字段 pass", detail=raw[:500])
                obj.setdefault("problems", [])
                obj.setdefault("score", 0)
                obj.setdefault("suggestions", "")
                return obj
            except json.JSONDecodeError:
                continue
    raise VerifyError("校验模型未返回可解析的 JSON", detail=raw[:800])


def _call_vision(api_key: str, base_url: str, model: str, image_parts: list[dict],
                 user_text: str, system: str, *, timeout: int | None, max_retries: int | None) -> str:
    client = config_mod.make_openai_client(api_key, base_url, timeout=timeout, max_retries=max_retries)
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [{"type": "text", "text": user_text}, *image_parts]},
            ],
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        raise VerifyError(f"校验模型调用失败: {type(e).__name__}: {e}")
    if not raw.strip():
        raise VerifyError("校验模型返回空内容")
    return raw


def _apply_text_check(verdict: dict, text_mode: str | None) -> dict:
    """把图内文字判定并入结论（T2）。缺判定时按保守处理，不允许静默通过。"""
    if not text_mode:
        return verdict
    check = verdict.get("text_check")
    if not isinstance(check, dict) or not isinstance(check.get("pass"), bool):
        verdict["problems"] = list(verdict.get("problems", [])) + [
            f"（校验降级）本轮模型未给出 text_check，图内文字策略（{text_mode}）未获确认，按保守处理计为不通过"]
        verdict["pass"] = False
        verdict["text_check"] = None
        return verdict
    if not check["pass"]:
        found = check.get("found") or []
        detail = "；".join(str(f) for f in found) if found else "（未列出具体文字）"
        verdict["problems"] = list(verdict.get("problems", [])) + [
            f"图内文字不符合 {text_mode} 策略：{detail}（位置: {check.get('evidence', '未说明')}）"]
        verdict["pass"] = False
    return verdict


def _normalize_results(raw_items, expected: list[str], *, field: str) -> tuple[list[dict], bool]:
    """把模型返回的逐条判定对齐到期望清单。返回 (结果列表, 是否降级)。

    模型漏判某条时**不得当成通过**（"只写不查等于没写"）——补一条"未判定"并标记降级。
    """
    by_item: dict[str, dict] = {}
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("item", "")).strip()
        if key:
            by_item[key] = it
    results: list[dict] = []
    degraded = False
    for want in expected:
        hit = by_item.get(want.strip())
        if hit is None:
            degraded = True
            results.append({"item": want, "status": "未判定",
                            "evidence": f"校验模型未对这条给出 {field} 判定"})
        else:
            results.append({
                "item": want,
                "status": str(hit.get("status", "未判定")).strip() or "未判定",
                "evidence": str(hit.get("evidence", "")).strip() or "（模型未给证据）",
            })
    return results, degraded


def _apply_revision_checks(verdict: dict, must_change: list[str], must_keep: list[str],
                           text_mode: str | None) -> dict:
    """把 must_change / must_keep 的逐条判定并入结论，并综合出最终 pass。"""
    change_results, change_degraded = _normalize_results(
        verdict.get("must_change_results"), must_change, field="must_change")
    keep_results, keep_degraded = _normalize_results(
        verdict.get("must_keep_results"), must_keep, field="must_keep")

    problems = list(verdict.get("problems", []))
    for r in change_results:
        if r["status"] != "满足":
            problems.append(
                f"反馈项未落实（{r['status']}）：{r['item']}｜图面证据: {r['evidence']}")
    for r in keep_results:
        if r["status"] != "未漂移":
            problems.append(
                f"未指定方面疑似漂移（{r['status']}）：{r['item']}｜依据: {r['evidence']}")

    model_pass = bool(verdict.get("pass"))
    checks_ok = (all(r["status"] == "满足" for r in change_results)
                 and all(r["status"] == "未漂移" for r in keep_results))
    if model_pass and not checks_ok:
        problems.append("校验模型整体判为通过，但逐条判定未全部落实——按逐条判定结果否决（宁严勿松）")

    verdict["must_change_results"] = change_results
    verdict["must_keep_results"] = keep_results
    verdict["verdict_degraded"] = change_degraded or keep_degraded
    verdict["problems"] = problems
    verdict["pass"] = model_pass and checks_ok
    return verdict


def verify_image(
    api_key: str, base_url: str, model: str,
    image_path: str, requirement: str, knowledge_md: str,
    *,
    text_mode: str | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> dict:
    """常规审稿校验。text_mode 非空时追加图内文字判定（T2）。"""
    system = VERIFY_SYSTEM
    if text_mode in TEXT_CHECK_BLOCKS:
        system = f"{VERIFY_SYSTEM}\n\n{TEXT_CHECK_BLOCKS[text_mode]}"
    user_text = (
        "## 教授的绘图需求\n{requirement}\n\n"
        "## 绘图知识汇总（来自参考文献）\n{knowledge}\n\n"
        "## 待审插图\n（见附带图片）\n\n"
        "请逐项核对并输出 JSON 结论。"
    ).format(requirement=requirement, knowledge=knowledge_md)
    raw = _call_vision(api_key, base_url, model, [{"type": "image_url", "image_url": {"url": _data_uri(image_path)}}],
                       user_text, system, timeout=timeout, max_retries=max_retries)
    return _apply_text_check(_extract_json(raw), text_mode)


def verify_revision_image(
    api_key: str, base_url: str, model: str,
    image_path: str, requirement: str, knowledge_md: str,
    *,
    must_change: list[str], must_keep: list[str],
    previous_image: str | Path | None = None,
    previous_knowledge: str = "",
    text_mode: str | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> dict:
    """指定重绘（T1）的逐条校验：must_change 必须落实、must_keep 不得漂移。"""
    system = f"{VERIFY_SYSTEM}\n\n{REVISION_RULES.format(ref_hint='，上图为上一版可供比对' if previous_image else '')}"
    if text_mode in TEXT_CHECK_BLOCKS:
        system = f"{system}\n\n{TEXT_CHECK_BLOCKS[text_mode]}"

    image_parts = []
    if previous_image:
        image_parts.append({"type": "text", "text": "【上一版插图】"})
        image_parts.append({"type": "image_url", "image_url": {"url": _data_uri(previous_image)}})
        image_hint = "（见附带图片：第一张为上一版，第二张为本轮新版，请逐项比对）"
    else:
        image_hint = "（见附带图片：本轮新版）"
    image_parts.append({"type": "text", "text": "【本轮新版插图】"})
    image_parts.append({"type": "image_url", "image_url": {"url": _data_uri(image_path)}})

    previous_block = ""
    if previous_knowledge.strip():
        previous_block = ("\n## 上一版的知识上下文（仅供比对是否漂移，不要据此评判对错）\n"
                          f"{previous_knowledge.strip()}\n")
    user_text = REVISION_USER_TEMPLATE.format(
        requirement=requirement,
        knowledge=knowledge_md,
        previous_block=previous_block,
        must_change="\n".join(f"{i}. {m}" for i, m in enumerate(must_change, 1)) or "（无）",
        must_keep="\n".join(f"{i}. {m}" for i, m in enumerate(must_keep, 1)) or "（无）",
        image_hint=image_hint,
    )
    raw = _call_vision(api_key, base_url, model, image_parts, user_text, system,
                       timeout=timeout, max_retries=max_retries)
    verdict = _apply_text_check(_extract_json(raw), text_mode)
    return _apply_revision_checks(verdict, must_change, must_keep, text_mode)
