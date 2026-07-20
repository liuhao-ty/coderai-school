from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import TeacherAuditLog, TeacherSession, now


def record_teacher_audit(
    db: Session,
    teacher: TeacherSession | None,
    action: str,
    *,
    target_type: str = "",
    target_id: str | int = "",
    summary: str = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> TeacherAuditLog:
    actor = teacher.user if teacher and teacher.user_id else None
    log = TeacherAuditLog(
        session_id=teacher.id if teacher else None,
        actor_user_id=actor.id if actor else None,
        actor_username=actor.username if actor else "",
        actor_name=actor.name if actor else "",
        action=action[:80],
        target_type=target_type[:60],
        target_id=str(target_id)[:120],
        summary=summary[:300],
        details_json=json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
    )
    db.add(log)
    if commit:
        db.commit()
        db.refresh(log)
    else:
        db.flush()
    return log


def teacher_audit_payload(log: TeacherAuditLog) -> dict[str, Any]:
    try:
        details = json.loads(log.details_json or "{}")
    except json.JSONDecodeError:
        details = {}
    return {
        "id": log.id,
        "session_id": log.session_id,
        "actor_user_id": log.actor_user_id,
        "actor_username": log.actor_username,
        "actor_name": log.actor_name,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "summary": log.summary,
        "details": details,
        "created_at": log.created_at.isoformat(),
    }


def record_teacher_audit_to_sqlite(
    db_path: Path,
    teacher_session_id: int | None,
    action: str,
    *,
    target_type: str = "",
    target_id: str | int = "",
    summary: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    with closing(sqlite3.connect(str(db_path))) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS teacher_audit_logs (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                actor_user_id INTEGER,
                actor_username VARCHAR(80) DEFAULT '' NOT NULL,
                actor_name VARCHAR(120) DEFAULT '' NOT NULL,
                action VARCHAR(80) NOT NULL,
                target_type VARCHAR(60) DEFAULT '',
                target_id VARCHAR(120) DEFAULT '',
                summary VARCHAR(300) DEFAULT '',
                details_json TEXT DEFAULT '{}',
                created_at DATETIME,
                FOREIGN KEY(session_id) REFERENCES teacher_sessions(id)
            )
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(teacher_audit_logs)").fetchall()}
        if "actor_user_id" not in columns:
            connection.execute("ALTER TABLE teacher_audit_logs ADD COLUMN actor_user_id INTEGER")
        if "actor_username" not in columns:
            connection.execute("ALTER TABLE teacher_audit_logs ADD COLUMN actor_username VARCHAR(80) DEFAULT '' NOT NULL")
        if "actor_name" not in columns:
            connection.execute("ALTER TABLE teacher_audit_logs ADD COLUMN actor_name VARCHAR(120) DEFAULT '' NOT NULL")
        actor_user_id = None
        actor_username = ""
        actor_name = ""
        if teacher_session_id is not None:
            actor = connection.execute(
                """
                SELECT users.id, users.username, users.name
                FROM teacher_sessions
                LEFT JOIN users ON users.id = teacher_sessions.user_id
                WHERE teacher_sessions.id = ?
                """,
                (teacher_session_id,),
            ).fetchone()
            if actor:
                actor_user_id, actor_username, actor_name = actor[0], str(actor[1] or ""), str(actor[2] or "")
        connection.execute(
            """
            INSERT INTO teacher_audit_logs
                (session_id, actor_user_id, actor_username, actor_name, action, target_type, target_id, summary, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                teacher_session_id,
                actor_user_id,
                actor_username,
                actor_name,
                action[:80],
                target_type[:60],
                str(target_id)[:120],
                summary[:300],
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
                now().isoformat(),
            ),
        )
        connection.commit()
