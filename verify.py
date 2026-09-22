# -*- coding: utf-8 -*-
"""阶段⑤：视觉大模型校验生成的插图是否准确、是否符合文献与知识汇总。"""
from __future__ import annotations

import base64
import mimetypes

from openai import OpenAI

from errors import VerifyError

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

VERIFY_USER_TEMPLATE = """## 教授的绘图需求
{requirement}

## 绘图知识汇总（来自参考文献）
{knowledge}

## 待审插图
（见附带图片）

请逐项核对并输出 JSON 结论。"""


def verify_image(
    api_key: str, base_url: str, model: str,
    image_path: str, requirement: str, knowledge_md: str,
) -> dict:
    mime, _ = mimetypes.guess_type(image_path)
    mime = mime or "image/png"
    b64 = base64.b64encode(open(image_path, "rb").read()).decode()
    data_uri = f"data:{mime};base64,{b64}"

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=300)
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": VERIFY_USER_TEMPLATE.format(
                        requirement=requirement, knowledge=knowledge_md)},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]},
            ],
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        raise VerifyError(f"校验模型调用失败: {type(e).__name__}: {e}")

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    import json
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
