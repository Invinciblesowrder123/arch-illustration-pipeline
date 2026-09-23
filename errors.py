# -*- coding: utf-8 -*-
"""类型化错误体系：调用方只处理 AppError 及其子类。"""
from __future__ import annotations


class AppError(Exception):
    """项目内所有可预期错误的基类。"""

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


class IngestError(AppError):
    """文献读取/解析失败。"""


class KnowledgeError(AppError):
    """知识学习阶段失败（模型输出不可解析等）。"""


class ImageGenError(AppError):
    """绘图模型调用失败。"""


class VerifyError(AppError):
    """校验阶段失败。"""


class SearchError(AppError):
    """联网搜索失败（可降级继续）。"""


class RagflowError(AppError):
    """RAGFlow 知识层调用失败（检索/上传/配置）。"""


class ReviseError(AppError):
    """指定重绘（--revise）失败：基准 run 产物缺失、反馈约束冲突等。"""
