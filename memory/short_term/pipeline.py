"""应用级记忆入口：统一管理短期和长期存储的生命周期。

具体实现分别位于 ``memory.short_term`` 和 ``memory.long_term``；本模块只
保留应用层的共享实例入口，避免改变现有调用方的使用方式。
"""

from config import cfg

from .store import ThreadStore
from ..long_term.repository import LearningStore


_STORE = None
_LEARNING = None


def thread_store() -> ThreadStore:
    global _STORE
    if _STORE is None:
        _STORE = ThreadStore(cfg.checkpoint_db)
    return _STORE


def close_store() -> None:
    global _STORE, _LEARNING
    if _STORE is not None:
        _STORE.close()
        _STORE = None
    if _LEARNING is not None:
        _LEARNING.close()
        _LEARNING = None


def learning_store() -> LearningStore:
    global _LEARNING
    if _LEARNING is None:
        _LEARNING = LearningStore()
    return _LEARNING
