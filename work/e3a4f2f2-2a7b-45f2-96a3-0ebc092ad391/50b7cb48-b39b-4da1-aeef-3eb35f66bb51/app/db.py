from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


CAPACITY = 100
MAX_ATTEMPTS = 3
BUSY_BACKOFF_SECONDS = 0.05
BUSY_TIMEOUT_MS = 50


def _python_strip_codepoints() -> tuple[int, ...]:
    return tuple(codepoint for codepoint in range(0x110000) if chr(codepoint).isspace())


STRIP_CODEPOINTS = _python_strip_codepoints()
STRIP_CHAR_SQL = "char(" + ",".join(str(codepoint) for codepoint in STRIP_CODEPOINTS) + ")"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def database_path() -> Path:
    configured = os.environ.get("REGISTRATION_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return project_root() / "data" / "registrations.db"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        database_path(),
        timeout=BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return connection


def initialize() -> None:
    database_path().parent.mkdir(parents=True, exist_ok=True)
    connection = connect()
    try:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS registrations (
                student_id TEXT NOT NULL UNIQUE
                    CHECK (
                        typeof(student_id) = 'text'
                        AND length(student_id) > 0
                        AND student_id = trim(student_id, {STRIP_CHAR_SQL})
                    ),
                name TEXT NOT NULL
                    CHECK (
                        typeof(name) = 'text'
                        AND length(name) > 0
                        AND trim(name, {STRIP_CHAR_SQL}) <> ''
                    ),
                created_at TEXT NOT NULL
            )
            """
        )
    finally:
        connection.close()


@dataclass(frozen=True)
class RegistrationOutcome:
    result: str
    remaining: int | None
    student_id: str
    name: str | None = None


def _is_busy(error: sqlite3.OperationalError) -> bool:
    error_code = getattr(error, "sqlite_errorcode", None)
    if error_code is not None and (error_code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _count(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS total FROM registrations").fetchone()
    return int(row["total"])


def _remaining(connection: sqlite3.Connection) -> int:
    return max(0, CAPACITY - _count(connection))


def _row(connection: sqlite3.Connection, student_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT student_id, name, created_at FROM registrations WHERE student_id = ?",
        (student_id,),
    ).fetchone()


def _duplicate_outcome(connection: sqlite3.Connection, student_id: str) -> RegistrationOutcome:
    existing = _row(connection, student_id)
    if existing is None:
        return RegistrationOutcome("storage_error", None, student_id)
    return RegistrationOutcome(
        "already_registered",
        _remaining(connection),
        student_id,
        existing["name"],
    )


def _register_once(student_id: str, name: str) -> RegistrationOutcome:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if _row(connection, student_id) is not None:
            outcome = _duplicate_outcome(connection, student_id)
            connection.rollback()
            return outcome

        current_count = _count(connection)
        if current_count >= CAPACITY:
            connection.rollback()
            return RegistrationOutcome("full", 0, student_id)

        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        connection.execute(
            "INSERT INTO registrations (student_id, name, created_at) VALUES (?, ?, ?)",
            (student_id, name, created_at),
        )
        connection.commit()
        return RegistrationOutcome(
            "registered",
            max(0, CAPACITY - current_count - 1),
            student_id,
            name,
        )
    except sqlite3.IntegrityError:
        if connection.in_transaction:
            connection.rollback()
        outcome = _duplicate_outcome(connection, student_id)
        return outcome
    except sqlite3.OperationalError as error:
        if connection.in_transaction:
            connection.rollback()
        if _is_busy(error):
            raise
        return RegistrationOutcome("storage_error", None, student_id)
    finally:
        connection.close()


def register(student_id: str, name: str) -> RegistrationOutcome:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _register_once(student_id, name)
        except sqlite3.OperationalError as error:
            if not _is_busy(error):
                return RegistrationOutcome("storage_error", None, student_id)
            if attempt < MAX_ATTEMPTS:
                time.sleep(BUSY_BACKOFF_SECONDS)
    return RegistrationOutcome("busy", None, student_id)


def status(student_id: str) -> RegistrationOutcome:
    connection = connect()
    try:
        existing = _row(connection, student_id)
        if existing is None:
            return RegistrationOutcome(
                "not_registered",
                _remaining(connection),
                student_id,
            )
        return RegistrationOutcome(
            "already_registered",
            _remaining(connection),
            student_id,
            existing["name"],
        )
    finally:
        connection.close()
