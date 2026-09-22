# -*- coding: utf-8 -*-
"""阶段②：扫描指定目录，解析参考文献（PDF / DOCX / TXT / MD）。

扫描版 PDF（无文字层）三级回退链：
  1. MinerU 本地解析（免费，需本机装有 MinerU，自动探测）
  2. 视觉直读：把 PDF 页面渲染成图片直接交给多模态语言模型阅读（消耗较多 token，需用户允许）
  3. 跳过该文献（警告知识完整性可能受影响）

策略由 scan_policy 控制：auto（交互询问）/ mineru / visual / skip。
"""
from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path

from errors import IngestError

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
VISUAL_MAX_PAGES = 8      # 视觉直读每篇最多渲染页数
VISUAL_DPI = 110          # 视觉直读渲染分辨率（越高越清晰、token 越多）
SCAN_TEXT_THRESHOLD = 50  # 提取字符数低于此值视为扫描版


def _extract_pdf_text(path: Path, char_limit: int) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise IngestError("未安装 PyMuPDF，无法解析 PDF。请运行: pip install -r requirements.txt", detail=str(e))
    parts: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
            if sum(len(p) for p in parts) >= char_limit:
                break
    return "".join(parts)[:char_limit]


def _render_pdf_pages(path: Path, max_pages: int, dpi: int) -> list[str]:
    """把 PDF 前 N 页渲染为 JPEG data-URI 列表（供多模态模型直读）。"""
    import fitz
    uris: list[str] = []
    with fitz.open(path) as doc:
        for page in doc[:max_pages]:
            pix = page.get_pixmap(dpi=dpi)
            jpeg = pix.tobytes("jpeg", jpg_quality=70)
            uris.append("data:image/jpeg;base64," + base64.b64encode(jpeg).decode())
    return uris


def _extract_docx(path: Path, char_limit: int) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise IngestError("未安装 python-docx，无法解析 Word。请运行: pip install -r requirements.txt", detail=str(e))
    doc = Document(str(path))
    chunks = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            chunks.append("\t".join(c.text.strip() for c in row.cells))
    return "\n".join(chunks)[:char_limit]


def _extract_text(path: Path, char_limit: int) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return path.read_text(encoding=enc)[:char_limit]
        except UnicodeDecodeError:
            continue
    raise IngestError(f"无法识别文件编码: {path.name}")


