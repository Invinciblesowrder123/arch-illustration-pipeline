# -*- coding: utf-8 -*-
"""连通性自检：文本对话、视觉读图、绘图、RAGFlow 知识层四项各实测一次，替代手工排查。

用法:
  python main.py --check               # 全部检查（含真实生成一张测试图）
  python main.py --check --skip-image  # 跳过绘图测试（不产生绘图费用）
首次启动向导写入密钥后会自动执行一次。
配置了 RAGFlow（RAGFLOW_BASE_URL/API_KEY/DATASET_ID）时自动追加知识层探测。
自检开头打印**环境指纹**（T3）：两台机器的自检输出可直接逐行对比，定位"你这能跑我这不能跑"。
"""
from __future__ import annotations

import base64
import logging
import platform
import struct
import sys
import zlib
from importlib.metadata import PackageNotFoundError, version as _pkg_version

import config as config_mod
from errors import RagflowError
from ragflow_client import RAGFlowClient
from version import __version__

logger = logging.getLogger("painter")

# 探测请求超时（秒）。部分中转服务商对短输入会先做风控判定再返回，
# 实测 aixw 单次可耗 70s+，故给足余量，避免把"上游慢"误报成"链路不通"。
CHECK_TIMEOUT = 180

# 环境指纹里要打印的库（两台机器对比时最有用的那几个）
FINGERPRINT_PACKAGES = ("openai", "httpx", "httpx2", "requests", "PyMuPDF", "python-docx", "ddgs")

# 探测提示词：必须是"有实际内容的正常任务"，而不是心跳式短询问。
# 实测 aixw 上游会对短输入做风控，报
# "Upstream rejected illegal short-input distillation or heartbeat probing"（400），
# 且对重复出现的同一短提示词更敏感（1 秒内直接拒绝）。故探针使用一段真实的
# 知识整理任务（约 270 字），既能验证鉴权，也顺带验证结构化 JSON 输出能力。
_PROBE_PROMPT = (
    "你是考古学学术插图的资深顾问。下面给你一段真实的发掘简报文字，"
    "请当作一次正式的知识整理任务来完成：\n\n"
    "「二里头遗址出土的绿松石龙形器，由2000余片绿松石片粘嵌于有机物之上，"
    "龙身曲置呈匚形，吻部突出，尾尖内卷，全长约64.5厘米。出土时置于墓主人骨架右侧，"
    "头部附近有铜铃一件。」\n\n"
    "请完成：第一，提取其中所有可用于绘图的形制信息（材质、片数、形态、尺寸、出土位置）；"
    "第二，输出一个 JSON 对象，包含 subject（器物名）、elements（形制要素数组）、"
    "length_cm（数值）三个字段。请直接给出 JSON，不要附加解释。"
)

# 被风控拦截时的加长重试版（更长上下文，规避短输入判定）
_PROBE_PROMPT_EXTENDED = _PROBE_PROMPT + (
    "\n\n补充说明：这段文字来自正式出版的考古发掘报告，读者是需要据此绘制学术插图的"
    "研究人员。因此形制信息的准确性至关重要，请你逐条核对原文表述，"
    "不要把报告中未出现的纹饰、功能或年代信息补充进去——文献没写的内容宁可留空。"
)

_SHORT_INPUT_MARKERS = ("short-input distillation", "heartbeat probing")


def _pkg_versions() -> str:
    parts = []
    for name in FINGERPRINT_PACKAGES:
        try:
            parts.append(f"{name}={_pkg_version(name)}")
        except PackageNotFoundError:
            parts.append(f"{name}=未安装")
        except Exception as e:  # 元数据异常不应中断自检
            parts.append(f"{name}=读取失败({type(e).__name__})")
    return ", ".join(parts)


