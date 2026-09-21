"""旧 JSON 历史兼容器及一次性迁移；CLI 不再写入旧格式。"""

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage

from config import cfg

from .checkpoint import thread_config

if TYPE_CHECKING:
    from .store import ThreadStore


class Memory:
    """兼容原 JSON 历史接口，供旧调用方使用。"""

    def __init__(self, session_id: str = "default", window: int = 20):
        self.session_id = session_id
        self.window = window
        self.path = cfg.memory_dir / f"{session_id}.json"
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, role: str, content: str) -> None:
        """role 取 user 或 assistant。"""
        self.items.append({"role": role, "content": content})
        self._save()

    def history(self) -> list[dict]:
        """最近 window 条消息，用于喂给模型。"""
        return self.items[-self.window:]

    def clear(self) -> None:
        self.items = []
        self._save()

    @property
    def size(self) -> int:
        return len(self.items)


def import_legacy(store: "ThreadStore", graph, user_id: str, thread_id: str, directory: Path) -> int:
    """默认用户旧 JSON 一次性导入；稳定 message ID 防止中断重试重复。"""
    store.check_owner(user_id, thread_id)
    name, imported = store.connection.execute(
        "SELECT name, legacy_imported FROM study_threads WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    if user_id != "default" or imported:
        return 0
    directory = Path(directory).resolve()
    path = (directory / f"{name}.json").resolve()
    if path.parent != directory:
        # 新会话名可含路径字符，但不能用它访问旧目录外的文件。
        return 0
    config = thread_config(thread_id)
    snapshot = graph.get_state(config)
    items = []
    if not snapshot.values and path.exists():
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list) or len(items) % 2:
            raise ValueError("旧会话 JSON 必须包含完整问答对")
        for i, item in enumerate(items):
            role = "user" if i % 2 == 0 else "assistant"
            if (not isinstance(item, dict) or item.get("role") != role
                    or not isinstance(item.get("content"), str)):
                raise ValueError("旧会话 JSON 的 role/content 格式不正确")
        messages = [
            (HumanMessage if item["role"] == "user" else AIMessage)(
                content=item["content"], id=f"legacy-{thread_id}-{i}"
            )
            for i, item in enumerate(items)
        ]
        if messages:
            graph.update_state(config, {"messages": messages}, as_node="finalize")
    with store.connection:
        store.connection.execute(
            "UPDATE study_threads SET legacy_imported = 1 WHERE thread_id = ?",
            (thread_id,),
            )
    # 校验并写入 checkpoint 后，将旧文件移到受管备份目录，实时入口不再读取它。
    backup_dir = directory / "migration_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{name}.json"
    if path.exists() and not backup_path.exists():
        shutil.move(str(path), str(backup_path))
    return len(items)
