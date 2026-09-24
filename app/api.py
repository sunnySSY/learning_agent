"""FastAPI HTTP surface for Phase 5.

The API is intentionally thin: authentication and object ownership are
resolved before repository calls, file upload never calls embedding, and the
existing graph/memory/RAG packages remain reusable by CLI and Web clients.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from config import cfg
from graph.builder import run_stream
from memory import delete_fact_with_context, delete_user_data, learning_store, thread_store
from memory.long_term.service import MemoryService

from .auth import Authenticator, Principal, SlidingWindowLimiter, bearer, ip_digest, request_id
from .jobs import IndexWorker
from .observability import ACTIVE_SSE, REQUESTS, REQUEST_LATENCY, RETRIEVER_CALLS, UPLOAD_BYTES, VISION_CALLS, configure_logging, configure_tracing, metrics_payload
from .repository import ProductRepository
from .schemas import ChatPayload, Problem, ThreadCreate, ThreadRename, VisionConfirmation
from .vision import VisionResult, VisionService, context_text, preprocess_image


log = logging.getLogger("learning-agent")


class ProblemException(Exception):
    def __init__(self, status: int, code: str, detail: str, *, retryable: bool = False, title: str = "请求失败"):
        self.status = status; self.code = code; self.detail = detail; self.retryable = retryable; self.title = title


def _problem(request: Request, exc: ProblemException) -> JSONResponse:
    rid = getattr(request.state, "request_id", str(uuid4()))
    body = Problem(type=f"https://errors.study-agent.local/{exc.code}", title=exc.title, status=exc.status, code=exc.code, detail=exc.detail, request_id=rid, retryable=exc.retryable).model_dump()
    headers = {"X-Request-ID": rid}
    rate = getattr(request.state, "rate_limit", None)
    if rate:
        headers.update({"X-RateLimit-Limit": str(rate[0]), "X-RateLimit-Remaining": str(rate[1]), "X-RateLimit-Reset": str(rate[2])})
    if exc.status == 429:
        headers.update({"Retry-After": str(max(1, int(rate[2] - __import__("time").time())) if rate else "10" )})
    return JSONResponse(body, status_code=exc.status, media_type="application/problem+json", headers=headers)


def _safe_name(value: str, fallback: str = "file") -> str:
    value = Path(value or fallback).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return value[:255] or fallback


def _safe_user_dir(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", user_id).strip(".")[:80] or "user"


def _document_type(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ProblemException(415, "malformed_file", "PDF 文件签名无效")
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(__import__("io").BytesIO(data)).pages)
            if pages > 300:
                raise ProblemException(413, "file_page_limit", "PDF 页数不能超过 300 页")
        except ProblemException:
            raise
        except Exception as exc:
            raise ProblemException(415, "malformed_file", "PDF 文件损坏或无法解析") from exc
        return "application/pdf", ".pdf"
    if suffix in {".md", ".markdown"}:
        try: data.decode("utf-8")
        except UnicodeDecodeError as exc: raise ProblemException(415, "malformed_file", "文本文件不是有效 UTF-8") from exc
        return "text/markdown", suffix
    if suffix == ".txt":
        try: data.decode("utf-8")
        except UnicodeDecodeError as exc: raise ProblemException(415, "malformed_file", "文本文件不是有效 UTF-8") from exc
        return "text/plain", suffix
    raise ProblemException(415, "unsupported_file_type", "不支持该文件类型，请上传 PDF、Markdown 或 TXT")


def _index_view(row: dict, job_id: str | None = None, stage: str | None = None) -> dict:
    status = row["index_status"]
    failed = status in {"failed", "stale"} and bool(row.get("last_error_code"))
    return {
        "status": status,
        "stage": stage,
        "chunk_count": row.get("chunk_count", 0),
        "active_version": row.get("active_index_version"),
        "job_id": job_id,
        "updated_at": row.get("index_updated_at") or row.get("uploaded_at"),
        "error": ({"code": row["last_error_code"], "message": row["last_error_message"], "error_id": row["last_error_id"]} if failed else None),
        "can_index": status in {"unindexed", "stale", "failed"},
        "can_retry": status in {"stale", "failed"},
        "message": "文件已上传，尚未入库" if status == "unindexed" else None,
    }


def _file_view(row: dict, repository: ProductRepository | None = None) -> dict:
    job_id = None; stage = None
    if repository:
        job = repository.latest_job_for_file(row["id"])
        if job:
            job_id = job["id"]
            stage = job["stage"] if row["index_status"] in {"queued", "indexing"} else None
    return {"id": row["id"], "display_name": row["display_name"], "media_type": row["media_type"], "size_bytes": row["size_bytes"], "sha256": row["sha256"], "uploaded_at": row["uploaded_at"], "index": _index_view(row, job_id, stage)}


def _sse(event_iter):
    counter = 0
    for event, data in event_iter:
        counter += 1
        yield f"id: {counter}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _cleanup_turn_images(repository: ProductRepository, user_id: str, turn_id: str) -> None:
    root = cfg.product_image_dir.resolve()
    for attachment in repository.attachments_for_turn(user_id, turn_id):
        path = (cfg.product_image_dir / attachment["storage_key"]).resolve()
        if root in path.parents and path.is_file():
            path.unlink()
        repository.mark_attachment_deleted(attachment["id"])


def _tool_summary(trace) -> dict:
    """Expose operational metadata only; never stream tool args or previews."""
    return {"name": getattr(trace, "name", "tool"), "status": getattr(trace, "status", "unknown"), "duration": getattr(trace, "duration", 0), "attempts": getattr(trace, "attempts", 1), "result_chars": getattr(trace, "result_chars", 0)}


def create_app(repository: ProductRepository | None = None) -> FastAPI:
    cfg.validate_service()
    os.environ["LEARNING_AGENT_API"] = "1"
    if not cfg.service_langsmith_tracing:
        # The service defaults to local OTel/Prometheus only.  This prevents a
        # pre-existing CLI LangSmith setting from sending prompts or exposing
        # provider diagnostics during API failures.
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
    configure_logging()
    tracer = configure_tracing(cfg.otel_endpoint)
    repo = repository or ProductRepository()
    auth = Authenticator(repo)
    limiter = SlidingWindowLimiter()
    worker = IndexWorker(repo)
    vision = VisionService()
    chat_slots = threading.BoundedSemaphore(max(1, cfg.max_chat_concurrency))
    vision_slots = threading.BoundedSemaphore(max(1, cfg.max_vision_concurrency))
    @asynccontextmanager
    async def lifespan(_app):
        yield
        repo.close()

    app = FastAPI(title="Learning Agent API", version=cfg.service_version, lifespan=lifespan)
    app.state.repository = repo; app.state.worker = worker; app.state.authenticator = auth; app.state.vision = vision
    origins = [x.strip() for x in cfg.cors_origins.split(",") if x.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"])

    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        async def frontend_index():
            return FileResponse(frontend_dir / "index.html")

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request_id(request); request.state.request_id = rid
        start = __import__("time").perf_counter()
        span_context = tracer.start_as_current_span("http.request") if tracer else None
        span = span_context.__enter__() if span_context else None
        if span:
            span.set_attribute("http.request_id", rid)
            span.set_attribute("http.route", request.url.path)
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            rate = getattr(request.state, "rate_limit", None)
            if rate:
                response.headers["X-RateLimit-Limit"] = str(rate[0]); response.headers["X-RateLimit-Remaining"] = str(rate[1]); response.headers["X-RateLimit-Reset"] = str(rate[2])
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", request.url.path)
            REQUESTS.labels(request.method, route, str(locals().get("status", 500))).inc()
            REQUEST_LATENCY.labels(request.method, route).observe(__import__("time").perf_counter() - start)
            if span_context:
                span_context.__exit__(None, None, None)

    @app.exception_handler(ProblemException)
    async def problem_handler(request: Request, exc: ProblemException):
        return _problem(request, exc)

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException):
        status_code = exc.status_code; detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
        response = _problem(request, ProblemException(status_code, "http_error", detail, retryable=status_code >= 500))
        if exc.headers: response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return _problem(request, ProblemException(422, "validation_error", "请求字段无效"))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):  # noqa: ARG001
        log.error("unhandled request error", extra={"event": "unhandled_error", "error_code": "internal_error"})
        return _problem(request, ProblemException(500, "internal_error", "服务暂时不可用", retryable=True))

    def current_user(request: Request, credentials=Depends(bearer)) -> Principal:
        try:
            principal = auth.authenticate(credentials)
        except HTTPException:
            repo.audit("anonymous", "login", "auth", None, "rejected", request.state.request_id, ip_digest(request))
            raise
        request.state.principal = principal
        return principal

    def limited(request: Request, principal: Principal, kind: str = "api") -> None:
        if kind == "chat": user_limit, ip_limit = cfg.rate_chat_user_per_minute, cfg.rate_chat_ip_per_minute
        else: user_limit, ip_limit = cfg.rate_api_user_per_minute, cfg.rate_api_ip_per_minute
        ok, remaining, retry = limiter.check(f"user:{principal.user_id}:{kind}", user_limit)
        ok_ip, _, retry_ip = limiter.check(f"ip:{ip_digest(request)}:{kind}", ip_limit)
        request.state.rate_limit = (user_limit, remaining, int(__import__("time").time()) + max(retry, retry_ip))
        if not ok or not ok_ip:
            raise ProblemException(429, "rate_limited", f"请求过于频繁，请在 {max(retry, retry_ip)} 秒后重试", retryable=True, title="请求过于频繁")

    def owner_thread(principal: Principal, thread_id: str) -> None:
        try: thread_store().check_owner(principal.user_id, thread_id)
        except ValueError as exc: raise ProblemException(404, "thread_not_found", "会话不存在") from exc

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            repo.connection.execute("SELECT 1").fetchone()
            cfg.product_upload_dir.mkdir(parents=True, exist_ok=True)
            return {"status": "ready", "database": "ok", "storage": "ok"}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, "服务尚未就绪") from exc

    @app.get("/metrics")
    async def metrics(principal: Principal = Depends(current_user)):
        return Response(metrics_payload(), media_type="text/plain; version=0.0.4")

    @app.post("/v1/files", status_code=201)
    async def upload_file(request: Request, file: UploadFile = File(...), display_name: str | None = Form(None), principal: Principal = Depends(current_user)):
        limited(request, principal)
        data = await file.read(cfg.file_max_bytes + 1)
        if len(data) > cfg.file_max_bytes:
            raise ProblemException(413, "file_too_large", "文件不能超过 25 MiB")
        media_type, suffix = _document_type(file.filename or "file", data)
        if repo.storage_usage(principal.user_id) + len(data) > cfg.file_quota_bytes:
            raise ProblemException(413, "storage_quota_exceeded", "已达到文件存储配额")
        file_id = str(uuid4()); relative = f"{_safe_user_dir(principal.user_id)}/{file_id}{suffix}"
        path = (cfg.product_upload_dir / relative).resolve()
        cfg.product_upload_dir.resolve().mkdir(parents=True, exist_ok=True); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            row = repo.insert_file(principal.user_id, _safe_name(display_name or file.filename or "file"), relative, media_type, len(data), hashlib.sha256(data).hexdigest(), file_id=file_id)
        except Exception:
            path.unlink(missing_ok=True); raise
        UPLOAD_BYTES.inc(len(data)); repo.audit(principal.user_id, "upload", "file", file_id, "accepted", request.state.request_id, ip_digest(request))
        return _file_view(row, repo)

    @app.get("/v1/files")
    async def list_files(request: Request, status: str | None = Query(None), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), principal: Principal = Depends(current_user)):
        limited(request, principal); return {"items": [_file_view(row, repo) for row in repo.list_files(principal.user_id, status, limit, offset)], "next_cursor": None}

    @app.get("/v1/files/{file_id}")
    async def get_file(request: Request, file_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal); row = repo.file(principal.user_id, file_id)
        if not row: raise ProblemException(404, "file_not_found", "文件不存在")
        return _file_view(row, repo)

    @app.post("/v1/files/{file_id}/index-jobs")
    async def create_index_job(request: Request, file_id: str, background: BackgroundTasks, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        limited(request, principal)
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "入库请求必须包含 Idempotency-Key")
        try: job, reused = repo.create_index_job(principal.user_id, file_id, idempotency_key[:200])
        except FileNotFoundError as exc: raise ProblemException(404, "file_not_found", "文件不存在") from exc
        except PermissionError as exc:
            if str(exc) == "index_concurrency_limit": raise ProblemException(429, "index_concurrency_limited", "每个用户同时只能执行一个入库任务", retryable=True)
            if str(exc) == "embedding_token_quota": raise ProblemException(403, "embedding_quota_exceeded", "预计 embedding 用量已达到今日配额")
            raise ProblemException(403, "index_quota_exceeded", "已达到今日入库任务配额")
        except ValueError as exc:
            if str(exc) == "idempotency_conflict": raise ProblemException(409, "idempotency_conflict", "幂等键已用于另一请求")
            raise
        if not reused and job["status"] == "queued" and cfg.app_env in {"development", "test"}:
            background.add_task(worker.run_index, job["id"])
        request.state.response_status = 200 if reused else 202
        return JSONResponse({"job_id": job["id"], "file_id": file_id, "status": job["status"], "reused": reused, "created_at": job["created_at"]}, status_code=200 if reused else 202, headers={"X-Request-ID": request.state.request_id})

    @app.get("/v1/jobs/{job_id}")
    async def get_job(request: Request, job_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal); row = repo.job(principal.user_id, job_id)
        if not row: raise ProblemException(404, "job_not_found", "任务不存在")
        return {"id": row["id"], "file_id": row["file_id"], "status": row["status"], "stage": row["stage"], "attempt": row["attempt"], "chunk_count": row["chunk_count"], "embedding_tokens": row["embedding_tokens"], "estimated_cost": row["estimated_cost"], "error": ({"code": row["error_code"], "message": row["error_message"], "error_id": row["error_id"]} if row["error_code"] else None), "created_at": row["created_at"], "finished_at": row["finished_at"]}

    @app.delete("/v1/files/{file_id}")
    async def delete_file_endpoint(request: Request, file_id: str, background: BackgroundTasks, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        limited(request, principal)
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "删除请求必须包含 Idempotency-Key")
        try: job, reused = repo.mark_delete(principal.user_id, file_id, idempotency_key[:200])
        except FileNotFoundError as exc: raise ProblemException(404, "file_not_found", "文件不存在") from exc
        if not reused and job["status"] == "queued" and cfg.app_env in {"development", "test"}:
            background.add_task(worker.run_delete, job["id"])
        repo.audit(principal.user_id, "delete", "file", file_id, "accepted", request.state.request_id, ip_digest(request))
        return JSONResponse({"job_id": job["id"], "file_id": file_id, "status": job["status"], "reused": reused}, status_code=202, headers={"X-Request-ID": request.state.request_id})

    @app.post("/v1/threads")
    async def create_thread(request: Request, body: ThreadCreate, principal: Principal = Depends(current_user)):
        limited(request, principal); name = body.name.strip(); thread_id = thread_store().get_thread(principal.user_id, name)
        return {"id": thread_id, "name": name}

    @app.get("/v1/threads")
    async def list_threads(request: Request, principal: Principal = Depends(current_user)):
        limited(request, principal); return {"items": [{"id": row["thread_id"], "name": row["name"]} for row in thread_store().thread_rows(principal.user_id)]}

    @app.get("/v1/threads/{thread_id}")
    async def get_thread(request: Request, thread_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal); owner_thread(principal, thread_id)
        messages = []
        try:
            from graph.builder import compiled_graph
            snapshot = compiled_graph().get_state({"configurable": {"thread_id": thread_id}})
            for message in snapshot.values.get("messages", []) if snapshot else []:
                role = "assistant" if message.type == "ai" else "user"
                content = message.content if isinstance(message.content, str) else str(message.content)
                messages.append({"role": role, "content": content})
        except Exception:
            pass
        row = next((r for r in thread_store().thread_rows(principal.user_id) if r["thread_id"] == thread_id), None)
        return {"id": thread_id, "name": row["name"] if row else "", "messages": messages}

    @app.patch("/v1/threads/{thread_id}")
    async def rename_thread(request: Request, thread_id: str, body: ThreadRename, principal: Principal = Depends(current_user)):
        limited(request, principal)
        owner_thread(principal, thread_id)
        name = body.name.strip()
        if not name:
            raise ProblemException(400, "invalid_thread_name", "会话名称不能为空")
        try:
            name = thread_store().rename_thread(principal.user_id, thread_id, name)
        except ValueError as exc:
            if "同名" in str(exc):
                raise ProblemException(409, "thread_name_conflict", "已经存在同名会话") from exc
            raise ProblemException(404, "thread_not_found", "会话不存在") from exc
        repo.audit(principal.user_id, "rename", "thread", thread_id, "succeeded", request.state.request_id, ip_digest(request))
        return {"id": thread_id, "name": name}

    @app.delete("/v1/threads/{thread_id}")
    async def delete_thread(request: Request, thread_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        limited(request, principal)
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "删除请求必须包含 Idempotency-Key")
        key = idempotency_key[:200]
        existing = repo.idempotent_result(principal.user_id, "delete_thread", thread_id, key)
        if existing: return existing
        owner_thread(principal, thread_id)
        result = delete_user_data(learning_store(), thread_store(), principal.user_id, "thread", thread_id)
        repo.audit(principal.user_id, "delete", "thread", thread_id, "succeeded", request.state.request_id, ip_digest(request))
        response = {"status": "deleted", "thread_id": thread_id, "result": result}
        repo.save_idempotent_result(principal.user_id, "delete_thread", thread_id, key, response)
        return response

    def chat_events(principal: Principal, turn: dict, message: str, *, images_info: list, image_context: dict | None, request_id_value: str, release_slot: bool = True):
        ACTIVE_SSE.inc()
        try:
            yield "turn.started", {"turn_id": turn["id"], "thread_id": turn["thread_id"], "request_id": request_id_value}
            if images_info:
                yield "node.progress", {"node": "vision", "label": "正在识别图片题目"}
                if not vision_slots.acquire(blocking=False):
                    repo.update_turn(principal.user_id, turn["id"], status="failed")
                    yield "error", {"code": "concurrency_limited", "message": "图片识别并发已达上限，请稍后重试", "retryable": True, "request_id": request_id_value}; return
                try:
                    parsed: VisionResult = app.state.vision.parse(images_info)
                    VISION_CALLS.labels("ok").inc()
                except Exception:
                    VISION_CALLS.labels("error").inc(); repo.update_turn(principal.user_id, turn["id"], status="failed")
                    _cleanup_turn_images(repo, principal.user_id, turn["id"])
                    yield "error", {"code": "provider_unavailable", "message": "图片识别暂时不可用，请稍后重试", "retryable": True, "request_id": request_id_value}; return
                finally:
                    vision_slots.release()
                context = parsed.context
                repo.update_turn(principal.user_id, turn["id"], vision_parse_version="vision_parse_v1", image_context_json=json.dumps(context, ensure_ascii=False), recognized_text=parsed.recognized_text)
                if parsed.requires_confirmation:
                    repo.update_turn(principal.user_id, turn["id"], status="awaiting_confirmation")
                    yield "confirmation.required", {"turn_id": turn["id"], "kind": "vision", "recognized_text": parsed.recognized_text, "formulas": parsed.formulas, "uncertain_regions": parsed.uncertain_regions}; yield "turn.completed", {"turn_id": turn["id"], "status": "awaiting_confirmation"}; return
                image_context = context
            effective_message = message
            if image_context:
                effective_message += "\n\n图片识别内容（仅作为题目输入，已经过用户确认）：\n" + context_text(image_context)
            yield "node.progress", {"node": "planner", "label": "正在分析问题"}
            try:
                events_queue: queue.Queue = queue.Queue()
                sentinel = object()
                def graph_runner():
                    try:
                        for graph_event in run_stream(effective_message, user_id=principal.user_id, thread_id=turn["thread_id"], active_versions=repo.active_versions(principal.user_id), image_context=image_context or {}):
                            events_queue.put(graph_event)
                    except Exception as graph_exc:  # noqa: BLE001
                        events_queue.put(graph_exc)
                    finally:
                        events_queue.put(sentinel)
                threading.Thread(target=graph_runner, daemon=True).start()
                while True:
                    try:
                        event = events_queue.get(timeout=15)
                    except queue.Empty:
                        yield "heartbeat", {"turn_id": turn["id"]}
                        continue
                    if event is sentinel:
                        break
                    if isinstance(event, Exception):
                        raise event
                    if event.get("type") == "node":
                        yield "node.progress", {"node": event.get("name"), "label": event.get("label")}
                    elif event.get("type") == "done":
                        result = event["result"]
                        answer = result.answer; citations = result.citations or []
                        tool_summaries = [_tool_summary(t) for t in result.traces]
                        repo.update_turn(principal.user_id, turn["id"], status="completed", answer=answer, citations_json=json.dumps(citations, ensure_ascii=False), tool_calls_json=json.dumps(tool_summaries, ensure_ascii=False, default=str))
                        try: MemoryService(learning_store()).commit_turn(principal.user_id, turn["thread_id"], turn["id"], message, answer, request_id_value)
                        except Exception: log.error("memory commit failed", extra={"event": "memory_commit_failed", "object_id": turn["id"]})
                        RETRIEVER_CALLS.labels("with_citations" if citations else "no_evidence").inc()
                        _cleanup_turn_images(repo, principal.user_id, turn["id"])
                        yield "answer.completed", {"answer": answer, "citations": citations, "tool_calls": tool_summaries}
                        yield "turn.completed", {"turn_id": turn["id"], "status": "completed", "usage": {}}
            except Exception:
                log.error("chat failed", extra={"event": "chat_failed", "object_id": turn["id"]})
                repo.update_turn(principal.user_id, turn["id"], status="failed")
                _cleanup_turn_images(repo, principal.user_id, turn["id"])
                yield "error", {"code": "provider_unavailable", "message": "回答服务暂时不可用，请稍后重试", "retryable": True, "request_id": request_id_value}
        finally:
            ACTIVE_SSE.dec()
            if release_slot:
                chat_slots.release()

    async def build_chat(request: Request, payload: str, images: list[UploadFile], idempotency_key: str | None, principal: Principal):
        limited(request, principal, "chat")
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "对话请求必须包含 Idempotency-Key")
        try: raw = json.loads(payload); chat = ChatPayload.model_validate(raw)
        except Exception as exc: raise ProblemException(400, "invalid_payload", "payload 必须是有效 JSON 且包含 message") from exc
        if "user_id" in raw: raise ProblemException(400, "user_id_forbidden", "用户身份来自 Bearer 凭证")
        thread_id = chat.thread_id
        if thread_id: owner_thread(principal, thread_id)
        else: thread_id = thread_store().get_thread(principal.user_id, f"web-{uuid4().hex[:10]}")
        try:
            turn, reused = repo.create_turn(principal.user_id, thread_id, idempotency_key[:200], chat.message)
        except ValueError as exc:
            if str(exc) == "idempotency_conflict": raise ProblemException(409, "idempotency_conflict", "幂等键已用于另一轮对话")
            raise
        if reused:
            if turn["status"] == "completed":
                events = [("turn.started", {"turn_id": turn["id"], "thread_id": thread_id, "request_id": request.state.request_id}), ("answer.completed", {"answer": turn["answer"] or "", "citations": json.loads(turn["citations_json"] or "[]"), "tool_calls": json.loads(turn["tool_calls_json"] or "[]")}), ("turn.completed", {"turn_id": turn["id"], "status": "completed"})]
                return StreamingResponse(_sse(iter(events)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request.state.request_id})
            if turn["status"] == "awaiting_confirmation":
                context = json.loads(turn["image_context_json"] or "{}")
                events = [("turn.started", {"turn_id": turn["id"], "thread_id": thread_id, "request_id": request.state.request_id}), ("confirmation.required", {"turn_id": turn["id"], "kind": "vision", "recognized_text": context.get("recognized_text", ""), "formulas": context.get("formulas", []), "uncertain_regions": context.get("uncertain_regions", [])})]
                return StreamingResponse(_sse(iter(events)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request.state.request_id})
            if turn["status"] in {"failed", "cancelled"}:
                events = [("turn.started", {"turn_id": turn["id"], "thread_id": thread_id, "request_id": request.state.request_id}), ("error", {"code": "turn_" + turn["status"], "message": "该轮次已结束，请新建一轮重试", "retryable": turn["status"] == "failed", "request_id": request.state.request_id}), ("turn.completed", {"turn_id": turn["id"], "status": turn["status"]})]
                return StreamingResponse(_sse(iter(events)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request.state.request_id})
            raise ProblemException(409, "turn_in_progress", "该幂等请求正在处理中，请稍后读取会话状态", retryable=True)
        infos = []
        if len(images) > cfg.image_max_count:
            repo.update_turn(principal.user_id, turn["id"], status="failed"); raise ProblemException(413, "image_count_exceeded", "一轮最多上传 4 张图片")
        turn_dir = (cfg.product_image_dir / _safe_user_dir(principal.user_id) / turn["id"]).resolve(); turn_dir.mkdir(parents=True, exist_ok=True)
        for upload in images:
            data = await upload.read(cfg.image_max_bytes + 1)
            if len(data) > cfg.image_max_bytes:
                repo.update_turn(principal.user_id, turn["id"], status="failed"); _cleanup_turn_images(repo, principal.user_id, turn["id"]); raise ProblemException(413, "image_too_large", "图片不能超过 10 MiB")
            media = upload.content_type or ""
            try: info = preprocess_image(data, media)
            except ValueError as exc:
                repo.update_turn(principal.user_id, turn["id"], status="failed"); _cleanup_turn_images(repo, principal.user_id, turn["id"]); raise ProblemException(415, str(exc), "图片格式、签名或尺寸不符合要求") from exc
            key = f"{_safe_user_dir(principal.user_id)}/{turn['id']}/{uuid4().hex}.png"; path = (cfg.product_image_dir / key).resolve(); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(info.data)
            expires = (datetime.now(timezone.utc) + timedelta(hours=cfg.image_ttl_hours)).isoformat().replace("+00:00", "Z")
            repo.add_attachment(principal.user_id, turn["id"], key, info.media_type, len(info.data), info.width, info.height, expires); infos.append(info)
        if not chat_slots.acquire(blocking=False):
            repo.update_turn(principal.user_id, turn["id"], status="failed")
            _cleanup_turn_images(repo, principal.user_id, turn["id"])
            raise ProblemException(429, "concurrency_limited", "当前对话并发已达上限，请稍后重试", retryable=True, title="并发已达上限")
        return StreamingResponse(_sse(chat_events(principal, turn, chat.message, images_info=infos, image_context=None, request_id_value=request.state.request_id)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-ID": request.state.request_id})

    @app.post("/v1/chat/stream")
    async def chat_stream(request: Request, payload: str = Form(...), images: list[UploadFile] = File(default=[]), idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        return await build_chat(request, payload, images, idempotency_key, principal)

    @app.post("/v1/turns/{turn_id}/vision-confirmation")
    async def vision_confirmation(request: Request, turn_id: str, body: VisionConfirmation, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        limited(request, principal, "chat")
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "确认请求必须包含 Idempotency-Key")
        turn = repo.turn(principal.user_id, turn_id)
        if not turn: raise ProblemException(404, "turn_not_found", "轮次不存在")
        if turn["status"] != "awaiting_confirmation": raise ProblemException(409, "turn_not_awaiting_confirmation", "该轮次不在等待确认状态")
        if body.action == "cancel":
            repo.update_turn(principal.user_id, turn_id, status="cancelled", confirmation_at=datetime.now(timezone.utc).isoformat())
            _cleanup_turn_images(repo, principal.user_id, turn_id)
            return StreamingResponse(_sse(iter([("turn.started", {"turn_id": turn_id, "thread_id": turn["thread_id"], "request_id": request.state.request_id}), ("turn.completed", {"turn_id": turn_id, "status": "cancelled"})])), media_type="text/event-stream", headers={"X-Request-ID": request.state.request_id})
        context = json.loads(turn["image_context_json"] or "{}"); context["recognized_text"] = body.recognized_text or context.get("recognized_text", ""); context["formulas"] = body.formulas or context.get("formulas", [])
        repo.update_turn(principal.user_id, turn_id, status="running", image_context_json=json.dumps(context, ensure_ascii=False), recognized_text=context["recognized_text"], confirmation_at=datetime.now(timezone.utc).isoformat())
        if not chat_slots.acquire(blocking=False):
            repo.update_turn(principal.user_id, turn_id, status="awaiting_confirmation")
            raise ProblemException(429, "concurrency_limited", "当前对话并发已达上限，请稍后重试", retryable=True, title="并发已达上限")
        return StreamingResponse(_sse(chat_events(principal, turn, context["recognized_text"], images_info=[], image_context=context, request_id_value=request.state.request_id)), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request.state.request_id})

    @app.get("/v1/memory")
    async def get_memory(request: Request, principal: Principal = Depends(current_user)):
        limited(request, principal); service = MemoryService(learning_store()); plan = service.review_plan(principal.user_id)
        return {"facts": service.list_memory(principal.user_id)["facts"], "candidates": service.list_memory(principal.user_id)["candidates"], "knowledge": service.knowledge(principal.user_id), "review": {"items": [asdict(item) for item in plan["items"]], "backlog": [asdict(item) for item in plan["backlog"]], "budget_minutes": plan["budget_minutes"], "used_minutes": plan["used_minutes"]}}

    @app.get("/v1/memory/export")
    async def export_memory(request: Request, scope: str = Query("all"), principal: Principal = Depends(current_user)):
        limited(request, principal)
        if scope not in {"memory", "threads", "all"}: raise ProblemException(400, "invalid_scope", "导出范围必须是 memory、threads 或 all")
        data = learning_store().export_rows(principal.user_id, scope)
        if scope in {"threads", "all"}: data["thread_registry"] = thread_store().thread_rows(principal.user_id)
        if scope == "all":
            data["files"] = [{"id": row["id"], "display_name": row["display_name"], "media_type": row["media_type"], "size_bytes": row["size_bytes"], "sha256": row["sha256"], "uploaded_at": row["uploaded_at"], "index_status": row["index_status"], "chunk_count": row["chunk_count"]} for row in repo.list_files(principal.user_id, limit=100)]
            user_dir = _safe_user_dir(principal.user_id)
            data["artifacts"] = {
                "flashcards": [path.read_text(encoding="utf-8") for path in (cfg.flashcard_dir / user_dir).glob("*.json")] if (cfg.flashcard_dir / user_dir).exists() else [],
                "traces": [path.read_text(encoding="utf-8") for path in (cfg.trace_dir / user_dir).glob("*.jsonl")] if (cfg.trace_dir / user_dir).exists() else [],
            }
        return {"schema_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "scope": scope, "data": data}

    @app.post("/v1/memory/candidates/{candidate_id}/confirm")
    async def confirm_memory_candidate(request: Request, candidate_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal)
        try:
            fact_id = MemoryService(learning_store()).repository.confirm_candidate(principal.user_id, candidate_id)
        except ValueError as exc:
            raise ProblemException(404, "candidate_not_found", "记忆候选不存在或已处理") from exc
        repo.audit(principal.user_id, "confirm", "memory_candidate", candidate_id, "succeeded", request.state.request_id, ip_digest(request))
        return {"candidate_id": candidate_id, "fact_id": fact_id, "status": "confirmed"}

    @app.post("/v1/memory/candidates/{candidate_id}/reject")
    async def reject_memory_candidate(request: Request, candidate_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal)
        try:
            MemoryService(learning_store()).repository.reject_candidate(principal.user_id, candidate_id)
        except ValueError as exc:
            raise ProblemException(404, "candidate_not_found", "记忆候选不存在或已处理") from exc
        repo.audit(principal.user_id, "reject", "memory_candidate", candidate_id, "succeeded", request.state.request_id, ip_digest(request))
        return {"candidate_id": candidate_id, "status": "rejected"}

    @app.delete("/v1/memory/facts/{fact_id}")
    async def delete_memory_fact(request: Request, fact_id: str, principal: Principal = Depends(current_user)):
        limited(request, principal)
        try:
            result = delete_fact_with_context(learning_store(), thread_store(), principal.user_id, fact_id)
        except ValueError as exc:
            raise ProblemException(404, "memory_not_found", "记忆不存在") from exc
        repo.audit(principal.user_id, "delete", "memory_fact", fact_id, "succeeded", request.state.request_id, ip_digest(request))
        return {"status": "deleted", "result": result}

    @app.delete("/v1/memory")
    async def delete_memory(request: Request, scope: str = Query("memory"), thread_id: str | None = Query(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal: Principal = Depends(current_user)):
        limited(request, principal)
        if not idempotency_key: raise ProblemException(400, "idempotency_key_required", "删除请求必须包含 Idempotency-Key")
        if scope not in {"memory", "thread", "all"}: raise ProblemException(400, "invalid_scope", "删除范围必须是 memory、thread 或 all")
        object_id = thread_id or scope; key = idempotency_key[:200]
        existing = repo.idempotent_result(principal.user_id, "delete_memory", object_id, key)
        if existing: return existing
        if scope == "thread" and thread_id: owner_thread(principal, thread_id)
        result = delete_user_data(learning_store(), thread_store(), principal.user_id, scope, thread_id)
        if scope == "all":
            from rag.store import delete_file as delete_vectors
            for row in repo.list_files(principal.user_id, limit=100):
                delete_vectors(row["id"], principal.user_id)
                path = (cfg.product_upload_dir / row["storage_key"]).resolve()
                if cfg.product_upload_dir.resolve() in path.parents and path.is_file(): path.unlink()
            result["files"] = repo.delete_user(principal.user_id)
        repo.audit(principal.user_id, "delete", "memory", thread_id, "succeeded", request.state.request_id, ip_digest(request))
        response = {"status": "deleted", "scope": scope, "result": result}; repo.save_idempotent_result(principal.user_id, "delete_memory", object_id, key, response)
        return response

    return app


app = create_app()
