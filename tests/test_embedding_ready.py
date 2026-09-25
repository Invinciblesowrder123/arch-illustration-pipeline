# -*- coding: utf-8 -*-
"""embedding 预检 mock 单测：三态判定 + rag 模式缺模型时阻断并给出指引。

不访问网络、不调用真实模型。运行：
  .venv/Scripts/python.exe -m unittest discover -s tests -t .
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import embedding_guide
import pipeline
from errors import RagflowError
from ragflow_client import RAGFlowClient


def _client() -> RAGFlowClient:
    return RAGFlowClient("http://ragflow.test", "sk-rf", timeout=5)


def _stub_request(models_data: dict, providers_data: list):
    """按 path 分别返回 /users/me/models 与 /providers 的 data。"""

    def fake(method, path, **kwargs):
        if path == "/users/me/models":
            return models_data
        if path == "/providers":
            return {"data": providers_data}
        raise AssertionError(f"未预期的请求: {path}")

    return fake


class TestCheckEmbeddingReady(unittest.TestCase):
    def test_ready_when_provider_registered(self):
        c = _client()
        with patch.object(c, "_request", side_effect=_stub_request(
                {"embd_id": "embedding-3@zhipu-main@ZHIPU-AI"}, [{"name": "ZHIPU-AI"}])):
            st = c.check_embedding_ready()
        self.assertEqual(st["status"], "ready")
        self.assertIn("ZHIPU-AI", st["providers"])

    def test_missing_when_not_set(self):
        c = _client()
        with patch.object(c, "_request", side_effect=_stub_request(
                {"embd_id": ""}, [{"name": "ZHIPU-AI"}])):
            st = c.check_embedding_ready()
        self.assertEqual(st["status"], "missing")
        self.assertIn("未设置", st["reason"])

    def test_missing_when_provider_not_registered(self):
        c = _client()
        with patch.object(c, "_request", side_effect=_stub_request(
                {"embd_id": "embedding-3@zhipu-main@ZHIPU-AI"}, [])):
            st = c.check_embedding_ready()
        self.assertEqual(st["status"], "missing")
        self.assertIn("ZHIPU-AI", st["reason"])

    def test_local_when_builtin(self):
        c = _client()
        with patch.object(c, "_request", side_effect=_stub_request(
                {"embd_id": "BAAI/bge-m3@Builtin"}, [])):
            st = c.check_embedding_ready()
        self.assertEqual(st["status"], "local")

    def test_local_when_bare_model_name(self):
        c = _client()
        with patch.object(c, "_request", side_effect=_stub_request(
                {"embd_id": "BAAI/bge-m3"}, [])):
            st = c.check_embedding_ready()
        self.assertEqual(st["status"], "local")


class TestEnsureEmbeddingReady(unittest.TestCase):
    def _mock_client(self, status: str, reason: str = "测试原因") -> MagicMock:
        c = MagicMock()
        c.check_embedding_ready.return_value = {
            "status": status, "reason": reason,
            "tenant_embd": "embedding-3@zhipu-main@ZHIPU-AI", "providers": ["ZHIPU-AI"],
        }
        return c

    def test_ready_passes(self):
        pipeline._ensure_embedding_ready(self._mock_client("ready"))

    def test_local_only_warns(self):
        pipeline._ensure_embedding_ready(self._mock_client("local"))

    def test_missing_raises_with_guide(self):
        with self.assertRaises(RagflowError) as ctx:
            pipeline._ensure_embedding_ready(self._mock_client("missing", "未登记 provider"))
        detail = ctx.exception.detail or ""
        self.assertIn("ZHIPU-AI", detail)
        self.assertIn("embedding-init", detail)


class TestGuideContent(unittest.TestCase):
    def test_guide_mentions_key_hints(self):
        text = embedding_guide.render_setup_guide("测试")
        self.assertIn("embedding 模型", text)
        self.assertIn("embedding-init", text)
        self.assertIn("ZHIPU-AI", text)
        # 迁移省事的那条（同模型可复用旧向量）应该在推荐里
        self.assertIn("BAAI/bge-m3", text)

    def test_recommendations_have_required_fields(self):
        for p in embedding_guide.PROVIDER_RECOMMENDATIONS:
            for k in ("factory", "model", "price", "dim", "when", "url"):
                self.assertIn(k, p, f"{p.get('factory')} 缺字段 {k}")


if __name__ == "__main__":
    unittest.main()
