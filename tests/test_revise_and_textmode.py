# -*- coding: utf-8 -*-
"""T1 指定重绘 / T2 图内文字 / T4 代理 / T5 客户端工厂 / T6 检索确诊 的 mock 单测。

全部 mock，不访问网络、不调用真实模型、不产生任何费用。
运行：在项目根目录执行
  .venv/Scripts/python.exe -m unittest discover -s tests -t .
"""
from __future__ import annotations

import base64
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import config as config_mod
import generate
import knowledge
import verify
from config import Config, ConfigError, load_config
from errors import RagflowError, ReviseError
from ragflow_client import diagnose_retrieval_cause

_IMG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _fake_config(mode: str = "rag", tmp: Path | None = None) -> Config:
    tmp = tmp or Path(tempfile.mkdtemp())
    return Config(
        llm_base_url="http://llm.test/v1", llm_api_key="sk-llm", llm_model="test-llm",
        img_base_url="http://img.test/v1", img_api_key="sk-img", img_model="test-img",
        img_size="1024x1024",
        vision_base_url="http://llm.test/v1", vision_api_key="sk-llm", vision_model="test-llm",
        text_mode="caption_only",
        llm_timeout=111, knowledge_timeout=222, vision_timeout=333, image_timeout=444, llm_retries=3,
        max_attempts=1, search_enabled=False, search_top_k=5, search_proxy=None,
        per_file_char_limit=30000,
        refs_dir=tmp / "references", output_dir=tmp / "output",
        knowledge_dir=tmp / "knowledge", log_dir=tmp / "logs",
        mode=mode,
        ragflow_base_url="http://ragflow.test", ragflow_api_key="sk-rf",
        ragflow_dataset_ids=["ds1"], retrieval_top_k=5,
        retrieval_sim_threshold=0.2, retrieval_page_size=5, ragflow_timeout=10,
    )


class _FakeOpenAI:
    """既可当 chat 客户端、也可当 images 客户端的假 OpenAI（记录构造参数）。"""

    def __init__(self, content: str = '{"ok": true}', edit_raises: bool = True):
        self.content = content
        self.edit_raises = edit_raises
        self.init_kwargs: dict = {}
        self.chat_kwargs: dict = {}
        self.image_calls: list[str] = []

    def __call__(self, **kwargs):
        self.init_kwargs = kwargs
        return self

    # chat.completions.create
    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.chat_kwargs = kwargs
        return MagicMock(choices=[MagicMock(message=MagicMock(content=self.content))])

    # images.generate / images.edit
    @property
    def images(self):
        return self

    def _item(self):
        return MagicMock(b64_json=base64.b64encode(_IMG_BYTES).decode(), url=None)

    def generate(self, **kwargs):
        self.image_calls.append("generate")
        return MagicMock(data=[self._item()])

    def edit(self, **kwargs):
        self.image_calls.append("edit")
        if self.edit_raises:
            raise Exception("404 model does not support image editing")
        return MagicMock(data=[self._item()])


def _make_run(tmp: Path, name: str = "run_20260101_000000", *, requirement: str = "绘制绿松石龙形器",
              images: int = 2, final: bool = False, report: bool = True,
              knowledge_dir: Path | None = None, rev_record: dict | None = None) -> Path:
    """造一个假的基准 run 目录。"""
    run = tmp / "output" / name
    run.mkdir(parents=True, exist_ok=True)
    if report:
        (run / "report.md").write_text(
            "# 考古插图生成报告\n"
            f"- 知识层模式: rag\n- 绘图需求: {requirement}\n"
            "- 最终结论: ⚠️ 达到最大重试次数，仍有未解决问题\n",
            encoding="utf-8")
    for i in range(1, images + 1):
        (run / f"illustration_attempt_{i}.png").write_bytes(_IMG_BYTES)
    if final:
        (run / "final_illustration.png").write_bytes(_IMG_BYTES)
    if knowledge_dir is not None:
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        summary = "## 知识\n- 龙身呈匚形 [报告.pdf p.3]"
        (knowledge_dir / "knowledge_summary.md").write_text(summary, encoding="utf-8")
        # pipeline 每次运行都会在 run 目录留快照，--revise 优先读它
        (run / "knowledge_summary.md").write_text(summary, encoding="utf-8")
    if rev_record is not None:
        (run / "revision.json").write_text(json.dumps(rev_record, ensure_ascii=False), encoding="utf-8")
    return run


