# -*- coding: utf-8 -*-
"""RAGFlow 知识层客户端（P2）。

封装 RAGFlow HTTP API（OpenAPI 前缀 /api/v1，Bearer 鉴权）：
- retrieval 检索：需求/检索词 → 相关片段（content + document_name + page + similarity）
- dataset 管理：列表 / 创建
- 文档批量上传：供建库脚本与后续增量入库使用

注意：RAGFlow 版本迭代较快，不同版本字段命名有差异（如 document_name vs
document_keyword、page_number vs positions）。本模块做防御式解析，把返回
统一归一化为 {content, document_name, page, similarity, keywords}。
API 样例见 docs/RAGFLOW_ARCHITECTURE.md §6。
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from errors import RagflowError

logger = logging.getLogger("painter")


class RAGFlowClient:
    """RAGFlow 知识层 HTTP 客户端。所有失败统一抛 RagflowError。"""

    def __init__(self, base_url: str, api_key: str, timeout: int = 60):
        if not base_url or not api_key:
            raise RagflowError("RAGFlow base_url / api_key 不能为空")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # ---------- 内部 ----------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/api/v1{path}"
        try:
            resp = self._session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise RagflowError(f"RAGFlow 请求失败: {method} {path}", detail=repr(e))
        if resp.status_code != 200:
            raise RagflowError(
                f"RAGFlow 返回 HTTP {resp.status_code}: {method} {path}",
                detail=resp.text[:500],
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise RagflowError("RAGFlow 返回非 JSON 内容", detail=resp.text[:500])
        # RAGFlow 约定：code=0 成功，非 0 业务失败，message 为错误说明
        code = body.get("code", -1)
        if code != 0:
            raise RagflowError(
                f"RAGFlow 业务错误(code={code}): {body.get('message', '未知错误')}",
                detail=f"{method} {path}",
            )
        return body.get("data", {})

    @staticmethod
    def _norm_chunk(raw: dict) -> dict:
        """把不同版本的 chunk 字段归一化。page 统一为 1 基整数或 None。"""
        content = raw.get("content") or raw.get("content_with_weight") or ""
        doc_name = raw.get("document_name") or raw.get("document_keyword") or "未知文献"
        page = raw.get("page_number")
        if page is None:
            positions = raw.get("positions")
            # positions 形如 [[page, x1, y1, x2, y2], ...]，page 可能 0 基
            if positions and isinstance(positions, list) and positions:
                try:
                    page = int(positions[0][0])
                    page = page + 1 if page == 0 else page
                except (TypeError, ValueError, IndexError):
                    page = None
        try:
            similarity = float(raw.get("similarity", 0.0))
        except (TypeError, ValueError):
            similarity = 0.0
        return {
            "content": str(content).strip(),
            "document_name": str(doc_name),
            "page": int(page) if page is not None else None,
            "similarity": similarity,
            "keywords": list(raw.get("important_keywords") or []),
        }

    # ---------- 检索 ----------

    def retrieve(
        self,
        question: str,
        dataset_ids: list[str],
        top_k: int = 12,
        similarity_threshold: float = 0.2,
        page_size: int = 12,
    ) -> list[dict]:
        """检索一个查询词，返回归一化片段列表（按相似度降序）。"""
        if not dataset_ids:
            raise RagflowError("检索需要至少一个 dataset_id")
        payload = {
            "question": question,
            "dataset_ids": dataset_ids,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
            "page_size": page_size,
        }
        data = self._request("POST", "/retrieval", json=payload)
        raw_chunks = data.get("chunks") if isinstance(data, dict) else data
        chunks = [self._norm_chunk(c) for c in (raw_chunks or [])]
        chunks = [c for c in chunks if c["content"]]
        chunks.sort(key=lambda c: c["similarity"], reverse=True)
        return chunks

    # ---------- dataset 管理 ----------

    def list_datasets(self, page_size: int = 100) -> list[dict]:
        data = self._request("GET", "/datasets", params={"page": 1, "page_size": page_size})
        items = data.get("items") if isinstance(data, dict) else data
        return items or []

    def get_or_create_dataset(self, name: str, description: str = "", chunk_method: str = "paper") -> dict:
        """按名称查找 dataset，不存在则创建（embedding 模型用 RAGFlow 默认）。"""
        for ds in self.list_datasets():
            if ds.get("name") == name:
                return ds
        payload = {"name": name, "description": description, "chunk_method": chunk_method}
        data = self._request("POST", "/datasets", json=payload)
        return data if isinstance(data, dict) and data.get("id") else {}

    # ---------- 文档上传 ----------

    def upload_documents(self, dataset_id: str, file_paths: list[str | Path]) -> list[dict]:
        """批量上传文档（单次请求多文件）。返回已上传文档列表 [{id, name}]。"""
        if not file_paths:
            return []
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents"
        files = []
        opened = []
        try:
            for p in file_paths:
                fh = open(p, "rb")
                opened.append(fh)
                files.append(("file", (Path(p).name, fh)))
                # 上传用 multipart，需去掉 JSON 的 Content-Type 头
            headers = {"Authorization": f"Bearer {self.api_key}"}
            try:
                resp = self._session.post(url, files=files, headers=headers,
                                          timeout=max(self.timeout, 300))
            except requests.RequestException as e:
                raise RagflowError(f"RAGFlow 上传失败: {len(file_paths)} 个文件", detail=repr(e))
        finally:
            for fh in opened:
                fh.close()
        if resp.status_code != 200:
            raise RagflowError(
                f"RAGFlow 上传返回 HTTP {resp.status_code}", detail=resp.text[:500])
        try:
            body = resp.json()
        except ValueError:
            raise RagflowError("RAGFlow 上传返回非 JSON 内容", detail=resp.text[:500])
        if body.get("code", -1) != 0:
            raise RagflowError(
                f"RAGFlow 上传业务错误: {body.get('message', '未知错误')}")
        data = body.get("data", [])
        items = data.get("items") if isinstance(data, dict) else data
        logger.info(f"RAGFlow 已上传 {len(items or [])} 个文档到 dataset {dataset_id}")
        return items or []


def merge_chunks(chunk_lists: list[list[dict]], top_k: int) -> list[dict]:
    """多组查询的检索结果合并去重：同文献同内容视为重复，按相似度降序取 top_k。"""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for chunks in chunk_lists:
        for c in chunks:
            key = (c["document_name"], hash(c["content"][:120]))
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)
    merged.sort(key=lambda c: c["similarity"], reverse=True)
    return merged[:top_k]