def env_identity() -> list[str]:
    """环境指纹（T3）：跨机器排查"你这能跑我这不能跑"时直接逐行对比。"""
    return [
        f"项目版本: arch-illustration {__version__}",
        f"Python: {sys.version.split()[0]} ({platform.python_implementation()}) "
        f"/ {platform.platform()}",
        f"关键库: {_pkg_versions()}",
    ]


def _print_env_identity(cfg=None) -> None:
    logger.info("环境指纹（两台机器可直接逐行对比）:")
    for line in env_identity():
        logger.info(f"  - {line}")
    if cfg is not None:
        logger.info("生效的客户端参数（T5，来自 .env）: "
                    f"LLM_TIMEOUT={cfg.llm_timeout}s, "
                    f"LLM_TIMEOUT_KNOWLEDGE={cfg.knowledge_timeout}s, "
                    f"VISION_TIMEOUT={cfg.vision_timeout}s, "
                    f"IMG_TIMEOUT={cfg.image_timeout}s, "
                    f"LLM_RETRIES={cfg.llm_retries}, "
                    f"CHECK_TIMEOUT={CHECK_TIMEOUT}s")
        logger.info(f"图内文字策略 TEXT_MODE={cfg.text_mode}")


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


def _is_short_input_rejection(err: Exception) -> bool:
    text = str(err)
    return any(m in text for m in _SHORT_INPUT_MARKERS)