# ================= T4 代理健壮性 =================

class TestProxySanitize(unittest.TestCase):
    def test_removes_bracketed_ipv6_loopback(self):
        # 注意：Windows 上 NO_PROXY / no_proxy 是**同一个**环境变量（大小写不敏感），
        # 所以只按一个名字断言，否则两个键会互相覆盖。
        with patch.dict(os.environ, {
            "NO_PROXY": "localhost,127.0.0.1,[::1],[::1]:8080",
            "HTTP_PROXY": "http://proxy.test:7890",
            "HTTPS_PROXY": "http://proxy.test:7890",
        }, clear=False):
            config_mod._sanitize_proxy_env()
            self.assertNotIn("[::1]", os.environ["NO_PROXY"])
            self.assertIn("127.0.0.1", os.environ["NO_PROXY"])
            self.assertIn("localhost", os.environ["NO_PROXY"])
            # 代理本身必须原样保留（访问上游模型要走代理）
            self.assertEqual(os.environ["HTTP_PROXY"], "http://proxy.test:7890")
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://proxy.test:7890")

    def test_keeps_legal_unbracketed_loopback(self):
        with patch.dict(os.environ, {"NO_PROXY": "::1,example.com"}, clear=False):
            config_mod._sanitize_proxy_env()
            self.assertIn("::1", os.environ["NO_PROXY"])
            self.assertIn("example.com", os.environ["NO_PROXY"])

    def test_module_import_triggers_sanitize(self):
        """模块导入即生效——早于任何 httpx 客户端的构造（否则会构造即崩）。"""
        with patch.dict(os.environ, {"NO_PROXY": "a,[::1],b"}, clear=False):
            importlib.reload(config_mod)
            self.assertEqual(os.environ["NO_PROXY"], "a,b")


# ================= T5 客户端工厂 =================

class TestClientFactory(unittest.TestCase):
    def test_factory_forwards_timeout_and_retries(self):
        fake = _FakeOpenAI()
        with patch.object(config_mod, "OpenAI", fake):
            config_mod.make_openai_client("k", "u", timeout=42, max_retries=5)
        self.assertEqual(fake.init_kwargs, {"api_key": "k", "base_url": "u", "timeout": 42, "max_retries": 5})

    def test_call_points_take_values_from_config(self):
        cfg = _fake_config()
        fake = _FakeOpenAI()
        with patch.object(config_mod, "OpenAI", fake):
            generate.generate_image("k", "u", "m", "1024x1024", "prompt", Path(tempfile.mkdtemp()) / "a.png",
                                    timeout=cfg.image_timeout, max_retries=cfg.llm_retries)
        self.assertEqual(fake.init_kwargs["timeout"], cfg.image_timeout)
        self.assertEqual(fake.init_kwargs["max_retries"], cfg.llm_retries)

    def test_knowledge_client_uses_given_timeout(self):
        captured = {}

        def fake_factory(api_key, base_url, **kw):
            captured.update(kw)
            m = MagicMock()
            m.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"queries": ["a"]}'))])
            return m

        with patch.object(knowledge, "_client", fake_factory):
            knowledge.plan_queries("k", "u", "m", "需求", timeout=222, max_retries=3)
        self.assertEqual(captured, {"timeout": 222, "max_retries": 3})

    def test_env_identity_reports_versions(self):
        import check
        text = "\n".join(check.env_identity())
        self.assertIn("arch-illustration", text)
        self.assertIn("openai=", text)
        self.assertIn("Python:", text)


# ================= T2 图内文字 =================

class TestTextModeConfig(unittest.TestCase):
    def _isolate(self):
        for k in ("TEXT_MODE", "RAGFLOW_BASE_URL", "RAGFLOW_API_KEY", "RAGFLOW_DATASET_ID"):
            os.environ.pop(k, None)
        return patch.object(config_mod, "load_dotenv", lambda *a, **kw: None)

    # 注意：TestProxySanitize 里的 importlib.reload 会重建 config 的类对象，
    # 所以这里一律通过 config_mod 动态取，不用 import 时绑定的旧引用。
    def test_default_is_caption_only(self):
        with self._isolate():
            cfg = config_mod.load_config(mode="local", require_llm=False)
        self.assertEqual(cfg.text_mode, "caption_only")

    def test_invalid_mode_rejected(self):
        with self._isolate(), patch.dict(os.environ, {"TEXT_MODE": "klingon"}, clear=False):
            with self.assertRaises(config_mod.ConfigError):
                config_mod.load_config(mode="local", require_llm=False)

    def test_timeouts_have_defaults(self):
        with self._isolate():
            cfg = config_mod.load_config(mode="local", require_llm=False)
        self.assertEqual((cfg.llm_timeout, cfg.knowledge_timeout, cfg.vision_timeout,
                          cfg.image_timeout, cfg.llm_retries), (300, 600, 300, 300, 2))


