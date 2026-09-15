"""入库清单：记住每个文件入库时的内容哈希，让重复入库变成幂等操作。

对应 PROJECT_PLAN 3.2 的「文件哈希作为幂等键；重新上传只更新变化部分」。

没有它的话，每次 /ingest 都要把所有文件重新分片、重新调一遍 embedding，
而且文件删掉后留在库里的分片也没人清理。有了清单才能分辨出：
    - 新增：清单里没有
    - 变动：哈希变了
    - 最新：哈希没变，跳过
    - 已删除：清单里有、磁盘上没有，要连带删掉它的分片

清单落在 data/index_manifest.json，和向量库同级。它是**缓存不是真相**——
真相永远是 data/uploads/ 里的文件本身。清单丢了或写坏了，重跑一次全量入库即可。
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from config import cfg

# 清单格式版本。将来改结构时靠它做兼容，对不上就当作空清单全量重建。
VERSION = 1

# 算哈希时的分块大小，避免一次性把大文件读进内存
_CHUNK = 1 << 20


@dataclass
class Entry:
    """一个文件的入库记录。"""

    sha256: str
    size: int
    mtime: float
    chunks: int
    user_id: str
    indexed_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def matches(self, path: Path, user_id: str) -> bool:
        """内容没变、且是同一个 user_id 入的库，就可以跳过。"""
        if self.user_id != user_id:
            return False
        if not path.is_file():
            return False
        # 先用 size + mtime 快速挡掉绝大多数，不一致再算哈希
        stat = path.stat()
        if stat.st_size != self.size or abs(stat.st_mtime - self.mtime) > 1:
            return False
        return self.sha256 == file_hash(path)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()


class Manifest:
    """清单的读改写。键是文件的绝对路径（与向量库 metadata 里的 source 一致）。"""

    def __init__(self, path: Path | None = None):
        self.path = path or cfg.manifest_path
        self.entries: dict[str, Entry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 清单坏了不影响正确性，当作空的重建即可
            return
        if raw.get("version") != VERSION:
            return
        self.entries = {k: Entry(**v) for k, v in (raw.get("files") or {}).items()}

    def save(self) -> None:
        payload = {
            "version": VERSION,
            "files": {k: asdict(v) for k, v in self.entries.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            # 和 ToolTracer.save 一样：清单写不进去不该让入库本身失败
            print(f"[manifest] 写入失败: {exc}")

    # ---- 查询 ----
    def key(self, path: Path) -> str:
        return str(Path(path).resolve())

    def is_current(self, path: Path, user_id: str) -> bool:
        entry = self.entries.get(self.key(path))
        return bool(entry and entry.matches(path, user_id))

    def record(self, path: Path, user_id: str, chunks: int) -> None:
        """记下一次成功的入库。"""
        stat = path.stat()
        self.entries[self.key(path)] = Entry(
            sha256=file_hash(path),
            size=stat.st_size,
            mtime=stat.st_mtime,
            chunks=chunks,
            user_id=user_id,
        )

    def forget(self, path: Path) -> None:
        self.entries.pop(self.key(path), None)

    def missing_from(self, on_disk: set[str]) -> list[str]:
        """清单里有、但磁盘上已经没有的路径。"""
        return [key for key in self.entries if key not in on_disk]

    def paths_for(self, user_id: str) -> list[str]:
        return [k for k, v in self.entries.items() if v.user_id == user_id]
