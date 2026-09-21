"""会话注册表：UUID、用户归属、会话查询及完整清理。"""

from pathlib import Path
from uuid import uuid4

from .checkpoint import open_checkpointer
from .legacy import import_legacy


class ThreadStore:
    """本地单进程 CLI 的会话归属注册表；会话名称不是文件路径。"""

    def __init__(self, path: Path):
        self.connection, self.saver = open_checkpointer(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS study_threads ("
            "thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, "
            "legacy_imported INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id, name))"
        )
        self.connection.commit()

    def get_thread(self, user_id: str, name: str) -> str:
        if not user_id.strip() or not name.strip():
            raise ValueError("用户和会话名不能为空")
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO study_threads(thread_id, user_id, name) VALUES (?, ?, ?)",
                (str(uuid4()), user_id, name),
            )
        return self.connection.execute(
            "SELECT thread_id FROM study_threads WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()[0]

    def check_owner(self, user_id: str, thread_id: str) -> None:
        row = self.connection.execute(
            "SELECT 1 FROM study_threads WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        ).fetchone()
        if row is None:
            raise ValueError("会话不存在或不属于当前用户")

    def list_threads(self, user_id: str) -> list[tuple[str, str]]:
        return self.connection.execute(
            "SELECT thread_id, name FROM study_threads WHERE user_id = ? ORDER BY name",
            (user_id,),
        ).fetchall()

    def thread_rows(self, user_id: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT thread_id,user_id,name,legacy_imported FROM study_threads WHERE user_id=? ORDER BY name", (user_id,))]

    def delete_record(self, user_id: str, thread_id: str) -> None:
        self.clear(user_id, thread_id)
        with self.connection:
            self.connection.execute("DELETE FROM study_threads WHERE user_id=? AND thread_id=?", (user_id, thread_id))

    def delete_user_records(self, user_id: str) -> int:
        rows = self.thread_rows(user_id)
        for row in rows:
            self.saver.delete_thread(row["thread_id"])
        with self.connection:
            self.connection.execute("DELETE FROM study_threads WHERE user_id=?", (user_id,))
        return len(rows)

    def clear_user_records(self, user_id: str) -> int:
        """清空用户线程 checkpoint，但保留线程名称/UUID，供 memory reset 后继续使用。"""
        rows = self.thread_rows(user_id)
        for row in rows:
            self.saver.delete_thread(row["thread_id"])
        with self.connection:
            self.connection.execute("UPDATE study_threads SET legacy_imported=1 WHERE user_id=?", (user_id,))
        return len(rows)

    def clear(self, user_id: str, thread_id: str) -> None:
        self.check_owner(user_id, thread_id)
        # 删除全部快照及 pending writes；旧 JSON 不再重新导入。
        with self.connection:
            self.connection.execute(
                "UPDATE study_threads SET legacy_imported = 1 WHERE thread_id = ?",
                (thread_id,),
            )
        self.saver.delete_thread(thread_id)

    def import_legacy(self, graph, user_id: str, thread_id: str, directory: Path) -> int:
        """委托旧格式迁移，保留原有调用接口。"""
        return import_legacy(self, graph, user_id, thread_id, directory)

    def close(self) -> None:
        self.connection.close()
