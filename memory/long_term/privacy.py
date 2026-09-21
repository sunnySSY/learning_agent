"""跨短期和长期记忆的数据导出与删除；只处理应用管理的数据。"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .repository import LearningStore
from ..short_term.store import ThreadStore
from config import cfg


def export_user(learning: LearningStore, threads: ThreadStore, user_id: str, scope: str, output: Path) -> dict:
    if scope not in {"memory", "threads", "all"}:
        raise ValueError("导出范围必须是 memory、threads 或 all")
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"导出目标已存在：{output}")
    data = learning.export_rows(user_id, scope)
    if scope in {"threads", "all"}:
        data["thread_registry"] = threads.thread_rows(user_id)
    if scope == "all":
        from rag.manifest import Manifest
        data["files"] = [{"path": path, "managed": Path(path).resolve().is_file()} for path in Manifest().paths_for(user_id)]
        user_dir = _safe_user_dir(user_id)
        data["artifacts"] = {
            "flashcards": [str(path) for path in (cfg.flashcard_dir / user_dir).glob("*.json")] if (cfg.flashcard_dir / user_dir).exists() else [],
            "traces": [str(path) for path in (cfg.trace_dir / user_dir).glob("*.jsonl")] if (cfg.trace_dir / user_dir).exists() else [],
        }
    payload = {"schema_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "user_id": user_id, "timezone": learning.timezone_name, "scope": scope, "data": data}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temporary, output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return payload


def preview_delete(learning: LearningStore, threads: ThreadStore, user_id: str, scope: str, thread_id: str | None = None) -> dict:
    if scope not in {"memory", "thread", "all"}:
        raise ValueError("删除范围必须是 memory、thread 或 all")
    result = {"scope": scope, "user_id": user_id, "facts": 0, "knowledge": 0, "turns": 0, "threads": 0}
    if scope == "thread":
        if not thread_id:
            raise ValueError("删除 thread 必须提供 thread_id")
        threads.check_owner(user_id, thread_id)
        result["threads"] = 1
        result["turns"] = learning.connection.execute("SELECT COUNT(*) FROM turns WHERE user_id=? AND thread_id=?", (user_id, thread_id)).fetchone()[0]
    else:
        result.update({"facts": learning.connection.execute("SELECT COUNT(*) FROM memory_facts WHERE user_id=?", (user_id,)).fetchone()[0], "knowledge": learning.connection.execute("SELECT COUNT(*) FROM knowledge_points WHERE user_id=?", (user_id,)).fetchone()[0], "turns": learning.connection.execute("SELECT COUNT(*) FROM turns WHERE user_id=?", (user_id,)).fetchone()[0], "threads": len(threads.thread_rows(user_id))})
    return result


def delete_user_data(learning: LearningStore, threads: ThreadStore, user_id: str, scope: str, thread_id: str | None = None) -> dict:
    preview = preview_delete(learning, threads, user_id, scope, thread_id)
    job_id = learning.create_deletion_job(user_id, scope, thread_id, preview)
    try:
        if scope == "thread":
            thread_row = next(row for row in threads.thread_rows(user_id) if row["thread_id"] == thread_id)
            deleted = learning.delete_thread_data(user_id, thread_id)
            threads.delete_record(user_id, thread_id)
            trace_dir = cfg.trace_dir / _safe_user_dir(user_id)
            for turn_id in deleted.get("turn_ids", []):
                path = trace_dir / f"{turn_id}.jsonl"
                if path.exists(): path.unlink()
            _remove_legacy(user_id, thread_row["name"])
            result = dict(preview)
        elif scope == "memory":
            learning.delete_user(user_id)
            threads.clear_user_records(user_id)
            for row in threads.thread_rows(user_id): _remove_legacy(user_id, row["name"])
            _remove_user_artifacts(user_id, include_legacy=True)
            result = dict(preview)
        else:
            thread_rows = threads.thread_rows(user_id)
            learning.delete_user(user_id)
            threads.delete_user_records(user_id)
            # all 是本应用可管理数据的删除；导出的外部文件不在此范围。
            from rag.manifest import Manifest
            from rag.store import delete_user as delete_vectors
            manifest = Manifest()
            result = dict(preview)
            result["vectors"] = delete_vectors(user_id)
            paths = manifest.paths_for(user_id)
            managed_root = cfg.data_dir.resolve()
            removed_files = 0
            for raw_path in paths:
                candidate = Path(raw_path).resolve()
                owners = manifest.users_for_path(candidate)
                if candidate.is_file() and managed_root in candidate.parents and not [owner for owner in owners if owner != user_id]:
                    candidate.unlink(); removed_files += 1
            result["files"] = removed_files
            result["manifest"] = manifest.remove_user(user_id)
            for row in thread_rows: _remove_legacy(user_id, row["name"])
            _remove_user_artifacts(user_id, include_legacy=True)
        result["job_id"] = job_id
        learning.finish_deletion_job(job_id, result)
        return result
    except Exception as exc:
        learning.finish_deletion_job(job_id, preview, str(exc))
        raise


def reset_thread(learning: LearningStore, threads: ThreadStore, user_id: str, thread_id: str) -> dict:
    """清空当前会话并撤销该会话产生的长期数据，但保留会话 ID。"""
    threads.check_owner(user_id, thread_id)
    deleted = learning.delete_thread_data(user_id, thread_id)
    threads.clear(user_id, thread_id)
    trace_dir = cfg.trace_dir / _safe_user_dir(user_id)
    for turn_id in deleted.get("turn_ids", []):
        path = trace_dir / f"{turn_id}.jsonl"
        if path.exists():
            path.unlink()
    return deleted


def delete_fact_with_context(learning: LearningStore, threads: ThreadStore, user_id: str, fact_id: str) -> dict:
    """删除事实并清空产生它的会话上下文，防止旧消息再次恢复该事实。"""
    rows = learning.connection.execute(
        "SELECT DISTINCT t.thread_id FROM memory_sources s JOIN turns t ON t.turn_id=s.turn_id WHERE s.fact_id=? AND t.user_id=?",
        (fact_id, user_id),
    ).fetchall()
    for row in rows:
        reset_thread(learning, threads, user_id, row[0])
    try:
        learning.delete_fact(user_id, fact_id)
    except ValueError:
        # reset_thread removes facts whose last source was in that context.
        pass
    return {"fact_id": fact_id, "threads_cleared": len(rows)}


def _safe_user_dir(user_id: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]", "_", user_id).strip(".")[:80] or "default"


def _remove_legacy(user_id: str, name: str) -> None:
    if user_id != "default":
        return
    root = cfg.memory_dir.resolve()
    path = (root / f"{name}.json").resolve()
    if path.parent == root and path.is_file():
        path.unlink()
    backup = root / "migration_backup" / f"{name}.json"
    if backup.is_file():
        backup.unlink()


def _remove_user_artifacts(user_id: str, include_legacy: bool = False) -> None:
    """删除带用户目录的新产物，并按 default 兼容规则清理旧无归属产物。"""
    user_dir_name = _safe_user_dir(user_id)
    for root in (cfg.flashcard_dir, cfg.trace_dir):
        user_dir = root / user_dir_name
        if user_dir.exists():
            shutil.rmtree(user_dir)
        if include_legacy and user_id == "default" and root.exists():
            if root == cfg.flashcard_dir:
                for path in root.glob("*.json"):
                    path.unlink()
            else:
                for path in root.glob("default-*.jsonl"):
                    path.unlink()