class TestTextModePrompt(unittest.TestCase):
    def test_caption_only_clause_forbids_text(self):
        zh, en = knowledge.apply_text_mode("原始中文提示", "original english", "caption_only")
        self.assertTrue(zh.startswith("原始中文提示"))
        self.assertTrue(en.startswith("original english"))
        for s in (zh, en):
            self.assertIn("图内不得出现任何文字", s)
            self.assertIn("NO text", s)

    def test_in_image_clause_demands_chinese(self):
        _, en = knowledge.apply_text_mode("a", "b", "in_image")
        self.assertIn("简体中文", en)
        self.assertIn("Simplified Chinese", en)

    def test_learning_prompt_carries_text_mode_block(self):
        captured = {}

        def fake_chat(client, model, system, user, temperature=0.3):
            captured["user"] = user
            return json.dumps({"knowledge_summary_md": "k", "illustration_spec": {},
                               "image_prompt_zh": "中", "image_prompt_en": "en"}, ensure_ascii=False)

        with patch.object(knowledge, "_chat", fake_chat), \
             patch.object(knowledge, "_client", lambda *a, **kw: MagicMock()):
            knowledge.learn("k", "u", "m", "需求", refs=[],
                            chunks=[{"content": "c", "document_name": "d.pdf", "page": 1,
                                     "similarity": 0.5, "keywords": []}],
                            text_mode="in_image")
        self.assertIn("in_image", captured["user"])
        self.assertIn("简体中文", captured["user"])


class TestCaptionTable(unittest.TestCase):
    def test_object_array(self):
        md = knowledge.caption_table_md([
            {"index": 1, "name": "吻部", "desc": "图面右上引线所指"},
            {"index": 2, "name": "菱形主纹", "desc": "龙身中段"},
        ])
        self.assertIn("| 编号 | 名称 | 图上位置 |", md)
        self.assertIn("| 1 | 吻部 | 图面右上引线所指 |", md)
        self.assertIn("| 2 | 菱形主纹 | 龙身中段 |", md)

    def test_plain_strings_and_mapping(self):
        self.assertIn("| 1 | 吻部 | — |", knowledge.caption_table_md(["吻部"]))
        md = knowledge.caption_table_md({"1": "吻部", "2": "尾尖"})
        self.assertIn("| 1 | 吻部 | — |", md)
        self.assertIn("| 2 | 尾尖 | — |", md)

    def test_pipe_is_escaped(self):
        md = knowledge.caption_table_md([{"name": "吻部|尾尖"}])
        self.assertNotIn("吻部|尾尖", md)
        self.assertIn("吻部／尾尖", md)

    def test_empty_placeholder(self):
        self.assertIn("图注表为空", knowledge.caption_table_md(None))


