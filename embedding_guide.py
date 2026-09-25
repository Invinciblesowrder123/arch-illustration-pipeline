# -*- coding: utf-8 -*-
"""云 embedding 服务商推荐与配置指引。

用途：当 RAGFlow 缺少可用的 embedding 模型时，给**用户**一条可执行、带依据的提示，
而不是抛一个只有开发者看得懂的异常。推荐表同时被 `pipeline.py`（rag 模式预检）和
`scripts/ragflow_ops.py health` 引用，避免两处各写一份而慢慢走偏。

价格口径：2026-09 查证的各服务商公开定价，**以服务商官方最新公示为准**；
下面只写量级与条件，不替用户做计费承诺。
"""
from __future__ import annotations

# RAGFlow 侧「工厂名 / 模型 / 参考价 / 维度 / 何时选」。
# factory 必须与 RAGFlow conf/models/*.json 里登记的名字一致，否则用户照抄会失败。
PROVIDER_RECOMMENDATIONS: list[dict[str, str]] = [
    {
        "factory": "ZHIPU-AI",
        "model": "embedding-3",
        "price": "0.5 元/百万 tokens（Batch API 0.25）",
        "dim": "2048（可自定义 256-2048）",
        "when": "默认推荐：国内直连、中文效果好、RAGFlow 原生支持。本项目当前在用。",
        "url": "https://open.bigmodel.cn",
    },
    {
        "factory": "Tongyi-Qianwen",
        "model": "text-embedding-v4",
        "price": "0.6 元/百万 tokens（国内地域）",
        "dim": "1024 默认（可选 64-2048）",
        "when": "备选：阿里百炼，新账号通常有免费额度，适合先白嫖验证。",
        "url": "https://bailian.console.aliyun.com",
    },
    {
        "factory": "OpenAI-API-Compatible / VLLM",
        "model": "BAAI/bge-m3",
        "price": "硅基流动官方定价页列为「免费」（输入输出均免）；另有付费的 Pro/BAAI/bge-m3",
        "dim": "1024",
        "when": "★ 迁移省事：与旧库本地 TEI 同一个模型，同维度时**理论上可复用旧向量、免全量重解析**"
                "（仍需抽样验证相似度分布再决定）。通过 OpenAI 兼容接口接入。\n"
                "     ⚠ 「免费」是平台列的免费模型，不是合同保障：有过模型被移出免费名单的先例，"
                "免费版也有限速，大批量入库仍要分批。",
        "url": "https://siliconflow.cn/pricing",
    },
    {
        "factory": "Jina",
        "model": "jina-embeddings-v3",
        "price": "有每月免费额度（以平台公示为准）",
        "dim": "1024",
        "when": "语料含多语种时考虑；国内直连稳定性不如前两者。",
        "url": "https://jina.ai",
    },
    {
        "factory": "OpenAI",
        "model": "text-embedding-3-small",
        "price": "$0.02/百万 tokens（约 ¥0.14）",
        "dim": "1536",
        "when": "仅在国内网络与合规都可行的前提下考虑；中文表现一般。",
        "url": "https://platform.openai.com",
    },
]

# 本地嵌入（TEI）的取舍，单独说明——它不是"云服务商"，但用户经常需要这个选项。
LOCAL_TEI_NOTE = (
    "本地 TEI（BAAI/bge-m3）不用花钱、数据不出本机，但常驻约 12GB 内存；\n"
    "    需要时用 `ragflow_ops.py up --with-tei --wait` 临时拉起。"
)


def render_provider_table() -> str:
    """渲染服务商推荐表（纯文本，便于直接打到终端/日志）。"""
    lines = ["可选云 embedding 服务商（factory 名须与 RAGFlow 登记一致）："]
    for i, p in enumerate(PROVIDER_RECOMMENDATIONS, 1):
        lines.append(f"  {i}. {p['factory']} / {p['model']}")
        lines.append(f"     价格 {p['price']}；维度 {p['dim']}")
        lines.append(f"     适用：{p['when']}")
        lines.append(f"     申请：{p['url']}")
    return "\n".join(lines)


def render_setup_guide(reason: str) -> str:
    """生成「缺少可用 embedding 模型」时的完整处置指引。"""
    return "\n".join([
        "",
        "=" * 68,
        "✗ RAGFlow 没有可用的 embedding 模型，rag 模式无法检索。",
        f"  原因：{reason}",
        "=" * 68,
        "请先添加一个 embedding 模型（只需做一次）：",
        "",
        "  ① 在下面的服务商里挑一个，申请 API key；",
        "  ② 把它写进项目根目录 .env 的 ZHIPU_API_KEY（或对应的 key 变量）；",
        "  ③ 执行一键接入：",
        "     python scripts/ragflow_ops.py embedding-init",
        "     （默认接智谱 ZHIPU-AI/embedding-3；换服务商加 --provider/--model 等参数）",
        "  ④ 复查：",
        "     python scripts/ragflow_ops.py health",
        "",
        render_provider_table(),
        "",
        f"  本地方案：{LOCAL_TEI_NOTE}",
        "=" * 68,
        "",
    ])
