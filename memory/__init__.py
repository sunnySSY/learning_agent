"""记忆能力的统一入口。

根目录只保留 ``short_term`` 和 ``long_term`` 两个实现包。这里提供稳定的
顶层 API，并注册旧模块路径的兼容别名，避免已有调用方因目录重构失效。
"""

import sys as _sys

from .short_term.checkpoint import thread_config
from .short_term.legacy import Memory
from .short_term.pipeline import close_store, learning_store, thread_store
from .short_term.store import ThreadStore
from .long_term.knowledge import KnowledgeService
from .long_term.privacy import delete_fact_with_context, delete_user_data, export_user, preview_delete, reset_thread
from .long_term.repository import LearningStore
from .long_term.scheduler import ReviewScheduler
from .long_term.service import MemoryService


# 兼容 memory.pipeline、memory.repository 等旧模块导入；实现本身全部位于两个子包。
from .short_term import checkpoint as _checkpoint_module
from .short_term import legacy as _legacy_module
from .short_term import pipeline as _pipeline_module
from .short_term import store as _store_module
from .long_term import database as _database_module
from .long_term import extraction as _extraction_module
from .long_term import knowledge as _knowledge_module
from .long_term import repository as _repository_module
from .long_term import scheduler as _scheduler_module
from .long_term import schemas as _schemas_module
from .long_term import service as _service_module
from .long_term import privacy as _privacy_module


def _register_compat_module(name: str, module: object) -> None:
    _sys.modules[f"{__name__}.{name}"] = module
    globals()[name] = module


for _name, _module in {
    "checkpoint": _checkpoint_module,
    "database": _database_module,
    "extraction": _extraction_module,
    "knowledge": _knowledge_module,
    "legacy": _legacy_module,
    "pipeline": _pipeline_module,
    "privacy": _privacy_module,
    "repository": _repository_module,
    "scheduler": _scheduler_module,
    "schemas": _schemas_module,
    "service": _service_module,
    "store": _store_module,
}.items():
    _register_compat_module(_name, _module)

__all__ = [
    "ThreadStore",
    "thread_config",
    "thread_store",
    "close_store",
    "Memory",
    "LearningStore",
    "MemoryService",
    "learning_store",
    "export_user",
    "preview_delete",
    "delete_user_data",
    "reset_thread",
    "delete_fact_with_context",
    "KnowledgeService",
    "ReviewScheduler",
]