class TestTextCheckVerification(unittest.TestCase):
    def _verdict(self, payload: dict) -> dict:
        fake = _FakeOpenAI(content=json.dumps(payload, ensure_ascii=False))
        with patch.object(config_mod, "OpenAI", fake), tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.png"
            img.write_bytes(_IMG_BYTES)
            verdict = verify.verify_image("k", "u", "m", str(img), "需求", "知识",
                                          text_mode="caption_only")
        return verdict, fake

    def test_caption_only_rejects_image_with_text(self):
        verdict, fake = self._verdict({
            "pass": True, "score": 88, "problems": [], "suggestions": "",
            "text_check": {"pass": False, "found": ["KISS", "Ratio 1:5"],
                           "evidence": "图左侧与下方比例尺旁"},
        })
        self.assertFalse(verdict["pass"])
        self.assertTrue(any("图内文字不符合 caption_only" in p for p in verdict["problems"]))
        system = fake.chat_kwargs["messages"][0]["content"]
        self.assertIn("本次策略 caption_only", system)
        self.assertIn("只要图中出现任何可辨认的文字、字母、数字或仿汉字符号 → pass=false", system)

    def test_in_image_rejects_english_labels(self):
        fake = _FakeOpenAI(content=json.dumps({
            "pass": True, "score": 80, "problems": [], "suggestions": "",
            "text_check": {"pass": False, "found": ["KISS"], "evidence": "右上引线"},
        }, ensure_ascii=False))
        with patch.object(config_mod, "OpenAI", fake), tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.png"
            img.write_bytes(_IMG_BYTES)
            verdict = verify.verify_image("k", "u", "m", str(img), "需求", "知识", text_mode="in_image")
        self.assertFalse(verdict["pass"])
        self.assertIn("简体中文", fake.chat_kwargs["messages"][0]["content"])

    def test_missing_text_check_is_not_silently_passed(self):
        verdict, _ = self._verdict({"pass": True, "score": 90, "problems": [], "suggestions": ""})
        self.assertFalse(verdict["pass"])          # 未获确认 → 不允许静默通过
        self.assertTrue(any("校验降级" in p for p in verdict["problems"]))

    def test_no_text_mode_keeps_old_behaviour(self):
        fake = _FakeOpenAI(content=json.dumps({"pass": True, "score": 90, "problems": []}))
        with patch.object(config_mod, "OpenAI", fake), tempfile.TemporaryDirectory() as td:
            img = Path(td) / "a.png"
            img.write_bytes(_IMG_BYTES)
            verdict = verify.verify_image("k", "u", "m", str(img), "需求", "知识")
        self.assertTrue(verdict["pass"])
        self.assertNotIn("text_check", fake.chat_kwargs["messages"][0]["content"])


# ================= T1 指定重绘 =================