def check_llm(cfg) -> tuple[bool, str]:
    """文本对话 + 结构化 JSON 输出能力（知识学习阶段依赖）。

    探针用一段真实的知识整理任务而非"只回复 ok"式短询问——部分中转服务商
    （实测 aixw）会对短输入做风控直接 400；若仍被拦，自动加长后重试一次。
    """
    client = config_mod.make_openai_client(cfg.llm_api_key, cfg.llm_base_url,
                                           timeout=CHECK_TIMEOUT, max_retries=cfg.llm_retries)
    attempts = [("", _PROBE_PROMPT), ("（短输入风控拦截，已加长提示后重试）", _PROBE_PROMPT_EXTENDED)]
    for idx, (note, prompt) in enumerate(attempts):
        try:
            resp = client.chat.completions.create(
                model=cfg.llm_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                return False, "模型返回空内容"
            return True, f"模型 {cfg.llm_model} 正常响应{note}"
        except Exception as e:
            if _is_short_input_rejection(e):
                if idx == 0:
                    continue  # 被风控拦截 → 换加长版重试
                return False, ("探针请求被上游风控连续拦截（提示词已加长仍被拒）——"
                               "该服务商可能限制自动化探测，请改用手动方式确认模型可用性")
            return False, f"{type(e).__name__}: {e}"
    return False, "探针未能完成（未知原因）"


def check_vision(cfg) -> tuple[bool, str]:
    """视觉读图能力（阶段⑤校验依赖）。同样避免短询问，并被拦时加长重试。"""
    client = config_mod.make_openai_client(cfg.vision_api_key, cfg.vision_base_url,
                                           timeout=CHECK_TIMEOUT, max_retries=cfg.llm_retries)
    data_uri = "data:image/png;base64," + base64.b64encode(_tiny_png()).decode()
    question = (
        "这是一张用于测试图像识别能力的图片文件，请仔细查看后依次回答：\n"
        "第一，图片的主体内容是什么形状、什么颜色；\n"
        "第二，图中是否存在可以辨认的文字、数字或符号；\n"
        "第三，如果要把这张图当作考古报告的插图占位图，你会怎么描述它的画面特征。\n"
        "请用两三句话概括回答即可，不必展开论述。"
    )
    question_extended = question + (
        "\n\n补充背景：这项测试用于确认读图链路是否可用于学术插图的自动审稿——"
        "审稿环节需要模型逐项核对插图与文献描述是否一致，因此对图像的观察必须具体、"
        "不能给出放之四海皆准的泛泛描述。"
    )
    for idx, (note, q) in enumerate((("", question), ("（短输入风控拦截，已加长提示后重试）", question_extended))):
        try:
            resp = client.chat.completions.create(
                model=cfg.vision_model,
                max_tokens=300,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": q},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ]}],
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                return False, "模型返回空内容"
            return True, f"读图正常（模型 {cfg.vision_model}）{note}"
        except Exception as e:
            if _is_short_input_rejection(e):
                if idx == 0:
                    continue
                return False, "读图探针被上游风控连续拦截（提示词已加长仍被拒）"
            return False, f"{type(e).__name__}: {e}（校验模型须支持读图；若失败请在 .env 单独配 VISION_MODEL）"
    return False, "读图探针未能完成（未知原因）"


def check_image(cfg) -> tuple[bool, str]:
    """真实生成一张小图（会产生一次绘图调用费用）。"""
    try:
        client = config_mod.make_openai_client(cfg.img_api_key, cfg.img_base_url,
                                               timeout=cfg.image_timeout, max_retries=cfg.llm_retries)
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


def check_ragflow(cfg) -> tuple[bool, str]:
    """RAGFlow 知识层探测：鉴权 + dataset 存在性 + 一次真实检索（不产生模型费用）。

    探测检索用相似度阈值 0，以便区分"通路故障"与"库内为空/尚未解析完"两种情况。
    """
    try:
        client = RAGFlowClient(cfg.ragflow_base_url, cfg.ragflow_api_key, timeout=cfg.ragflow_timeout)
        datasets = client.list_datasets()
        known = {d.get("id") for d in datasets}
        missing = [d for d in cfg.ragflow_dataset_ids if d not in known]
        if missing:
            return False, (f"配置的 dataset 不存在: {missing}（服务端现有 {len(datasets)} 个 dataset，"
                           f"请核对 RAGFLOW_DATASET_ID）")
        hits = client.retrieve("考古 器物 复原", cfg.ragflow_dataset_ids,
                               top_k=3, similarity_threshold=0.0, page_size=3)
        if not hits:
            return False, ("接口可达、dataset 存在，但库内检索零命中——"
                           "文献可能尚未完成解析入库，或该 dataset 为空")
        return True, f"知识层可达（{len(datasets)} 个 dataset，探测检索命中 {len(hits)} 片段）"
    except RagflowError as e:
        return False, (f"{e.message} | 请核对 RAGFLOW_BASE_URL / RAGFLOW_API_KEY，"
                       f"并确认本机能访问 RAGFlow 服务（服务器地址、端口、防火墙）")


def run_checks(cfg, skip_image: bool = False) -> bool:
    rag_configured = bool(cfg.ragflow_base_url and cfg.ragflow_api_key and cfg.ragflow_dataset_ids)
    total = 3 + (1 if rag_configured else 0)
    _print_env_identity(cfg)
    logger.info(f"开始连通性自检（{total} 项）…")
    results = []
    results.append(("文本模型（知识学习）", check_llm(cfg)))
    results.append(("视觉读图（审稿校验）", check_vision(cfg)))
    if skip_image:
        logger.info("  ⏭ 跳过 — 绘图模型（按要求跳过，未产生费用）")
    else:
        results.append(("绘图模型（生成插图）", check_image(cfg)))
    if rag_configured:
        results.append(("RAGFlow 知识层（rag 模式）", check_ragflow(cfg)))
    else:
        logger.info("  ⏭ 跳过 — RAGFlow 知识层（未配置，local 模式不需要。"
                    "配置 .env 的 RAGFLOW_* 三项后自动纳入自检）")

    all_ok = all(ok for _, (ok, _) in results)
    for name, (ok, detail) in results:
        _report(name, ok, detail)

    if all_ok:
        logger.info("全部通过，可以正式运行：python main.py")
    else:
        logger.error("存在失败项。请核对 .env 中的密钥与模型名；绘图/读图失败通常是该模型在当前服务商不可用。")
    return all_ok
