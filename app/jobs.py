"""Explicit indexing/deletion worker.

The development deployment calls this worker through FastAPI background tasks
or ``python worker.py``.  A production queue can call the same ``run_*``
methods after claiming a database row.
"""

from __future__ import annotations

import logging
from config import cfg
from rag.pipeline import index_path
from rag.store import delete_file, drop_file_old_versions

from .observability import INDEX_JOBS
from .repository import ProductRepository, config_hash


log = logging.getLogger("learning-agent")


class IndexWorker:
    def __init__(self, repository: ProductRepository):
        self.repository = repository

    def run_index(self, job_id: str) -> None:
        job = self.repository.claim_job(job_id)
        if not job:
            return
        file_row = self.repository.file(job["user_id"], job["file_id"])
        if not file_row:
            self.repository.finish_job(job_id, success=False, error_code="file_not_found", error_message="文件不存在")
            return
        path = (cfg.product_upload_dir / file_row["storage_key"]).resolve()
        try:
            if cfg.product_upload_dir.resolve() not in path.parents:
                raise ValueError("invalid_storage_key")
            self.repository.update_job_stage(job_id, "parsing")
            self.repository.update_job_stage(job_id, "splitting")
            self.repository.update_job_stage(job_id, "embedding")
            count = index_path(path, user_id=job["user_id"], file_id=job["file_id"],
                               index_version=job["target_version"], index_config_hash=config_hash(), replace=False)
            if count <= 0:
                self.repository.finish_job(job_id, success=False, error_code="empty_text", error_message="没有解析到文字；扫描版 PDF 请上传含文字层的版本")
                INDEX_JOBS.labels("failed", "parsing").inc()
                return
            self.repository.update_job_stage(job_id, "writing")
            self.repository.finish_job(job_id, success=True, chunk_count=count)
            # Metadata now points at the complete version; old vectors can be
            # reclaimed without ever creating a retrieval gap.
            drop_file_old_versions(job["file_id"], job["target_version"], job["user_id"])
            INDEX_JOBS.labels("succeeded", "finalizing").inc()
        except ValueError as exc:
            code = str(exc) if str(exc) in {"invalid_storage_key", "unsupported_file_type", "malformed_file"} else "malformed_file"
            self.repository.finish_job(job_id, success=False, error_code=code, error_message="文件无法解析，请检查格式或重新上传")
            INDEX_JOBS.labels("failed", "parsing").inc()
        except Exception:  # noqa: BLE001
            log.error("index job failed", extra={"event": "index_failed", "object_id": job_id})
            self.repository.finish_job(job_id, success=False, error_code="provider_or_index_error", error_message="入库暂时失败，请稍后重试")
            INDEX_JOBS.labels("failed", "embedding").inc()

    def run_delete(self, job_id: str) -> None:
        job = self.repository.job_for_operation(job_id, "delete") if hasattr(self.repository, "job_for_operation") else self.repository.job_by_id(job_id)
        if not job:
            return
        row = self.repository.file_including_deleted(job["user_id"], job["file_id"]) if hasattr(self.repository, "file_including_deleted") else None
        if row:
            delete_file(job["file_id"], job["user_id"])
            path = (cfg.product_upload_dir / row["storage_key"]).resolve()
            if cfg.product_upload_dir.resolve() in path.parents and path.is_file():
                path.unlink()
        self.repository.complete_delete(job_id)

    def run_pending(self) -> int:
        self.cleanup_images()
        self.repository.recover_stale_jobs()
        rows = self.repository.pending_jobs()
        for row in rows:
            if row["operation"] == "index": self.run_index(row["id"])
            else: self.run_delete(row["id"])
        return len(rows)

    def cleanup_images(self) -> int:
        removed = 0
        root = cfg.product_image_dir.resolve()
        for row in self.repository.expired_attachments():
            path = (cfg.product_image_dir / row["storage_key"]).resolve()
            if root in path.parents and path.is_file():
                path.unlink(); removed += 1
            self.repository.mark_attachment_deleted(row["id"])
        return removed
