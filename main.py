# -*- coding: utf-8 -*-
"""考古学论文插图生成流水线 — 命令行入口。

用法:
  python main.py --requirement "绘制汉代铜奔马的科学复原图"
  python main.py                          # 交互式输入需求（或读取 requirement.txt）
  python main.py --dry-run                # 只做文献摄取检查，不调用任何模型
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import ConfigError, load_config
from errors import AppError
from ingest import collect_references

PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="考古学论文插图生成流水线")
    p.add_argument("--requirement", "-r", help="绘图需求描述（不给则读 requirement.txt 或交互输入）")
    p.add_argument("--refs", type=Path, help="参考文献目录（默认 ./references）")
    p.add_argument("--out", type=Path, help="输出目录（默认 ./output）")
    p.add_argument("--max-attempts", type=int, help="绘图+校验最大轮数（默认 3）")
    p.add_argument("--no-search", action="store_true", help="禁用联网补充搜索")
    p.add_argument("--mode", choices=["rag", "local"], default=None,
                   help="知识层模式：rag=从 RAGFlow 文献库检索（需在 .env 配置 RAGFlow）；"
                        "local=直读 references/ 目录。留空则自动：RAGFlow 配置齐全用 rag，否则 local")
    p.add_argument("--dry-run", action="store_true", help="只检查文献能否读取，不调用模型、不产生费用")
    p.add_argument("--check", action="store_true", help="模型连通性自检（文本/读图/绘图各实测一次）")
    p.add_argument("--skip-image", action="store_true", help="配合 --check：跳过绘图测试，不产生绘图费用")
    p.add_argument("--scan-policy", choices=["auto", "mineru", "visual", "skip"], default="auto",
                   help="扫描版 PDF（无文字层）处理策略：auto=交互询问；mineru=MinerU本地解析；"
                        "visual=渲染成图片交给多模态模型直读；skip=跳过（默认 auto）")
    return p.parse_args()


def get_requirement(args: argparse.Namespace) -> str:
    if args.requirement and args.requirement.strip():
        return args.requirement.strip()
    req_file = PROJECT_ROOT / "requirement.txt"
    if req_file.exists():
        text = req_file.read_text(encoding="utf-8-sig").strip()
        if text:
            print(f"已从 requirement.txt 读取需求: {text[:80]}{'…' if len(text) > 80 else ''}")
            return text
    print("=" * 60)
    print("欢迎使用考古学论文插图生成助手")
    print("请描述您想绘制的学术论文插图（越具体越好：")
    print("对象、时代、视角、风格、需要标注的要素等），回车确认：")
    print("=" * 60)
    text = input("绘图需求> ").strip()
    if not text:
        print("需求不能为空。")
        sys.exit(1)
    return text


def main() -> int:
    args = parse_args()

    # 首次启动：只让用户填一次 API Key，其余默认（aixw / gpt-5.6-sol / gpt-image-2）
    from setup import ensure_env
    first_run = False
    if not args.dry_run:
        first_run = not ensure_env(interactive_ok=True)

    try:
        cfg = load_config(
            refs_dir=args.refs, output_dir=args.out,
            max_attempts=args.max_attempts, no_search=args.no_search,
            require_llm=not args.dry_run, mode=args.mode,
        )
    except ConfigError as e:
        print(f"[配置错误] {e}")
        return 2

    if args.check:
        from logger import setup_logging
        from check import run_checks
        logger = setup_logging(cfg.log_dir)
        return 0 if run_checks(cfg, skip_image=args.skip_image) else 1

    # 首次配置完成后自动做一次连通性自检，确保模型真的能用
    if first_run:
        from logger import setup_logging
        from check import run_checks
        logger = setup_logging(cfg.log_dir)
        logger.info("首次配置完成，自动执行连通性自检…")
        if not run_checks(cfg, skip_image=False):
            logger.error("连通性自检未通过，请修正 .env 后重新运行。本次不继续。")
            return 1

    if args.dry_run:
        if cfg.mode == "rag":
            # rag 模式 dry-run：真实访问 RAGFlow 做一次检索连通性测试（不调用语言/绘图模型）
            from errors import RagflowError
            from ragflow_client import RAGFlowClient
            try:
                client = RAGFlowClient(cfg.ragflow_base_url, cfg.ragflow_api_key, timeout=cfg.ragflow_timeout)
                req_file = PROJECT_ROOT / "requirement.txt"
                text = (args.requirement
                        or (req_file.read_text(encoding="utf-8-sig").strip() if req_file.exists() else "")
                        or "考古 器物 复原")
                chunks = client.retrieve(text, cfg.ragflow_dataset_ids,
                                         top_k=cfg.retrieval_top_k,
                                         similarity_threshold=cfg.retrieval_sim_threshold,
                                         page_size=cfg.retrieval_page_size)
            except (RagflowError, OSError) as e:
                print(f"[dry-run] RAGFlow 检索失败: {getattr(e, 'message', e)}")
                return 1
            print(f"[dry-run] RAGFlow 检索连通正常，需求原文检索命中 {len(chunks)} 个片段:")
            for c in chunks[:5]:
                page = f" p.{c['page']}" if c.get("page") else ""
                print(f"  - [{c['document_name']}{page}] 相关度 {c['similarity']:.2f}: {c['content'][:50]}…")
            print(f"[dry-run] 共 {len(cfg.ragflow_dataset_ids)} 个 dataset: {cfg.ragflow_dataset_ids}")
            print("[dry-run] 未调用语言/绘图模型，配置校验通过。可以正式运行。")
            return 0
        try:
            refs = collect_references(cfg.refs_dir, cfg.per_file_char_limit, scan_policy=args.scan_policy)
        except AppError as e:
            print(f"[文献检查失败] {e.message}")
            return 1
        print(f"[dry-run] 文献摄取正常，共 {len(refs)} 篇:")
        for r in refs:
            kind = "视觉直读图片×" + str(len(r["pages"])) if r.get("pages") else f"{r['chars']} 字"
            print(f"  - {r['name']} ({r['ext']}, {kind})")
        print("[dry-run] 配置校验通过，未调用任何模型。可以正式运行。")
        return 0

    from logger import setup_logging
    logger = setup_logging(cfg.log_dir)

    requirement = get_requirement(args)
    logger.info(f"绘图需求: {requirement}")

    from pipeline import run
    try:
        result = run(cfg, requirement, scan_policy=args.scan_policy)
    except AppError as e:
        logger.error(f"流水线中止: {e.message}")
        if e.detail:
            logger.debug(f"细节: {e.detail}")
        return 1

    print("=" * 60)
    if result["ok"]:
        print(f"✅ 完成！最终插图: {result['final_image']}")
    else:
        print(f"⚠️ 未通过全部校验，问题清单见报告: {result['report']}")
    print(f"📄 生成报告: {result['report']}")
    print(f"📚 知识摘要: {result['knowledge_summary']}")
    print("=" * 60)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