class TestRunParsing(unittest.TestCase):
    def test_split_rev_name(self):
        import revise
        self.assertEqual(revise.split_rev_name("run_1_rev2"), ("run_1", 2))
        self.assertEqual(revise.split_rev_name("run_1"), ("run_1", 0))

    def test_next_rev_dir_increments(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "output"
            (out / "run_x_rev1").mkdir(parents=True)
            (out / "run_x_rev2").mkdir()
            (out / "other_run").mkdir()
            path, n = revise.next_rev_dir(out, "run_x")
        self.assertEqual(n, 3)
        self.assertEqual(path.name, "run_x_rev3")

    def test_load_context_ok_and_picks_last_image(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp, images=3, knowledge_dir=tmp / "knowledge")
            ctx = revise.load_run_context(run, tmp / "knowledge")
        self.assertEqual(ctx.requirement, "绘制绿松石龙形器")
        self.assertEqual(ctx.mode, "rag")
        self.assertEqual(ctx.base_image.name, "illustration_attempt_3.png")
        self.assertIn("run 目录快照", ctx.knowledge_source)

    def test_refine_from_final_missing_errors_with_paths(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp, images=2, knowledge_dir=tmp / "knowledge")
            with self.assertRaises(ReviseError) as cm:
                revise.load_run_context(run, tmp / "knowledge", refine_from="final")
        self.assertIn("illustration_attempt_1.png", str(cm.exception.message))

    def test_missing_images_reports_available_paths(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp, images=0, knowledge_dir=tmp / "knowledge")
            with self.assertRaises(ReviseError) as cm:
                revise.load_run_context(run, tmp / "knowledge")
        self.assertIn("report.md", str(cm.exception.message))

    def test_missing_report_uses_requirement_fallback(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp, report=False, knowledge_dir=tmp / "knowledge")
            ctx = revise.load_run_context(run, tmp / "knowledge", requirement_fallback="命令行给的需求")
            self.assertEqual(ctx.requirement, "命令行给的需求")

    def test_missing_knowledge_allows_but_notes(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp)   # 不提供 knowledge 目录
            ctx = revise.load_run_context(run, tmp / "knowledge")
        self.assertEqual(ctx.knowledge_md, "")
        self.assertIn("无", ctx.knowledge_source)

    def test_global_knowledge_fallback(self):
        import revise
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = _make_run(tmp)                                  # run 内无快照
            kd = tmp / "knowledge"
            kd.mkdir()
            (kd / "knowledge_summary.md").write_text("## 知识\n- 旧摘要", encoding="utf-8")
            ctx = revise.load_run_context(run, kd)
        self.assertIn("全局", ctx.knowledge_source)
        self.assertIn("旧摘要", ctx.knowledge_md)


class TestFeedbackStructuring(unittest.TestCase):
    def _structure(self, content: str):
        import revise
        cfg = _fake_config()
        fake = _FakeOpenAI(content=content)
        with patch.object(config_mod, "OpenAI", fake):
            return revise.structure_feedback(cfg, "需求", "知识", "图内标注改成中文；比例尺移到右下角")

    def test_normal(self):
        out = self._structure(json.dumps({
            "must_change": ["图内标注改成中文", "比例尺移到右下角"],
            "must_keep": ["构图与视角"],
            "open": ["字体"],
        }, ensure_ascii=False))
        self.assertFalse(out["degraded"])
        self.assertEqual(len(out["must_change"]), 2)
        self.assertEqual(out["must_keep"], ["构图与视角"])

    def test_missing_must_change_degrades(self):
        out = self._structure(json.dumps({"must_keep": ["构图"]}, ensure_ascii=False))
        self.assertTrue(out["degraded"])
        self.assertEqual(out["must_change"], ["图内标注改成中文；比例尺移到右下角"])

    def test_non_json_degrades(self):
        out = self._structure("抱歉，我无法完成这个请求。")
        self.assertTrue(out["degraded"])
        self.assertTrue(out["degraded_reason"])


class _RevisionHarness:
    """把 revise_run 的算力通道全部打桩。"""

    def __init__(self, tmp: Path, *, verdicts: list[dict], structured: dict | None = None,
                 edit_raises: bool = True):
        self.tmp = tmp
        self.verdicts = verdicts
        self.structured = structured or {
            "must_change": ["图内标注改成中文"], "must_keep": ["构图与视角"],
            "open": [], "degraded": False, "degraded_reason": ""}
        self.edit_raises = edit_raises
        self.prompts: list[str] = []
        self.verify_calls: list[dict] = []
        self.fake_openai = _FakeOpenAI(edit_raises=edit_raises)

    def __enter__(self):
        import revise
        self.revise = revise

        def fake_gen(_k, _u, _m, _s, prompt, path, **kw):
            self.prompts.append(prompt)
            Path(path).write_bytes(_IMG_BYTES)
            return generate.ImageResult(
                path=Path(path),
                used_reference=kw.get("input_image") is not None and not self.edit_raises,
                note="" if not self.edit_raises else "上游图片编辑接口不可用，本次已退化为纯提示词重绘（未带参考图）",
            )

        def fake_verify(_k, _u, _m, image, requirement, kmd, **kw):
            """只替换"模型返回的原始 JSON"，后处理（逐条判定/综合 pass）走真实代码。"""
            self.verify_calls.append({"image": image, "kw": kw, "knowledge": kmd})
            raw = self.verdicts[min(len(self.verify_calls) - 1, len(self.verdicts) - 1)]
            verdict = verify._apply_text_check(dict(raw), kw.get("text_mode"))
            return verify._apply_revision_checks(
                verdict, kw.get("must_change", []), kw.get("must_keep", []), kw.get("text_mode"))

        self._patches = [
            patch.object(revise, "structure_feedback", lambda *a, **kw: dict(self.structured)),
            patch.object(revise.generate, "generate_image", fake_gen),
            patch.object(revise.verify, "verify_revision_image", fake_verify),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()
        return False


def _ok_verdict(change=("图内标注改成中文",), keep=("构图与视角",)):
    return {
        "pass": True, "score": 90, "problems": [], "suggestions": "",
        "must_change_results": [{"item": c, "status": "满足", "evidence": "图面右上"} for c in change],
        "must_keep_results": [{"item": k, "status": "未漂移", "evidence": "与上一版一致"} for k in keep],
        "text_check": {"pass": True, "found": [], "evidence": "无文字"}, "verdict_degraded": False,
    }


class TestRevisionFlow(unittest.TestCase):
    def test_happy_path_writes_rev_dir_and_report(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            with _RevisionHarness(tmp, verdicts=[_ok_verdict()]) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000",
                                             "图内标注改成中文；底部比例尺移到右下角")
            self.assertTrue(result["ok"])
            self.assertEqual(result["out_dir"].name, "run_20260101_000000_rev1")
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("人工反馈驱动", report)
            self.assertIn("图内标注改成中文；底部比例尺移到右下角", report)
            self.assertIn("must_change（本轮必须改动）", report)
            self.assertIn("must_keep（本轮必须保持）", report)
            self.assertIn("must_change 落实情况", report)
            self.assertIn("| 1 | 图内标注改成中文 | ✅ 满足 |", report)
            self.assertIn("图注表（图版说明）", report)
            self.assertTrue((result["out_dir"] / "final_illustration.png").exists())
            self.assertTrue((result["out_dir"] / "revision.json").exists())
            # 提示词里必须带上 must_keep 与"其余不许动"的硬约束（T1.3）
            prompt = h.prompts[0]
            self.assertIn("构图与视角", prompt)
            self.assertIn("其余内容必须与上一版保持一致", prompt)
            # 校验也必须真的检查 must_keep（只写不查等于没写）
            self.assertEqual(h.verify_calls[0]["kw"]["must_keep"], ["构图与视角"])
            self.assertEqual(h.verify_calls[0]["kw"]["previous_image"].name, "illustration_attempt_1.png")

    def test_unmet_change_item_joins_problem_list_and_retries(self):
        bad = {
            "pass": True, "score": 70, "problems": [], "suggestions": "重画比例尺",
            "must_change_results": [{"item": "图内标注改成中文", "status": "未满足",
                                     "evidence": "标注仍为英文"}],
            "must_keep_results": [{"item": "构图与视角", "status": "未漂移", "evidence": "一致"}],
            "text_check": {"pass": True, "found": [], "evidence": "—"}, "verdict_degraded": False,
        }
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            cfg.max_attempts = 2
            with _RevisionHarness(tmp, verdicts=[bad, _ok_verdict()]) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "改中文")
            self.assertTrue(result["ok"])
            self.assertEqual(len(h.prompts), 2)
            self.assertIn("未落实", h.prompts[1])          # 未落实项进入下一轮提示词
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("反馈项未落实（未满足）", report)

    def test_drift_forces_fail_even_if_model_says_pass(self):
        drift = {
            "pass": True, "score": 85, "problems": [], "suggestions": "",
            "must_change_results": [{"item": "图内标注改成中文", "status": "满足", "evidence": "—"}],
            "must_keep_results": [{"item": "构图与视角", "status": "漂移",
                                   "evidence": "视角由俯视45度变成正投影"}],
            "text_check": {"pass": True, "found": [], "evidence": "—"}, "verdict_degraded": False,
        }
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            with _RevisionHarness(tmp, verdicts=[drift]) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "改中文")
            self.assertFalse(result["ok"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("未指定方面疑似漂移（漂移）", report)

    def test_missing_item_judgement_is_not_silently_passed(self):
        incomplete = _ok_verdict()
        incomplete["must_change_results"] = []      # 模型漏判
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            with _RevisionHarness(tmp, verdicts=[incomplete]) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "改中文")
            self.assertFalse(result["ok"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("未判定", report)

    def test_chain_inherits_must_keep(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, name="run_x", images=1, knowledge_dir=tmp / "knowledge")
            rev1 = _make_run(tmp, name="run_x_rev1", images=1, knowledge_dir=tmp / "knowledge",
                             rev_record={"must_keep_effective": ["构图与视角", "器物形制"],
                                         "feedback": "第一轮反馈"})
            cfg = _fake_config(tmp=tmp)
            h = _RevisionHarness(tmp, verdicts=[_ok_verdict(keep=("构图与视角", "器物形制"))])
            with h:
                result = h.revise.revise_run(cfg, rev1, "再改一处")
            self.assertEqual(result["out_dir"].name, "run_x_rev2")
            self.assertEqual(result["must_keep"], ["构图与视角", "器物形制"])
            self.assertEqual(h.verify_calls[0]["kw"]["must_keep"], ["构图与视角", "器物形制"])
            record = json.loads((result["out_dir"] / "revision.json").read_text(encoding="utf-8"))
            self.assertEqual(record["must_keep_inherited"], ["构图与视角", "器物形制"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("继承的 must_keep", report)

    def test_cli_must_change_overrides_and_conflict_errors(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            with _RevisionHarness(tmp, verdicts=[_ok_verdict(change=("把比例尺挪走",), keep=("构图与视角",))]) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "随便改点",
                                             must_change_arg="把比例尺挪走",
                                             must_keep_arg="构图与视角")
            self.assertEqual(result["must_change"], ["把比例尺挪走"])
            with _RevisionHarness(tmp, verdicts=[_ok_verdict()]) as h2:
                with self.assertRaises(ReviseError):
                    h2.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "改点东西",
                                         must_change_arg="构图,器物形制",
                                         must_keep_arg="构图,器物形制")

    def test_parse_list_arg_supports_chinese_punctuation(self):
        import revise
        self.assertEqual(revise.parse_list_arg("甲，乙；丙,丁"), ["甲", "乙", "丙", "丁"])
        self.assertEqual(revise.parse_list_arg(None), [])

    def test_image_to_image_degrade_is_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1, knowledge_dir=tmp / "knowledge")
            cfg = _fake_config(tmp=tmp)
            with _RevisionHarness(tmp, verdicts=[_ok_verdict()], edit_raises=True) as h:
                result = h.revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "改中文")
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("降级说明", report)
            self.assertIn("退化为纯提示词重绘", report)

    def test_feedback_required(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_run(tmp, images=1)
            cfg = _fake_config(tmp=tmp)
            import revise
            with self.assertRaises(ReviseError):
                revise.revise_run(cfg, tmp / "output" / "run_20260101_000000", "   ")


class TestRealImageToImage(unittest.TestCase):
    """generate_image 的图生图/降级两条路径（打桩 OpenAI，不产生费用）。"""

    def test_edit_success_marks_reference_used(self):
        fake = _FakeOpenAI(edit_raises=False)
        with tempfile.TemporaryDirectory() as td, patch.object(config_mod, "OpenAI", fake):
            ref = Path(td) / "ref.png"
            ref.write_bytes(_IMG_BYTES)
            out = Path(td) / "out.png"
            res = generate.generate_image("k", "u", "m", "1024x1024", "prompt", out, input_image=ref)
        self.assertTrue(res.used_reference)
        self.assertEqual(fake.image_calls, ["edit"])
        self.assertEqual(res.note, "")

    def test_edit_unsupported_falls_back_and_notes(self):
        fake = _FakeOpenAI(edit_raises=True)
        with tempfile.TemporaryDirectory() as td, patch.object(config_mod, "OpenAI", fake):
            ref = Path(td) / "ref.png"
            ref.write_bytes(_IMG_BYTES)
            out = Path(td) / "out.png"
            res = generate.generate_image("k", "u", "m", "1024x1024", "prompt", out, input_image=ref)
        self.assertFalse(res.used_reference)
        self.assertEqual(fake.image_calls, ["edit", "generate"])
        self.assertIn("退化为纯提示词重绘", res.note)


class TestRevisionVerification(unittest.TestCase):
    def _verify(self, payload: dict, must_change=None, must_keep=None):
        fake = _FakeOpenAI(content=json.dumps(payload, ensure_ascii=False))
        with patch.object(config_mod, "OpenAI", fake), tempfile.TemporaryDirectory() as td:
            img = Path(td) / "new.png"
            img.write_bytes(_IMG_BYTES)
            prev = Path(td) / "old.png"
            prev.write_bytes(_IMG_BYTES)
            verdict = verify.verify_revision_image(
                "k", "u", "m", str(img), "需求", "知识",
                must_change=must_change or ["改成中文"],
                must_keep=must_keep or ["构图"],
                previous_image=prev)
        return verdict, fake

    def test_all_satisfied_passes(self):
        verdict, fake = self._verify(_ok_verdict(change=("改成中文",), keep=("构图",)))
        self.assertTrue(verdict["pass"])
        self.assertFalse(verdict["verdict_degraded"])
        user = fake.chat_kwargs["messages"][1]["content"]
        self.assertEqual(len([p for p in user if p["type"] == "image_url"]), 2)  # 上一版 + 新版

    def test_partial_change_counts_as_not_passed(self):
        verdict, _ = self._verify({
            "pass": True, "score": 80, "problems": [],
            "must_change_results": [{"item": "改成中文", "status": "部分满足", "evidence": "仅一处改了"}],
            "must_keep_results": [{"item": "构图", "status": "未漂移", "evidence": "一致"}],
        })
        self.assertFalse(verdict["pass"])
        self.assertTrue(any("部分满足" in p for p in verdict["problems"]))

    def test_missing_array_marks_undetermined(self):
        verdict, _ = self._verify({"pass": True, "score": 80, "problems": []})
        self.assertTrue(verdict["verdict_degraded"])
        self.assertFalse(verdict["pass"])
        self.assertEqual(verdict["must_change_results"][0]["status"], "未判定")


# ================= T6 检索诊断 =================

class TestDiagnoseRetrieval(unittest.TestCase):
    def test_unreachable(self):
        client = MagicMock()
        client.list_datasets.side_effect = RagflowError("RAGFlow 请求失败: POST /retrieval")
        msg = diagnose_retrieval_cause(client, ["ds1"])
        self.assertIn("不可达", msg)

    def test_dataset_missing(self):
        client = MagicMock()
        client.list_datasets.return_value = [{"id": "other"}]
        msg = diagnose_retrieval_cause(client, ["ds1"])
        self.assertIn("dataset 不存在", msg)
        client.retrieve.assert_not_called()

    def test_threshold_too_high(self):
        client = MagicMock()
        client.list_datasets.return_value = [{"id": "ds1"}]
        client.retrieve.return_value = [{"content": "x", "similarity": 0.9}]
        msg = diagnose_retrieval_cause(client, ["ds1"])
        self.assertIn("阈值", msg)
        self.assertEqual(client.retrieve.call_args.kwargs["similarity_threshold"], 0.0)

    def test_library_empty(self):
        client = MagicMock()
        client.list_datasets.return_value = [{"id": "ds1"}]
        client.retrieve.return_value = []
        msg = diagnose_retrieval_cause(client, ["ds1"])
        self.assertIn("尚未完成解析入库", msg)


class TestPipelineRetrievalObservability(unittest.TestCase):
    def _cfg(self, tmp: Path):
        cfg = _fake_config(tmp=tmp)
        cfg.output_dir = tmp / "out"
        cfg.knowledge_dir = tmp / "kn"
        return cfg

    def test_zero_hits_raises_with_cause_and_logs_each_query(self):
        import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = self._cfg(tmp)
            fake = MagicMock()
            fake.retrieve.return_value = []
            with patch.object(pipeline.knowledge, "plan_queries", return_value=["q1", "q2"]), \
                 patch.object(pipeline, "RAGFlowClient", return_value=fake), \
                 patch.object(pipeline, "diagnose_retrieval_cause", return_value="库空或阈值过高"):
                with self.assertRaises(RagflowError) as cm:
                    pipeline.run(cfg, "画东西")
        self.assertIn("库空或阈值过高", str(cm.exception.message))

    def test_partial_failure_continues_and_reports_detail(self):
        import pipeline
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = self._cfg(tmp)
            hits = [{"content": "龙身呈匚形", "document_name": "a.pdf", "page": 3,
                     "similarity": 0.8, "keywords": []}]

            def retrieve(q, *a, **kw):
                if q == "q2":
                    raise RagflowError("RAGFlow 返回 HTTP 502")
                return hits

            fake = MagicMock()
            fake.retrieve.side_effect = retrieve
            learned = {"knowledge_summary_md": "知识", "needs_search": False, "search_queries": [],
                       "illustration_spec": {"annotations": [{"index": 1, "name": "吻部"}]},
                       "image_prompt_zh": "中", "image_prompt_en": "en"}
            with patch.object(pipeline.knowledge, "plan_queries", return_value=["q1", "q2"]), \
                 patch.object(pipeline, "RAGFlowClient", return_value=fake), \
                 patch.object(pipeline.knowledge, "learn", return_value=learned), \
                 patch.object(pipeline.generate, "generate_image",
                              side_effect=lambda _k, _u, _m, _s, _p, path, **kw: (
                                  Path(path).write_bytes(_IMG_BYTES), generate.ImageResult(Path(path)))[1]) as mock_gen, \
                 patch.object(pipeline.verify, "verify_image",
                              return_value={"pass": True, "score": 90, "problems": [], "suggestions": ""}):
                result = pipeline.run(cfg, "画绿松石龙形器")
            self.assertTrue(result["ok"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            self.assertIn("## 检索明细", report)
            self.assertIn("HTTP 502", report)          # 失败原因必须写进报告，不能静默吞掉
            self.assertIn("图注表（图版说明）", report)
            self.assertIn("| 1 | 吻部 |", report)
            self.assertIn("caption_only", report)
            # 图内文字硬约束必须进了绘图提示词
            prompt = mock_gen.call_args.args[4]
            self.assertIn("图内不得出现任何文字", prompt)


if __name__ == "__main__":
    unittest.main()
