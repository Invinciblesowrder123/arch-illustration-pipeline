# -*- coding: utf-8 -*-
"""阶段②：扫描指定目录，解析参考文献（PDF / DOCX / TXT / MD）。"""
from __future__ import annotations

from pathlib import Path

from errors import IngestError

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


def _extract_pdf(path: Path, char_limit: int) -> str:
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


def collect_references(refs_dir: Path, char_limit: int = 30000) -> list[dict]:
    """返回 [{path, name, ext, chars, text}]，按文件名排序保证可复现。"""
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
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            text = _extract_pdf(f, char_limit)
        elif ext == ".docx":
            text = _extract_docx(f, char_limit)
        else:
            text = _extract_text(f, char_limit)
        if len(text.strip()) < 50:
            raise IngestError(f"文件内容过少（疑似扫描版 PDF 或空文件）: {f.name}")
        refs.append({"path": str(f), "name": f.name, "ext": ext, "chars": len(text), "text": text})
    return refs
