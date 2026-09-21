"""长期学习业务库的连接和 schema migration。"""

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 1


def open_learning_db(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, timezone TEXT NOT NULL, auto_extract INTEGER NOT NULL DEFAULT 1,
            generation INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS turns (
            turn_id TEXT PRIMARY KEY, request_id TEXT UNIQUE, user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'committed', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS memory_facts (
            fact_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, fact_key TEXT NOT NULL,
            value TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, source_turn_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, kind, fact_key, version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS active_fact_key ON memory_facts(user_id, kind, fact_key) WHERE status='active';
        CREATE TABLE IF NOT EXISTS memory_sources (
            source_id TEXT PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES memory_facts(fact_id) ON DELETE CASCADE,
            turn_id TEXT, message_id TEXT, evidence_quote TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS memory_candidates (
            candidate_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, fact_key TEXT NOT NULL,
            value TEXT NOT NULL, confidence REAL NOT NULL, evidence_quote TEXT NOT NULL, source_turn_id TEXT,
            fingerprint TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS knowledge_points (
            knowledge_point_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, subject TEXT NOT NULL, name TEXT NOT NULL,
            normalized_name TEXT NOT NULL, learning_status TEXT NOT NULL DEFAULT 'unseen',
            mastery_status TEXT NOT NULL DEFAULT 'unknown', self_rating INTEGER, schedule_stage INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, subject, normalized_name)
        );
        CREATE TABLE IF NOT EXISTS learning_events (
            event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(knowledge_point_id) ON DELETE CASCADE,
            turn_id TEXT, event_type TEXT NOT NULL, payload TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1,
            reliability REAL NOT NULL DEFAULT 1.0, occurred_at TEXT NOT NULL, revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS review_tasks (
            task_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(knowledge_point_id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', stage INTEGER NOT NULL DEFAULT 0, due_at TEXT NOT NULL,
            original_due_at TEXT NOT NULL, algorithm_version TEXT NOT NULL DEFAULT 'interval_v1', version INTEGER NOT NULL DEFAULT 1,
            postponed_from TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_pending_review ON review_tasks(user_id, knowledge_point_id) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS review_feedback (
            feedback_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES review_tasks(task_id) ON DELETE CASCADE,
            request_id TEXT NOT NULL UNIQUE, rating TEXT NOT NULL, completed_at TEXT NOT NULL, next_task_id TEXT
        );
        CREATE TABLE IF NOT EXISTS extraction_jobs (
            job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, turn_id TEXT NOT NULL, extractor_version TEXT NOT NULL,
            generation INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(turn_id, extractor_version)
        );
        CREATE TABLE IF NOT EXISTS deletion_jobs (
            job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, scope TEXT NOT NULL, target TEXT,
            status TEXT NOT NULL DEFAULT 'pending', preview_json TEXT NOT NULL, result_json TEXT,
            error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn
