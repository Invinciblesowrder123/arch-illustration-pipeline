# -*- coding: utf-8 -*-
"""对已有 run 的图重跑一次审稿校验（不重新绘图）。

用法: python reverify.py <run_dir> [--text-mode in_image]
读 run 目录里的 knowledge_summary.md 与 report.md 开头记录的需求原文。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import load_config
from verify import verify_image


def get_requirement(run_dir: Path) -> str:
    # 需求原文记录在 run 目录的 spec 里
    spec = run_dir / "illustration_spec.json"
    if spec.exists():
        data = json.loads(spec.read_text(encoding="utf-8"))
        for key in ("requirement", "user_requirement", "raw_requirement"):
            if key in data and data[key]:
                return data[key]
    raise SystemExit(f"spec 中无需求字段，请手工指定。keys={list(json.loads(spec.read_text(encoding='utf-8')).keys())}")


def main() -> int:
    run_dir = Path(sys.argv[1])
    text_mode = "in_image" if "--text-mode" in sys.argv else None
    cfg = load_config()
    # run 目录内的知识快照优先
    knowledge = (run_dir / "knowledge_summary.md").read_text(encoding="utf-8")
    image = run_dir / "illustration_attempt_1.png"
    # 需求原文：argv[2] 给文件路径，或从 logs/pipeline_*.log 的"绘图需求"行回捞
    if len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
        requirement = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
    else:
        requirement = get_requirement(run_dir)
    print(f"重审: {image.name}（text_mode={text_mode}）")
    verdict = verify_image(
        cfg.vision_api_key, cfg.vision_base_url, cfg.vision_model,
        str(image), requirement, knowledge,
        text_mode=text_mode, timeout=cfg.vision_timeout, max_retries=cfg.llm_retries,
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
