"""SQLite saver 初始化与 LangGraph thread 配置。"""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def open_checkpointer(path: Path) -> tuple[sqlite3.Connection, SqliteSaver]:
    """打开数据库；连接由调用方持有并关闭。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection, SqliteSaver(connection)


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}
