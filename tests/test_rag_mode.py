# -*- coding: utf-8 -*-
"""P2 RAG 模式 mock 单测：不访问网络、不调用真实模型。

运行：在项目根目录执行
  .venv/Scripts/python.exe -m unittest discover -s tests -t .
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config import Config
from errors import RagflowError
from ragflow_client import RAGFlowClient, merge_chunks


def _fake_config(mode: str = "rag") -> Config:
    tmp = Path(tempfile.mkdtemp())
    return Config(
        llm_base_url="http://llm.test/v1", llm_api_key="sk-llm", llm_model="test-llm",
        img_base_url="http://img.test/v1", img_api_key="sk-img", img_model="test-img",
        img_size="1024x1024",
        vision_base_url="http://llm.test/v1", vision_api_key="sk-llm", vision_model="test-llm",
        text_mode="caption_only",
        llm_timeout=300, knowledge_timeout=600, vision_timeout=300, image_timeout=300, llm_retries=2,
        max_attempts=1, search_enabled=False, search_top_k=5, search_proxy=None,
        per_file_char_limit=30000,
        refs_dir=tmp / "references", output_dir=tmp / "output",
        knowledge_dir=tmp / "knowledge", log_dir=tmp / "logs",
        mode=mode,
        ragflow_base_url="http://ragflow.test", ragflow_api_key="sk-rf",
        ragflow_dataset_ids=["ds1"], retrieval_top_k=5,
        retrieval_sim_threshold=0.2, retrieval_page_size=5, ragflow_timeout=10,
        ragflow_retries=2, ragflow_retry_backoff=0, ragflow_embedding_ref="",
    )


def _client() -> RAGFlowClient:
    return RAGFlowClient("http://ragflow.test", "sk-rf", timeout=5)


def _ok_response(data) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"code": 0, "data": data}
    return resp


class TestNormChunk(unittest.TestCase):
    def test_page_number_field(self):
        c = RAGFlowClient._norm_chunk({
            "content": "龙身呈匚形", "document_name": "发掘报告.pdf",
            "page_number": 3, "similarity": 0.71,
        })
        self.assertEqual(c["page"], 3)
        self.assertEqual(c["document_name"], "发掘报告.pdf")
        self.assertAlmostEqual(c["similarity"], 0.71)

    def test_positions_fallback_0based(self):
        c = RAGFlowClient._norm_chunk({
            "content_with_weight": "绿松石镶嵌", "document_keyword": "简报.pdf",
            "positions": [[4, 10, 20, 30, 40]], "similarity": "0.55",
        })
        self.assertEqual(c["content"], "绿松石镶嵌")
        self.assertEqual(c["document_name"], "简报.pdf")
        self.assertEqual(c["page"], 5)  # positions 为 0 基（源码核验），一律 +1
        self.assertAlmostEqual(c["similarity"], 0.55)

    def test_positions_first_page(self):
        # 首页：positions 为 0 → 转为 1
        c = RAGFlowClient._norm_chunk({"content": "x", "positions": [[0, 1, 2, 3, 4]]})
        self.assertEqual(c["page"], 1)

    def test_no_page(self):
        c = RAGFlowClient._norm_chunk({"content": "x"})
        self.assertIsNone(c["page"])
        self.assertEqual(c["document_name"], "未知文献")


class TestRetrieve(unittest.TestCase):
    def test_retrieve_parses_and_sorts(self):
        client = _client()
        raw = {"chunks": [
            {"content": "低分片段", "document_name": "b.pdf", "page_number": 2, "similarity": 0.3},
            {"content": "高分片段", "document_name": "a.pdf", "page_number": 1, "similarity": 0.9},
            {"content": "", "document_name": "c.pdf", "similarity": 0.9},  # 空内容应被过滤
        ]}
        client._session = MagicMock()
        client._session.request.return_value = _ok_response(raw)
        chunks = client.retrieve("绿松石龙形器", ["ds1"], top_k=2,
                                 similarity_threshold=0.2, page_size=2)
        self.assertEqual([c["content"] for c in chunks], ["高分片段", "低分片段"])
        payload = client._session.request.call_args.kwargs["json"]
        self.assertEqual(payload["dataset_ids"], ["ds1"])
        self.assertEqual(payload["knn_top_k"], 2)  # v0.27 起改名为 knn_top_k
        self.assertEqual(payload["similarity_threshold"], 0.2)

    def test_retries_transient_retrieval_http_error(self):
        client = RAGFlowClient("http://ragflow.test", "sk-rf", timeout=5,
                               retries=2, retry_backoff=0)
        client._session = MagicMock()
        bad = MagicMock(status_code=503, text="temporarily unavailable")
        good = _ok_response({"chunks": [{"content": "命中", "document_name": "a.pdf",
                                           "page_number": 1, "similarity": 0.8}]})
        client._session.request.side_effect = [bad, bad, good]
        chunks = client.retrieve("q", ["ds1"])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(client._session.request.call_count, 3)

    def test_does_not_retry_upload_or_parse_style_post(self):
        client = _client()
        client._session = MagicMock()
        resp = MagicMock(status_code=503, text="temporarily unavailable")
        client._session.request.return_value = resp
        with self.assertRaises(RagflowError):
            client._request("POST", "/datasets/ds1/documents/parse", json={"document_ids": ["d1"]})
        self.assertEqual(client._session.request.call_count, 1)

    def test_business_error_raises(self):
        client = _client()
        client._session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"code": 100, "message": "无权限"}
        client._session.request.return_value = resp
        with self.assertRaises(RagflowError):
            client.retrieve("q", ["ds1"])

    def test_http_error_raises(self):
        client = _client()
        client._session = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "unauthorized"
        client._session.request.return_value = resp
        with self.assertRaises(RagflowError):
            client.retrieve("q", ["ds1"])

    def test_empty_dataset_ids(self):
        with self.assertRaises(RagflowError):
            _client().retrieve("q", [])


class TestMergeChunks(unittest.TestCase):
    def test_merge_dedupe_and_cap(self):
        c1 = {"content": "甲" * 200, "document_name": "a.pdf", "page": 1, "similarity": 0.9, "keywords": []}
        c1_dup = {"content": "甲" * 200, "document_name": "a.pdf", "page": 1, "similarity": 0.8, "keywords": []}
        c2 = {"content": "乙", "document_name": "b.pdf", "page": None, "similarity": 0.7, "keywords": []}
        c3 = {"content": "丙", "document_name": "c.pdf", "page": 3, "similarity": 0.6, "keywords": []}
        merged = merge_chunks([[c1, c2], [c1_dup, c3]], top_k=3)
        self.assertEqual(len(merged), 3)  # c1_dup 与 c1 去重
        self.assertEqual(merged[0]["content"], "甲" * 200)
        self.assertEqual([m["similarity"] for m in merged], [0.9, 0.7, 0.6])

    def test_top_k_cut(self):
        chunks = [{"content": f"片段{i}", "document_name": "a.pdf", "page": None,
                   "similarity": 0.5 + i / 100, "keywords": []} for i in range(5)]
        self.assertEqual(len(merge_chunks([chunks], top_k=2)), 2)


# ---------- knowledge 层 ----------

_FAKE_LLM_JSON = json.dumps({
    "knowledge_summary_md": "## 知识\n- 龙身呈匚形 [发掘报告.pdf p.3]",
    "needs_search": False, "search_queries": [],
    "illustration_spec": {"subject": "绿松石龙形器"},
    "image_prompt_zh": "白描线图…", "image_prompt_en": "line drawing…",
}, ensure_ascii=False)


def _fake_openai(content: str):
    """返回可替换 knowledge._client 的工厂，chat 固定返回 content。"""
    def factory(api_key=None, base_url=None, **kwargs):
        m = MagicMock()
        m.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))])
        return m
    return factory


class TestPlanQueries(unittest.TestCase):
    def test_plan_ok(self):
        import knowledge
        content = json.dumps({"queries": ["绿松石龙形器", "龙形器 二里头", "绿松石 镶嵌 工艺"]}, ensure_ascii=False)
        with patch.object(knowledge, "_client", _fake_openai(content)):
            queries = knowledge.plan_queries("k", "u", "m", "画绿松石龙形器")
        self.assertEqual(len(queries), 3)

    def test_plan_fallback_on_bad_json(self):
        import knowledge
        with patch.object(knowledge, "_client", _fake_openai("不是 JSON")):
            queries = knowledge.plan_queries("k", "u", "m", "画绿松石龙形器")
        self.assertEqual(queries, ["画绿松石龙形器"])


class TestLearnWithChunks(unittest.TestCase):
    def test_chunks_prompt_carries_citation(self):
        import knowledge
        captured = {}

        def fake_chat(client, model, system, user, temperature=0.3):
            captured["user"] = user
            return _FAKE_LLM_JSON

        chunks = [{"content": "龙身呈匚形", "document_name": "发掘报告.pdf",
                   "page": 3, "similarity": 0.8, "keywords": []}]
        with patch.object(knowledge, "_chat", fake_chat), \
             patch.object(knowledge, "_client", _fake_openai(_FAKE_LLM_JSON)):
            result = knowledge.learn("k", "u", "m", "画龙形器", refs=[], chunks=chunks)
        self.assertIn("[来源: 发掘报告.pdf p.3]", captured["user"])
        self.assertIn("每条实质性断言必须紧跟出处标注", captured["user"])
        self.assertEqual(result["image_prompt_en"], "line drawing…")

    def test_empty_chunks_falls_back_to_refs_path(self):
        import knowledge
        with patch.object(knowledge, "_client", _fake_openai(_FAKE_LLM_JSON)), \
             patch.object(knowledge, "_chat", return_value=_FAKE_LLM_JSON):
            result = knowledge.learn("k", "u", "m", "需求", refs=[])
        self.assertIn("image_prompt_zh", result)


# ---------- pipeline rag 分支 ----------

class TestPipelineRagMode(unittest.TestCase):
    def _run(self, mode: str, tmp: Path):
        import pipeline

        cfg = _fake_config(mode)
        cfg.output_dir = tmp / "out"
        cfg.knowledge_dir = tmp / "kn"

        chunks_q1 = [
            {"content": "龙身呈匚形，吻部突出", "document_name": "发掘报告.pdf", "page": 3,
             "similarity": 0.85, "keywords": []},
            {"content": "绿松石片约2000余片", "document_name": "简报.pdf", "page": 5,
             "similarity": 0.7, "keywords": []},
        ]
        chunks_q2 = [
            {"content": "龙身呈匚形，吻部突出", "document_name": "发掘报告.pdf", "page": 3,
             "similarity": 0.6, "keywords": []},  # 重复片段
        ]

        fake_rf = MagicMock()
        fake_rf.retrieve.side_effect = lambda q, *a, **kw: (chunks_q1 if q == "q1" else chunks_q2)

        learned = {"knowledge_summary_md": "知识", "needs_search": False, "search_queries": [],
                   "illustration_spec": {}, "image_prompt_zh": "中", "image_prompt_en": "en"}

        with patch.object(pipeline.knowledge, "plan_queries", return_value=["q1", "q2"]), \
             patch.object(pipeline, "RAGFlowClient", return_value=fake_rf), \
             patch.object(pipeline.knowledge, "learn", return_value=learned) as mock_learn, \
             patch.object(pipeline.generate, "generate_image",
                          side_effect=lambda _k, _u, _m, _s, _p, path, **kw: Path(path).write_bytes(b"\x89PNG fake")) as mock_gen, \
             patch.object(pipeline.verify, "verify_image",
                          return_value={"pass": True, "score": 9, "problems": [], "suggestions": ""}):
            if mode == "local":
                with patch.object(pipeline, "collect_references",
                                  return_value=[{"name": "a.pdf", "ext": ".pdf", "chars": 100, "text": "内容"}]) as mock_refs:
                    result = pipeline.run(cfg, "画绿松石龙形器")
                mock_refs.assert_called_once()
            else:
                result = pipeline.run(cfg, "画绿松石龙形器")

        if mode == "rag":
            # learn 应收到去重后的 chunks（3 条去 1 条重复 = 2 条），refs 为空
            self.assertEqual(len(mock_learn.call_args.kwargs.get("chunks") or []), 2)
            self.assertEqual(mock_learn.call_args.args[4], [])  # refs 为空列表
            self.assertEqual(fake_rf.retrieve.call_count, 2)
        else:
            self.assertIsNone(mock_learn.call_args.kwargs.get("chunks"))
        mock_gen.assert_called_once()
        return result, cfg

    def test_rag_branch(self):
        with tempfile.TemporaryDirectory() as td:
            result, cfg = self._run("rag", Path(td))
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "rag")
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("知识层模式: rag", report)
            self.assertIn("引用文献列表", report)
            self.assertIn("发掘报告.pdf", report)
            self.assertIn("p.3", report)
            self.assertIn("简报.pdf", report)
            # 知识摘要照常落盘
            self.assertTrue((cfg.knowledge_dir / "knowledge_summary.md").exists())

    def test_local_branch_regression(self):
        with tempfile.TemporaryDirectory() as td:
            result, cfg = self._run("local", Path(td))
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "local")
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("知识层模式: local", report)
            self.assertIn("a.pdf", report)
            self.assertNotIn("引用文献列表", report)

    def test_rag_no_hits_raises(self):
        import pipeline
        cfg = _fake_config("rag")
        fake_rf = MagicMock()
        fake_rf.retrieve.return_value = []
        with patch.object(pipeline.knowledge, "plan_queries", return_value=["q1"]), \
             patch.object(pipeline, "RAGFlowClient", return_value=fake_rf):
            with self.assertRaises(RagflowError):
                pipeline.run(cfg, "画东西")


class TestCheckRagflow(unittest.TestCase):
    """check.check_ragflow：鉴权 / dataset 存在性 / 检索通路三级探测。"""

    def _cfg(self):
        cfg = _fake_config("rag")
        cfg.ragflow_dataset_ids = ["ds1"]
        return cfg

    def test_ok(self):
        import check
        fake = MagicMock()
        fake.list_datasets.return_value = [{"id": "ds1", "name": "考古文献库"}]
        fake.retrieve.return_value = [{"content": "片段", "document_name": "a.pdf",
                                       "page": 1, "similarity": 0.5, "keywords": []}]
        with patch.object(check, "RAGFlowClient", return_value=fake):
            ok, detail = check.check_ragflow(self._cfg())
        self.assertTrue(ok)
        self.assertIn("命中 1 片段", detail)
        # 探测检索用 0 阈值，以便区分"通路坏"与"库为空"
        self.assertEqual(fake.retrieve.call_args.kwargs["similarity_threshold"], 0.0)

    def test_dataset_missing(self):
        import check
        fake = MagicMock()
        fake.list_datasets.return_value = [{"id": "other", "name": "别的库"}]
        with patch.object(check, "RAGFlowClient", return_value=fake):
            ok, detail = check.check_ragflow(self._cfg())
        self.assertFalse(ok)
        self.assertIn("dataset 不存在", detail)
        fake.retrieve.assert_not_called()

    def test_empty_library_vs_unreachable(self):
        import check
        fake = MagicMock()
        fake.list_datasets.return_value = [{"id": "ds1", "name": "考古文献库"}]
        fake.retrieve.return_value = []
        with patch.object(check, "RAGFlowClient", return_value=fake):
            ok, detail = check.check_ragflow(self._cfg())
        self.assertFalse(ok)
        self.assertIn("零命中", detail)

    def test_unreachable(self):
        import check
        with patch.object(check, "RAGFlowClient",
                          side_effect=RagflowError("RAGFlow 请求失败: POST /retrieval")):
            ok, detail = check.check_ragflow(self._cfg())
        self.assertFalse(ok)
        self.assertIn("RAGFLOW_BASE_URL", detail)

    def test_run_checks_skips_when_unconfigured(self):
        import check
        cfg = _fake_config("local")
        cfg.ragflow_base_url, cfg.ragflow_api_key, cfg.ragflow_dataset_ids = "", "", []
        with patch.object(check, "check_llm", return_value=(True, "ok")), \
             patch.object(check, "check_vision", return_value=(True, "ok")), \
             patch.object(check, "check_ragflow") as mock_rag:
            ok = check.run_checks(cfg, skip_image=True)
        self.assertTrue(ok)
        mock_rag.assert_not_called()  # 未配置 RAGFlow 时不探测

    def test_run_checks_includes_ragflow_when_configured(self):
        import check
        cfg = self._cfg()
        with patch.object(check, "check_llm", return_value=(True, "ok")), \
             patch.object(check, "check_vision", return_value=(True, "ok")), \
             patch.object(check, "check_ragflow", return_value=(True, "知识层可达")) as mock_rag:
            ok = check.run_checks(cfg, skip_image=True)
        self.assertTrue(ok)
        mock_rag.assert_called_once()

    def test_run_checks_fails_when_ragflow_fails(self):
        import check
        cfg = self._cfg()
        with patch.object(check, "check_llm", return_value=(True, "ok")), \
             patch.object(check, "check_vision", return_value=(True, "ok")), \
             patch.object(check, "check_ragflow", return_value=(False, "不可达")):
            ok = check.run_checks(cfg, skip_image=True)
        self.assertFalse(ok)


class _FakeChatClient:
    """按调用次数决定抛错或返回内容的假客户端。"""

    def __init__(self, fail_times: int = 0, persist_marker: bool = False,
                 message: str = "Upstream rejected illegal short-input distillation or heartbeat probing."):
        self.state = {"n": 0, "msg": message}
        self.fail_times = fail_times
        self.persist_marker = persist_marker

    def __call__(self, *a, **kw):
        return self

    # 模拟 OpenAI(api_key=..., base_url=..., timeout=...)
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.state["n"] += 1
        if self.persist_marker or self.state["n"] <= self.fail_times:
            raise Exception(f"Error code: 400 - {{'error': {{'message': '{self.state['msg']}'}}}}")
        return MagicMock(choices=[MagicMock(message=MagicMock(content='{"ok": true, "summary": "地层学"}'))])


class TestCheckLlmProbe(unittest.TestCase):
    """文本探针：真实任务提示词 + 短输入风控自动加长重试。"""

    def _cfg(self):
        return _fake_config("local")

    def test_ok_first_try(self):
        import check
        fake = _FakeChatClient()
        with patch.object(check.config_mod, "OpenAI", fake):
            ok, detail = check.check_llm(self._cfg())
        self.assertTrue(ok)
        self.assertEqual(fake.state["n"], 1)
        self.assertNotIn("加长", detail)

    def test_retry_on_short_input_guard(self):
        import check
        fake = _FakeChatClient(fail_times=1)  # 首轮被风控拦，加长后通过
        with patch.object(check.config_mod, "OpenAI", fake):
            ok, detail = check.check_llm(self._cfg())
        self.assertTrue(ok)
        self.assertEqual(fake.state["n"], 2)
        self.assertIn("加长", detail)

    def test_guard_persists(self):
        import check
        fake = _FakeChatClient(persist_marker=True)
        with patch.object(check.config_mod, "OpenAI", fake):
            ok, detail = check.check_llm(self._cfg())
        self.assertFalse(ok)
        self.assertIn("风控", detail)
        self.assertEqual(fake.state["n"], 2)  # 只重试一次，不无限重试

    def test_other_error_no_retry(self):
        import check
        fake = _FakeChatClient(fail_times=1, message="invalid api key")
        with patch.object(check.config_mod, "OpenAI", fake):
            ok, detail = check.check_llm(self._cfg())
        self.assertFalse(ok)
        self.assertIn("invalid api key", detail)
        self.assertEqual(fake.state["n"], 1)  # 非风控错误不重试


class TestSearchPolicyAndReport(unittest.TestCase):
    """回归：① 报告里的"组查询"数应来自检索规划（曾被联网搜索词覆盖，写成 3）；
    ② rag 模式不得混入联网资料（会破坏「断言可溯源到文献页码」的引用原则）。"""

    def _run(self, mode: str, tmp: Path, needs_search: bool):
        import pipeline
        import search

        cfg = _fake_config(mode)
        cfg.output_dir = tmp / "out"
        cfg.knowledge_dir = tmp / "kn"
        cfg.search_enabled = True

        chunks = [{"content": "龙身呈匚形", "document_name": "发掘报告.pdf", "page": 3,
                   "similarity": 0.9, "keywords": []}]
        learned = {"knowledge_summary_md": "知识", "needs_search": needs_search,
                   "search_queries": ["补搜1", "补搜2", "补搜3"], "illustration_spec": {},
                   "image_prompt_zh": "中", "image_prompt_en": "en"}

        fake_rf = MagicMock()
        fake_rf.retrieve.return_value = chunks
        web_hits = [{"query": "补搜1", "title": "t", "url": "http://x", "snippet": "s"}]

        with patch.object(pipeline.knowledge, "plan_queries",
                          return_value=["q1", "q2", "q3", "q4", "q5"]), \
             patch.object(pipeline, "RAGFlowClient", return_value=fake_rf), \
             patch.object(pipeline.knowledge, "learn", return_value=learned), \
             patch.object(pipeline.generate, "generate_image",
                          side_effect=lambda _k, _u, _m, _s, _p, path, **kw: Path(path).write_bytes(b"\x89PNG")), \
             patch.object(pipeline.verify, "verify_image",
                          return_value={"pass": True, "score": 9, "problems": [], "suggestions": ""}), \
             patch.object(search, "web_search", return_value=web_hits) as mock_web, \
             patch.object(search, "format_search_results", return_value="(搜索结果)"):
            if mode == "local":
                with patch.object(pipeline, "collect_references",
                                  return_value=[{"name": "a.pdf", "ext": ".pdf", "chars": 10, "text": "t"}]):
                    result = pipeline.run(cfg, "画龙形器")
            else:
                result = pipeline.run(cfg, "画龙形器")
        return result, mock_web

    def test_rag_report_counts_planned_queries(self):
        with tempfile.TemporaryDirectory() as td:
            result, mock_web = self._run("rag", Path(td), needs_search=True)
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("5 组查询", report)          # 修复前会显示 3（被搜索词覆盖）
            self.assertIn("检索词: q1 / q2 / q3 / q4 / q5", report)

    def test_rag_mode_does_not_use_web_search(self):
        with tempfile.TemporaryDirectory() as td:
            _, mock_web = self._run("rag", Path(td), needs_search=True)
            mock_web.assert_not_called()               # rag 模式不联网

    def test_local_mode_still_uses_web_search(self):
        with tempfile.TemporaryDirectory() as td:
            _, mock_web = self._run("local", Path(td), needs_search=True)
            mock_web.assert_called_once()              # local 模式行为不变


class TestConfigModeResolution(unittest.TestCase):
    """注意：这两个用例必须屏蔽项目真实 .env（本机部署 RAGFlow 后 .env 已含
    RAGFLOW_*，否则 load_config 会读到真实配置导致"应当报错"的用例失效）。"""

    def _isolate(self):
        import config as config_mod
        for k in ("RAGFLOW_BASE_URL", "RAGFLOW_API_KEY", "RAGFLOW_DATASET_ID", "PIPELINE_MODE"):
            os.environ.pop(k, None)
        return patch.object(config_mod, "load_dotenv", lambda *a, **kw: None)

    def test_rag_requires_config(self):
        from config import ConfigError, load_config
        with self._isolate():
            with self.assertRaises(ConfigError):
                load_config(mode="rag", require_llm=False)

    def test_local_mode_without_ragflow_ok(self):
        from config import load_config
        with self._isolate(), patch.dict("os.environ", {"PIPELINE_MODE": "local"}, clear=False):
            cfg = load_config(mode=None, require_llm=False)
        self.assertEqual(cfg.mode, "local")

    def test_auto_mode_picks_rag_when_configured(self):
        from config import load_config
        with self._isolate(), patch.dict("os.environ", {
            "RAGFLOW_BASE_URL": "http://ragflow.test",
            "RAGFLOW_API_KEY": "sk-x",
            "RAGFLOW_DATASET_ID": "ds-a;ds-b",
        }, clear=False):
            cfg = load_config(mode=None, require_llm=False)
        self.assertEqual(cfg.mode, "rag")
        self.assertEqual(cfg.ragflow_dataset_ids, ["ds-a", "ds-b"])


if __name__ == "__main__":
    unittest.main()