def _handle_scanned_pdfs(
    scanned: list[tuple[Path, str]],  # (路径, 初步提取的少量文本)
    scan_policy: str,
    logger: logging.Logger,
) -> dict[str, dict]:
    """对扫描版 PDF 执行回退策略，返回 {文件名: {"text": ...} 或 {"pages": [...]}}。"""
    result: dict[str, dict] = {}

    # 探测 MinerU（策略为 mineru/auto 时才探测）
    mineru = None
    if scan_policy in ("auto", "mineru"):
        from mineru_detect import detect_mineru
        mineru = detect_mineru()
        if mineru:
            logger.info(f"已检测到本机 MinerU（{mineru['source']}）: {mineru['cmd']}")

    # auto 策略：交互式让用户选；非交互环境自动降级
    policy = scan_policy
    if policy == "auto":
        names = ", ".join(p.name for p, _ in scanned)
        if sys.stdin.isatty():
            print("=" * 60)
            print(f"检测到 {len(scanned)} 篇扫描版 PDF（无文字层）: {names}")
            opts = []
            if mineru:
                opts.append("1 = MinerU 本地解析（免费，每页约 2-10 秒）")
            opts.append("2 = 视觉直读：页面渲染成图片交给多模态模型阅读（消耗较多 token）")
            opts.append("3 = 跳过这些文献")
            print("\n".join(opts))
            print("=" * 60)
            valid = [o for o in ("1", "2", "3") if o != "1" or mineru]
            while True:
                choice = input(f"选择处理方式 ({'/'.join(valid)}): ").strip()
                if choice in valid:
                    break
            policy = {"1": "mineru", "2": "visual", "3": "skip"}[choice]
        else:
            # 非交互：MinerU 可用则用，否则视觉直读兜底，再不行跳过
            policy = "mineru" if mineru else "visual"
            logger.info(f"非交互环境，扫描版 PDF 自动采用策略: {policy}")

    if policy == "skip":
        for p, _ in scanned:
            logger.warning(f"已跳过扫描版文献（知识完整性可能受影响）: {p.name}")
        return result

    if policy == "visual":
        logger.info("视觉直读模式：扫描版 PDF 页面将渲染为图片交给多模态模型阅读。")
        for p, _ in scanned:
            pages = _render_pdf_pages(p, VISUAL_MAX_PAGES, VISUAL_DPI)
            result[p.name] = {"pages": pages}
            logger.info(f"  已渲染 {len(pages)} 页: {p.name}")
        return result

    # policy == "mineru"
    if not mineru or not mineru.get("cmd"):
        raise IngestError(
            "策略要求使用 MinerU，但未在本机检测到 MinerU。"
            "请安装 MinerU（pip install mineru）或设置环境变量 MINERU_CMD 指向其可执行文件；"
            "也可改用 --scan-policy visual 走视觉直读。"
        )
    from mineru_detect import parse_pdf_with_mineru
    timeout = int(os.environ.get("MINERU_TIMEOUT", "900"))
    for p, _ in scanned:
        logger.info(f"MinerU 解析中（首次运行可能需下载模型，请耐心等待）: {p.name}")
        text, err = None, ""
        for attempt in (1, 2):
            text, err = parse_pdf_with_mineru(mineru["cmd"], p, p.parent, timeout=timeout)
            if text:
                break
            logger.warning(f"  第 {attempt} 次尝试失败: {err}")
        if text:
            result[p.name] = {"text": text[:300000]}
            logger.info(f"  ✅ MinerU 解析成功: {p.name}（{len(text)} 字）")
        else:
            logger.error(f"  ❌ MinerU 解析失败: {p.name} — {err}")
    return result


def collect_references(
    refs_dir: Path,
    char_limit: int = 30000,
    scan_policy: str = "auto",
    logger: logging.Logger | None = None,
) -> list[dict]:
    """返回 refs 列表：{path, name, ext, chars, text, pages}。pages 仅视觉直读的文献有。"""
    logger = logger or logging.getLogger("painter")
    if not refs_dir.exists():
        raise IngestError(f"参考文献目录不存在: {refs_dir}")

    files = sorted(
        p for p in refs_dir.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith("~$")
    )
    if not files:
        raise IngestError(
            f"参考文献目录为空: {refs_dir}\n"
            f"请把参考文献（支持 {'/'.join(sorted(SUPPORTED))}）放入该目录后重试。"
        )

    refs: list[dict] = []
    scanned: list[tuple[Path, str]] = []
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            text = _extract_pdf_text(f, char_limit)
            if len(text.strip()) < SCAN_TEXT_THRESHOLD:
                scanned.append((f, text))
                continue
        elif ext == ".docx":
            text = _extract_docx(f, char_limit)
        else:
            text = _extract_text(f, char_limit)
        if len(text.strip()) < SCAN_TEXT_THRESHOLD:
            raise IngestError(f"文件内容过少（疑似空文件或损坏）: {f.name}")
        refs.append({"path": str(f), "name": f.name, "ext": ext, "chars": len(text),
                     "text": text, "pages": None})

    if scanned:
        logger.info(f"发现 {len(scanned)} 篇扫描版 PDF（无文字层），启动回退链（MinerU → 视觉直读 → 跳过）")
        handled = _handle_scanned_pdfs(scanned, scan_policy, logger)
        for p, _ in scanned:
            h = handled.get(p.name)
            if h is None:
                continue  # 跳过或失败
            if "pages" in h:
                refs.append({"path": str(p), "name": p.name, "ext": ".pdf",
                             "chars": 0, "text": "", "pages": h["pages"]})
            else:
                t = h["text"]
                refs.append({"path": str(p), "name": p.name, "ext": ".pdf",
                             "chars": len(t), "text": t, "pages": None})

    if not refs:
        raise IngestError("所有参考文献都无法读取（扫描版解析失败或被跳过）。")
    return refs
