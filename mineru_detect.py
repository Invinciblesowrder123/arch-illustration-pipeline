# -*- coding: utf-8 -*-
"""MinerU 通用探测与调用：不写死任何本机路径，按优先级探测用户机器上的 MinerU。

探测顺序（命中即返回）：
  1. 环境变量 MINERU_CMD —— 显式指定 mineru / magic-pdf 可执行文件路径
  2. PATH 中的命令        —— mineru、magic-pdf
  3. 当前 Python 环境库    —— importlib 检查 mineru / magic_pdf 包，并在同环境找 CLI 入口
  4. 常见 venv 目录扫描    —— 工作目录及上级（最多 3 级）、用户主目录及其 venvs/.venvs 子目录下，
                              目录名含 "mineru"（含 .venv-mineru 等隐藏目录）内的 CLI 入口

CLI 兼容：新版 mineru（2.x/3.x，`mineru -p pdf -o outdir`）与旧版 magic-pdf 同参数风格。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

_CLI_NAMES = ("mineru", "magic-pdf")  # 新版在前
_PACKAGE_NAMES = ("mineru", "magic_pdf")
_VENV_SUBDIRS = ("Scripts", "bin")


def _venv_cli_candidates(venv_dir: Path) -> list[Path]:
    """在疑似 venv 目录里找 mineru/magic-pdf CLI 入口。"""
    out: list[Path] = []
    for sub in _VENV_SUBDIRS:
        for name in _CLI_NAMES:
            exe = venv_dir / sub / (f"{name}.exe" if os.name == "nt" else name)
            if exe.is_file():
                out.append(exe)
    return out


def _scan_venv_dirs(roots: list[Path]) -> list[Path]:
    """在候选根目录下（最多下探 2 层）找目录名含 mineru 的目录。"""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in os.scandir(root):
                if entry.is_dir() and "mineru" in entry.name.lower():
                    found.append(Path(entry.path))
                    continue  # 第一层命中后不再下探该目录内部
                if entry.is_dir():
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_dir() and "mineru" in sub.name.lower():
                                found.append(Path(sub.path))
                    except OSError:
                        pass
        except OSError:
            continue
    return found


def _candidate_roots() -> list[Path]:
    cwd = Path.cwd()
    roots = [cwd]
    parent = cwd.parent
    for _ in range(3):  # 向上最多 3 级（覆盖 D:/AI/R 这类布局）
        roots.append(parent)
        if parent == parent.parent:
            break
        parent = parent.parent
    home = Path.home()
    roots += [home, home / "venvs", home / ".venvs", home / "envs"]
    # 去重保序
    seen, uniq = set(), []
    for r in roots:
        rp = str(r.resolve()) if r.exists() else str(r)
        if rp not in seen:
            seen.add(rp)
            uniq.append(r)
    return uniq


def detect_mineru() -> dict | None:
    """探测 MinerU CLI。返回 {"cmd": 可执行文件路径, "source": 探测来源} 或 None。"""
    # 1) 显式环境变量
    explicit = os.environ.get("MINERU_CMD", "").strip()
    if explicit and Path(explicit).is_file():
        return {"cmd": explicit, "source": "环境变量 MINERU_CMD"}

    # 2) PATH
    for name in _CLI_NAMES:
        p = shutil.which(name)
        if p:
            return {"cmd": p, "source": f"PATH 中的 {name} 命令"}

    # 3) 当前 Python 环境库（用户环境的 site-packages）
    for pkg in _PACKAGE_NAMES:
        if importlib.util.find_spec(pkg) is not None:
            # 同环境的 venv CLI 入口
            for sub in _VENV_SUBDIRS:
                for name in _CLI_NAMES:
                    exe = Path(sys.prefix) / sub / (f"{name}.exe" if os.name == "nt" else name)
                    if exe.is_file():
                        return {"cmd": str(exe), "source": f"当前环境库（已安装 {pkg} 包）"}
            return {"cmd": "", "source": f"当前环境库（已安装 {pkg} 包，但未找到 CLI 入口）"}

    # 4) 常见 venv 目录扫描
    for venv_dir in _scan_venv_dirs(_candidate_roots()):
        for exe in _venv_cli_candidates(venv_dir):
            return {"cmd": str(exe), "source": f"venv 目录扫描（{venv_dir}）"}
    return None


def parse_pdf_with_mineru(cmd: str, pdf_path: Path, work_dir: Path, timeout: int = 900) -> tuple[str | None, str]:
    """调用 MinerU 解析 PDF，返回 (markdown 文本 | None, 错误信息)。

    默认使用 -b pipeline 后端（经典模型，CPU 可跑，约 2-10 秒/页）；
    MinerU 3.x 默认的 hybrid-engine 需要本地 GPU 算力，普通机器跑不动。
    可用环境变量 MINERU_BACKEND 覆盖（pipeline / vlm-engine / hybrid-engine 等）。
    注意：MinerU 首次在新机器运行会自动下载模型（可能 1-2 GB），耗时较长属正常现象。
    """
    backend = os.environ.get("MINERU_BACKEND", "pipeline")
    out_dir = work_dir / ("_mineru_" + pdf_path.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 关键：mineru CLI 会在本地起临时 API 服务并回连 127.0.0.1 轮询任务状态，
    # 系统代理环境变量会把 localhost 请求也劫持走代理（代理挂了就报 404）。
    # 因此给子进程一个无代理的干净环境。
    clean_env = {k: v for k, v in os.environ.items()
                 if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    clean_env["NO_PROXY"] = "*"
    clean_env["no_proxy"] = "*"
    try:
        proc = subprocess.run(
            [cmd, "-p", str(pdf_path), "-o", str(out_dir), "-b", backend],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", env=clean_env,
        )
    except subprocess.TimeoutExpired:
        return None, f"MinerU 解析超时（>{timeout}s）。可增大 MINERU_TIMEOUT 或改用视觉直读。"
    except FileNotFoundError:
        return None, f"MinerU 可执行文件不存在: {cmd}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:].strip()
        return None, f"MinerU 退出码 {proc.returncode}。{tail}"

    # 输出结构因版本而异，递归取最大的 .md
    mds = sorted(out_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if not mds:
        return None, "MinerU 运行完成但未产出 Markdown。"
    text = mds[0].read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 50:
        return None, "MinerU 产出的文本过少。"
    return text, ""
