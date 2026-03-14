"""Session management with SQLite persistence for test results."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..core.enums import TestCategory
from ..core.models import SessionInfo, TestCase, TestResult


class SessionStore:
    """SQLite-backed storage for test sessions and results."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                target_tool TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                total_planned INTEGER DEFAULT 0,
                total_executed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS test_cases (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                category TEXT NOT NULL,
                prompt TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                expected_behaviors_json TEXT DEFAULT '[]',
                failure_indicators_json TEXT DEFAULT '[]',
                tags_json TEXT DEFAULT '[]',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                test_case_id TEXT NOT NULL,
                target_tool TEXT NOT NULL,
                category TEXT NOT NULL,
                prompt_sent TEXT NOT NULL,
                response_raw TEXT,
                response_time_ms REAL DEFAULT 0,
                error TEXT,
                screenshot_path TEXT,
                timestamp TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_results_session ON test_results(session_id);
            CREATE INDEX IF NOT EXISTS idx_results_category ON test_results(category);
            CREATE INDEX IF NOT EXISTS idx_cases_session ON test_cases(session_id);
        """)

    def create_session(self, session_id: str, target_tool: str, total_planned: int) -> SessionInfo:
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO sessions (session_id, target_tool, started_at, total_planned, status) "
            "VALUES (?, ?, ?, ?, 'running')",
            (session_id, target_tool, now, total_planned),
        )
        self._conn.commit()
        return SessionInfo(
            session_id=session_id,
            target_tool=target_tool,
            started_at=datetime.fromisoformat(now),
            total_planned=total_planned,
            db_path=str(self._db_path),
        )

    def store_test_cases(self, session_id: str, cases: list[TestCase]) -> None:
        rows = [
            (
                case.id,
                session_id,
                case.category.value,
                case.prompt,
                json.dumps(case.metadata, ensure_ascii=False),
                json.dumps(case.expected_behaviors, ensure_ascii=False),
                json.dumps(case.failure_indicators, ensure_ascii=False),
                json.dumps(case.tags, ensure_ascii=False),
            )
            for case in cases
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO test_cases "
            "(id, session_id, category, prompt, metadata_json, "
            "expected_behaviors_json, failure_indicators_json, tags_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def store_result(self, session_id: str, result: TestResult) -> None:
        self._conn.execute(
            "INSERT INTO test_results "
            "(session_id, test_case_id, target_tool, category, prompt_sent, "
            "response_raw, response_time_ms, error, screenshot_path, timestamp, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                result.test_case_id,
                result.target_tool,
                result.category.value,
                result.prompt_sent,
                result.response_raw,
                result.response_time_ms,
                result.error,
                result.screenshot_path,
                result.timestamp.isoformat(),
                json.dumps(result.metadata, ensure_ascii=False),
            ),
        )
        # Update session progress
        self._conn.execute(
            "UPDATE sessions SET total_executed = total_executed + 1 WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def complete_session(self, session_id: str) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET completed_at = ?, status = 'completed' WHERE session_id = ?",
            (now, session_id),
        )
        self._conn.commit()

    def fail_session(self, session_id: str, error: str) -> None:
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET completed_at = ?, status = ? WHERE session_id = ?",
            (now, f"failed: {error}", session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> SessionInfo | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return SessionInfo(
            session_id=row["session_id"],
            target_tool=row["target_tool"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            total_planned=row["total_planned"],
            total_executed=row["total_executed"],
            status=row["status"],
            db_path=str(self._db_path),
        )

    def list_sessions(self) -> list[SessionInfo]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC"
        ).fetchall()
        return [
            SessionInfo(
                session_id=row["session_id"],
                target_tool=row["target_tool"],
                started_at=datetime.fromisoformat(row["started_at"]),
                completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                total_planned=row["total_planned"],
                total_executed=row["total_executed"],
                status=row["status"],
                db_path=str(self._db_path),
            )
            for row in rows
        ]

    def get_results(self, session_id: str) -> list[TestResult]:
        rows = self._conn.execute(
            "SELECT * FROM test_results WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [
            TestResult(
                test_case_id=row["test_case_id"],
                target_tool=row["target_tool"],
                category=TestCategory(row["category"]),
                prompt_sent=row["prompt_sent"],
                response_raw=row["response_raw"],
                response_time_ms=row["response_time_ms"],
                error=row["error"],
                screenshot_path=row["screenshot_path"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            )
            for row in rows
        ]

    def get_test_cases(self, session_id: str) -> list[TestCase]:
        rows = self._conn.execute(
            "SELECT * FROM test_cases WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            TestCase(
                id=row["id"],
                category=TestCategory(row["category"]),
                prompt=row["prompt"],
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                expected_behaviors=json.loads(row["expected_behaviors_json"]) if row["expected_behaviors_json"] else [],
                failure_indicators=json.loads(row["failure_indicators_json"]) if row["failure_indicators_json"] else [],
                tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            )
            for row in rows
        ]

    def get_executed_case_ids(self, session_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT test_case_id FROM test_results WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {row["test_case_id"] for row in rows}

    def close(self) -> None:
        self._conn.close()
