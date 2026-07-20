from pathlib import Path
import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import zipfile
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("CODERAI_DATA_DIR", ROOT_DIR / "workspace_data")).resolve()
DB_PATH = DATA_DIR / "coderai.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from backend.app import models
    from backend.app.auth import ensure_auth_settings
    from backend.app.privacy import cleanup_stale_privacy_artifacts, ensure_default_privacy_policy
    from backend.app.secrets import migrate_provider_secrets
    from backend.app.services import ensure_default_provider_routes

    backup_database_before_school_stage_migration()
    backup_database_before_course_package_author_migration()
    backup_database_before_course_package_teacher_migration()
    backup_database_before_curriculum_migration()
    backup_database_before_account_migration()
    Base.metadata.create_all(bind=engine)
    migrate_sqlite_schema()
    db = SessionLocal()
    try:
        ensure_auth_settings(db)
        backfill_teacher_ownership(db)
        migrate_global_student_accounts_and_classroom_teachers(db)
        migrate_legacy_curriculum(db)
        migrate_course_package_authors(db)
        migrate_school_stages(db)
        migrate_provider_secrets(db)
        ensure_default_provider_routes(db)
        ensure_default_privacy_policy(db)
    finally:
        db.close()
    cleanup_stale_privacy_artifacts()
    seed_default_classroom_and_student()
    backfill_asset_metadata()


def migrate_sqlite_schema():
    with engine.begin() as conn:
        ensure_column(conn, "users", "username", "VARCHAR(80) DEFAULT ''")
        ensure_column(conn, "users", "password_hash", "TEXT DEFAULT ''")
        ensure_column(conn, "users", "password_change_required", "BOOLEAN DEFAULT 0")
        ensure_column(conn, "users", "credential_version", "INTEGER DEFAULT 1")
        ensure_column(conn, "users", "registered_at", "DATETIME")
        ensure_column(conn, "users", "last_login_at", "DATETIME")
        ensure_column(conn, "users", "created_by_user_id", "INTEGER")
        ensure_column(conn, "users", "classroom_id", "INTEGER")
        ensure_column(conn, "users", "access_code", "VARCHAR(80) DEFAULT ''")
        ensure_column(conn, "users", "age_level", "VARCHAR(30) DEFAULT 'primary_lower'")
        ensure_column(conn, "users", "active", "BOOLEAN DEFAULT 1")
        ensure_column(conn, "users", "archived_at", "DATETIME")
        ensure_column(conn, "users", "archived_classroom_name", "VARCHAR(160) DEFAULT ''")
        ensure_column(conn, "classrooms", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "classrooms", "grade_level", "VARCHAR(40) DEFAULT 'mixed'")
        ensure_column(conn, "courses", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "lessons", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "tasks", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "tasks", "course_schedule_id", "INTEGER")
        ensure_column(conn, "tasks", "target_student_id", "INTEGER")
        ensure_column(conn, "tasks", "task_kind", "VARCHAR(20) DEFAULT 'legacy'")
        ensure_column(conn, "projects", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "task_submissions", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "feedback_templates", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "assets", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "workflows", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "workflow_runs", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "video_tasks", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "usage_logs", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "moderation_logs", "owner_teacher_id", "INTEGER")
        ensure_column(conn, "projects", "user_id", "INTEGER")
        ensure_column(conn, "projects", "classroom_id", "INTEGER")
        ensure_column(conn, "projects", "lifecycle_status", "VARCHAR(20) DEFAULT 'active'")
        ensure_column(conn, "projects", "moderation_status", "VARCHAR(20) DEFAULT 'approved'")
        ensure_column(conn, "projects", "moderation_reason", "TEXT DEFAULT ''")
        ensure_column(conn, "projects", "moderation_log_id", "INTEGER")
        ensure_column(conn, "projects", "archived_at", "DATETIME")
        ensure_column(conn, "projects", "trashed_at", "DATETIME")
        ensure_column(conn, "workflow_runs", "user_id", "INTEGER")
        ensure_column(conn, "workflow_runs", "classroom_id", "INTEGER")
        ensure_column(conn, "workflow_runs", "node_states_json", "TEXT DEFAULT '{}'")
        ensure_column(conn, "workflow_runs", "cancel_requested", "BOOLEAN DEFAULT 0")
        ensure_column(conn, "workflow_runs", "updated_at", "DATETIME")
        ensure_column(conn, "lessons", "created_at", "DATETIME")
        ensure_column(conn, "assets", "classroom_id", "INTEGER")
        ensure_column(conn, "assets", "lesson_id", "INTEGER")
        ensure_column(conn, "courses", "classroom_id", "INTEGER")
        ensure_column(conn, "moderation_logs", "content_stage", "VARCHAR(20) DEFAULT 'input'")
        ensure_column(conn, "moderation_logs", "resource_type", "VARCHAR(20) DEFAULT 'text'")
        ensure_column(conn, "moderation_logs", "resource_path", "TEXT DEFAULT ''")
        ensure_column(conn, "moderation_logs", "status", "VARCHAR(20) DEFAULT 'approved'")
        ensure_column(conn, "moderation_logs", "project_id", "INTEGER")
        ensure_column(conn, "moderation_logs", "review_note", "TEXT DEFAULT ''")
        ensure_column(conn, "moderation_logs", "reviewed_at", "DATETIME")
        ensure_column(conn, "workflows", "description", "TEXT DEFAULT ''")
        ensure_column(conn, "workflows", "owner_user_id", "INTEGER")
        ensure_column(conn, "workflows", "classroom_id", "INTEGER")
        ensure_column(conn, "workflows", "status", "VARCHAR(20) DEFAULT 'draft'")
        ensure_column(conn, "workflows", "version", "INTEGER DEFAULT 1")
        ensure_column(conn, "workflows", "updated_at", "DATETIME")
        ensure_column(conn, "courses", "status", "VARCHAR(20) DEFAULT 'published'")
        ensure_column(conn, "courses", "starts_at", "DATETIME")
        ensure_column(conn, "courses", "ends_at", "DATETIME")
        ensure_column(conn, "courses", "updated_at", "DATETIME")
        ensure_column(conn, "courses", "package_version", "VARCHAR(40) DEFAULT '1.0.0'")
        ensure_column(conn, "courses", "author", "VARCHAR(120) DEFAULT ''")
        ensure_column(conn, "courses", "age_range", "VARCHAR(80) DEFAULT '全年龄'")
        ensure_column(conn, "courses", "cover_path", "TEXT DEFAULT ''")
        ensure_column(conn, "courses", "dependencies_json", "TEXT DEFAULT '[]'")
        ensure_column(conn, "courses", "checklist_json", "TEXT DEFAULT '[]'")
        ensure_column(conn, "assets", "original_name", "VARCHAR(255) DEFAULT ''")
        ensure_column(conn, "assets", "mime_type", "VARCHAR(120) DEFAULT ''")
        ensure_column(conn, "assets", "file_size", "INTEGER DEFAULT 0")
        ensure_column(conn, "assets", "file_extension", "VARCHAR(20) DEFAULT ''")
        ensure_column(conn, "assets", "checksum_sha256", "VARCHAR(64) DEFAULT ''")
        ensure_column(conn, "assets", "safety_status", "VARCHAR(30) DEFAULT 'verified'")
        ensure_column(conn, "tasks", "status", "VARCHAR(20) DEFAULT 'published'")
        ensure_column(conn, "tasks", "starts_at", "DATETIME")
        ensure_column(conn, "tasks", "due_at", "DATETIME")
        ensure_column(conn, "tasks", "rubric_json", "TEXT DEFAULT '[{\"criterion\":\"完成度\",\"max_score\":100}]'")
        ensure_column(conn, "task_submissions", "version_count", "INTEGER DEFAULT 1")
        ensure_column(conn, "task_submissions", "is_late", "BOOLEAN DEFAULT 0")
        ensure_column(conn, "task_submissions", "is_featured", "BOOLEAN DEFAULT 0")
        ensure_column(conn, "task_submissions", "rubric_snapshot_json", "TEXT DEFAULT '[]'")
        ensure_column(conn, "task_submissions", "max_score_snapshot", "INTEGER DEFAULT 100")
        ensure_column(conn, "usage_logs", "user_id", "INTEGER")
        ensure_column(conn, "usage_logs", "classroom_id", "INTEGER")
        ensure_column(conn, "usage_logs", "provider_id", "INTEGER")
        ensure_column(conn, "usage_logs", "provider_name", "VARCHAR(80) DEFAULT ''")
        ensure_column(conn, "moderation_logs", "user_id", "INTEGER")
        ensure_column(conn, "moderation_logs", "classroom_id", "INTEGER")
        ensure_column(conn, "ai_providers", "last_test_status", "VARCHAR(20) DEFAULT 'untested'")
        ensure_column(conn, "ai_providers", "last_test_message", "TEXT DEFAULT ''")
        ensure_column(conn, "ai_providers", "last_tested_at", "DATETIME")
        ensure_column(conn, "ai_providers", "created_at", "DATETIME")
        ensure_column(conn, "video_tasks", "provider_id", "INTEGER")
        ensure_column(conn, "video_tasks", "retry_count", "INTEGER DEFAULT 0")
        ensure_column(conn, "video_tasks", "timeout_at", "DATETIME")
        ensure_column(conn, "video_tasks", "last_checked_at", "DATETIME")
        ensure_column(conn, "video_tasks", "download_url_expires_at", "DATETIME")
        ensure_column(conn, "teacher_sessions", "user_id", "INTEGER")
        ensure_column(conn, "teacher_audit_logs", "actor_user_id", "INTEGER")
        ensure_column(conn, "teacher_audit_logs", "actor_username", "VARCHAR(80) DEFAULT ''")
        ensure_column(conn, "teacher_audit_logs", "actor_name", "VARCHAR(120) DEFAULT ''")
        ensure_column(conn, "course_packages", "author_user_id", "INTEGER")
        ensure_column(conn, "course_packages", "school_stages_json", "TEXT DEFAULT '[\"primary_lower\",\"primary_upper\",\"secondary\"]'")
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_nocase ON users(username COLLATE NOCASE) WHERE username != ''"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_teacher_sessions_user_id ON teacher_sessions(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_teacher_audit_logs_actor_user_id ON teacher_audit_logs(actor_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_student_sessions_user_id ON student_sessions(user_id)"))
        conn.execute(text("UPDATE users SET username = '' WHERE username IS NULL"))
        conn.execute(text("UPDATE users SET password_hash = '' WHERE password_hash IS NULL"))
        conn.execute(text("UPDATE users SET password_change_required = 0 WHERE password_change_required IS NULL"))
        conn.execute(text("UPDATE users SET credential_version = 1 WHERE credential_version IS NULL OR credential_version < 1"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_logs_user_id ON usage_logs(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_logs_classroom_id ON usage_logs(classroom_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_logs_provider_id ON usage_logs(provider_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_moderation_logs_user_id ON moderation_logs(user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_moderation_logs_classroom_id ON moderation_logs(classroom_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_classroom_teachers_teacher_id ON classroom_teachers(teacher_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_course_package_teachers_teacher_id ON course_package_teachers(teacher_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_course_packages_author_user_id ON course_packages(author_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_video_tasks_provider_id ON video_tasks(provider_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_target_student_id ON tasks(target_student_id)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_course_schedule_id ON tasks(course_schedule_id) WHERE course_schedule_id IS NOT NULL"))
        for table_name in (
            "classrooms", "courses", "lessons", "tasks", "projects", "task_submissions",
            "feedback_templates", "assets", "workflows", "workflow_runs", "video_tasks",
            "usage_logs", "moderation_logs",
        ):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_owner_teacher_id ON {table_name}(owner_teacher_id)"))
        conn.execute(text("UPDATE courses SET updated_at = created_at WHERE updated_at IS NULL"))
        conn.execute(text("UPDATE projects SET lifecycle_status = 'active' WHERE lifecycle_status IS NULL OR lifecycle_status = ''"))
        conn.execute(text("UPDATE projects SET moderation_status = 'approved' WHERE moderation_status IS NULL OR moderation_status = ''"))
        conn.execute(text("UPDATE moderation_logs SET resource_type = 'text' WHERE resource_type IS NULL OR resource_type = ''"))
        conn.execute(text("UPDATE moderation_logs SET status = CASE WHEN passed = 1 THEN 'approved' ELSE 'rejected' END WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE courses SET status = 'published' WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE courses SET package_version = '1.0.0' WHERE package_version IS NULL OR package_version = ''"))
        conn.execute(text("UPDATE courses SET age_range = '全年龄' WHERE age_range IS NULL OR age_range = ''"))
        conn.execute(text("UPDATE tasks SET status = 'published' WHERE status IS NULL OR status = ''"))
        conn.execute(text("UPDATE task_submissions SET version_count = 1 WHERE version_count IS NULL OR version_count < 1"))
        conn.execute(text("UPDATE task_submissions SET is_late = 0 WHERE is_late IS NULL"))
        conn.execute(text("UPDATE task_submissions SET is_featured = 0 WHERE is_featured IS NULL"))
        conn.execute(text("UPDATE task_submissions SET rubric_snapshot_json = '[]' WHERE rubric_snapshot_json IS NULL OR rubric_snapshot_json = ''"))
        conn.execute(text("UPDATE task_submissions SET max_score_snapshot = 100 WHERE max_score_snapshot IS NULL OR max_score_snapshot < 1"))
        conn.execute(text("UPDATE tasks SET task_kind = 'legacy' WHERE task_kind IS NULL OR task_kind = ''"))
        conn.execute(text("UPDATE video_tasks SET retry_count = 0 WHERE retry_count IS NULL"))
        conn.execute(text("UPDATE ai_providers SET last_test_status = 'untested' WHERE last_test_status IS NULL OR last_test_status = ''"))
        conn.execute(text("UPDATE ai_providers SET created_at = updated_at WHERE created_at IS NULL"))
        conn.execute(
            text(
                """
                UPDATE usage_logs
                SET provider_id = (
                    SELECT ai_providers.id
                    FROM ai_providers
                    WHERE usage_logs.model IN (ai_providers.text_model, ai_providers.image_model, ai_providers.video_model)
                    ORDER BY ai_providers.id DESC
                    LIMIT 1
                )
                WHERE provider_id IS NULL AND model IS NOT NULL AND model != ''
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE usage_logs
                SET provider_name = (SELECT ai_providers.name FROM ai_providers WHERE ai_providers.id = usage_logs.provider_id)
                WHERE provider_id IS NOT NULL AND (provider_name IS NULL OR provider_name = '')
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE video_tasks
                SET provider_id = (
                    SELECT ai_providers.id
                    FROM ai_providers
                    WHERE ai_providers.provider_type = video_tasks.provider_type
                    ORDER BY ai_providers.id DESC
                    LIMIT 1
                )
                WHERE provider_id IS NULL AND provider_type IS NOT NULL AND provider_type != ''
                """
            )
        )
        conn.execute(text("UPDATE video_tasks SET timeout_at = datetime(created_at, '+30 minutes') WHERE timeout_at IS NULL"))
        conn.execute(
            text(
                """
                UPDATE moderation_logs
                SET classroom_id = (SELECT projects.classroom_id FROM projects WHERE projects.id = moderation_logs.project_id)
                WHERE classroom_id IS NULL AND project_id IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS submission_versions (
                    id INTEGER PRIMARY KEY,
                    submission_id INTEGER NOT NULL,
                    version_number INTEGER DEFAULT 1,
                    project_id INTEGER NOT NULL,
                    project_title VARCHAR(160) DEFAULT '',
                    project_summary TEXT DEFAULT '',
                    project_file_path TEXT DEFAULT '',
                    is_late BOOLEAN DEFAULT 0,
                    created_at DATETIME,
                    FOREIGN KEY(submission_id) REFERENCES task_submissions(id),
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                )
                """
            )
        )
        conn.execute(text("UPDATE workflows SET updated_at = datetime('now', '+8 hours') WHERE updated_at IS NULL"))
        conn.execute(text("UPDATE workflow_runs SET node_states_json = '{}' WHERE node_states_json IS NULL OR node_states_json = ''"))
        conn.execute(text("UPDATE workflow_runs SET cancel_requested = 0 WHERE cancel_requested IS NULL"))
        conn.execute(text("UPDATE workflow_runs SET updated_at = created_at WHERE updated_at IS NULL"))
        conn.execute(text("UPDATE lessons SET created_at = datetime('now', '+8 hours') WHERE created_at IS NULL"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS task_submissions (
                    id INTEGER PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    project_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    classroom_id INTEGER,
                    status VARCHAR(30) DEFAULT 'submitted',
                    feedback TEXT DEFAULT '',
                    score INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(project_id) REFERENCES projects(id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(classroom_id) REFERENCES classrooms(id)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS video_tasks (
                    id INTEGER PRIMARY KEY,
                    provider_task_id VARCHAR(160) DEFAULT '',
                    provider_type VARCHAR(40) DEFAULT '',
                    model VARCHAR(120) DEFAULT '',
                    prompt TEXT DEFAULT '',
                    source_image_path TEXT DEFAULT '',
                    duration_seconds INTEGER DEFAULT 5,
                    status VARCHAR(40) DEFAULT 'submitted',
                    file_id VARCHAR(160) DEFAULT '',
                    download_url TEXT DEFAULT '',
                    file_path TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    project_id INTEGER,
                    user_id INTEGER,
                    classroom_id INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME,
                    FOREIGN KEY(project_id) REFERENCES projects(id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(classroom_id) REFERENCES classrooms(id)
                )
                """
            )
        )


def ensure_column(conn, table_name: str, column_name: str, column_sql: str):
    columns = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()]
    if column_name not in columns:
        conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def backup_database_before_account_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "migration-backups"
    backup_path = backup_dir / "coderai-before-global-student-classroom-auth.db"
    if backup_path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def backup_database_before_course_package_teacher_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "migration-backups"
    backup_path = backup_dir / "coderai-before-course-package-teachers.db"
    if backup_path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def backup_database_before_course_package_author_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "migration-backups"
    backup_path = backup_dir / "coderai-before-course-package-authors.db"
    if backup_path.exists():
        return
    source = sqlite3.connect(DB_PATH)
    try:
        table_exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'course_packages'"
        ).fetchone()
        if not table_exists:
            return
        columns = {row[1] for row in source.execute("PRAGMA table_info(course_packages)").fetchall()}
        if "author_user_id" in columns:
            return
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def backup_database_before_school_stage_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "migration-backups"
    backup_path = backup_dir / "coderai-before-school-stages.db"
    if backup_path.exists():
        return
    source = sqlite3.connect(DB_PATH)
    try:
        tables = {
            row[0]
            for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "users" not in tables:
            return
        user_columns = {
            row[1] for row in source.execute("PRAGMA table_info(users)").fetchall()
        }
        classroom_columns = {
            row[1] for row in source.execute("PRAGMA table_info(classrooms)").fetchall()
        } if "classrooms" in tables else set()
        package_columns = {
            row[1] for row in source.execute("PRAGMA table_info(course_packages)").fetchall()
        } if "course_packages" in tables else set()
        old_students = True if "age_level" not in user_columns else bool(source.execute(
            "SELECT 1 FROM users WHERE role = 'student' AND (age_level NOT IN ('primary_lower', 'primary_upper', 'secondary') OR age_level IS NULL OR age_level = '') LIMIT 1"
        ).fetchone())
        old_classrooms = False
        if "classrooms" in tables:
            old_classrooms = "grade_level" not in classroom_columns or bool(source.execute(
                "SELECT 1 FROM classrooms WHERE grade_level NOT IN ('primary_lower', 'primary_upper', 'secondary', 'mixed') OR grade_level IS NULL OR grade_level = '' LIMIT 1"
            ).fetchone())
        old_packages = False
        if "course_packages" in tables:
            old_packages = "school_stages_json" not in package_columns or bool(source.execute(
                "SELECT 1 FROM course_packages WHERE school_stages_json IS NULL OR TRIM(school_stages_json) IN ('', '[]') LIMIT 1"
            ).fetchone())
        needs_migration = (
            old_packages
            or old_students
            or old_classrooms
        )
        if not needs_migration:
            return
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def backup_database_before_curriculum_migration() -> None:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size == 0:
        return
    backup_dir = DATA_DIR / "migration-backups"
    backup_path = backup_dir / "coderai-before-curriculum-v2.zip"
    if backup_path.exists():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = backup_dir / ".coderai-before-curriculum-v2.db"
    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(snapshot_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    try:
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path, "coderai.db")
            asset_root = DATA_DIR / "assets"
            if asset_root.is_dir():
                for item in asset_root.rglob("*"):
                    if item.is_file():
                        archive.write(item, (Path("assets") / item.relative_to(asset_root)).as_posix())
    finally:
        snapshot_path.unlink(missing_ok=True)


def migrate_legacy_curriculum(db) -> None:
    from backend.app.models import Asset, Course, CourseMaterial, CoursePackage, CurriculumCourse, Lesson, Task, User
    from backend.app.school_stages import infer_school_stages

    curriculum_root = DATA_DIR / "curriculum"
    curriculum_root.mkdir(parents=True, exist_ok=True)
    admin = db.query(User).filter(User.role == "admin").order_by(User.id.asc()).first()
    for legacy_course in db.query(Course).order_by(Course.id.asc()).all():
        package = db.query(CoursePackage).filter(CoursePackage.legacy_course_id == legacy_course.id).first()
        if not package:
            package = CoursePackage(
                created_by_user_id=admin.id if admin else legacy_course.owner_teacher_id,
                author_user_id=admin.id if admin else legacy_course.owner_teacher_id,
                legacy_course_id=legacy_course.id,
                title=legacy_course.title,
                description=legacy_course.description or "",
                package_version=legacy_course.package_version or "1.0.0",
                author=admin.name if admin else (legacy_course.author or ""),
                age_range=legacy_course.age_range or "全年龄",
                school_stages_json=json.dumps(infer_school_stages(legacy_course.age_range), ensure_ascii=False),
                cover_path=legacy_course.cover_path or "",
                status="draft",
            )
            db.add(package)
            db.flush()
        for legacy_lesson in db.query(Lesson).filter(Lesson.course_id == legacy_course.id).order_by(Lesson.order_index.asc(), Lesson.id.asc()).all():
            course = db.query(CurriculumCourse).filter(CurriculumCourse.legacy_lesson_id == legacy_lesson.id).first()
            if not course:
                template_task = db.query(Task).filter(Task.lesson_id == legacy_lesson.id).order_by(Task.id.asc()).first()
                course = CurriculumCourse(
                    package_id=package.id,
                    legacy_lesson_id=legacy_lesson.id,
                    title=legacy_lesson.title,
                    description="由旧课时迁移，资料可由管理员继续补充。",
                    order_index=legacy_lesson.order_index or 0,
                    assignment_instructions=template_task.instructions if template_task else "",
                    tool_scope=template_task.tool_scope if template_task else "text,image,workflow",
                    rubric_json=template_task.rubric_json if template_task else '[{"criterion":"完成度","max_score":100}]',
                )
                db.add(course)
                db.flush()
            target_dir = curriculum_root / str(package.id) / str(course.id)
            target_dir.mkdir(parents=True, exist_ok=True)
            if (legacy_lesson.content or "").strip() and not db.query(CourseMaterial).filter_by(course_id=course.id, kind="starter_markdown").first():
                target = target_dir / "starter.md"
                target.write_text(legacy_lesson.content, encoding="utf-8")
                content = target.read_bytes()
                db.add(CourseMaterial(
                    course_id=course.id,
                    kind="starter_markdown",
                    original_name="工程包.md",
                    source_path=str(target),
                    mime_type="text/markdown",
                    file_size=len(content),
                    checksum_sha256=hashlib.sha256(content).hexdigest(),
                    conversion_status="ready",
                ))
            for asset in db.query(Asset).filter(Asset.lesson_id == legacy_lesson.id).order_by(Asset.id.asc()).all():
                source = Path(asset.file_path)
                if asset.file_path.startswith(("http://", "https://")) or not source.is_file():
                    continue
                extension = source.suffix.lower()
                name = (asset.original_name or source.name).lower()
                if extension == ".pptx":
                    kind, filename, conversion_status = "slides", "slides.pptx", "pending"
                elif extension == ".md":
                    kind = "result_markdown" if any(token in name for token in ("成果", "result", "完成")) else "starter_markdown"
                    filename, conversion_status = ("result.md" if kind == "result_markdown" else "starter.md"), "ready"
                else:
                    continue
                if db.query(CourseMaterial).filter_by(course_id=course.id, kind=kind).first():
                    continue
                target = target_dir / filename
                shutil.copy2(source, target)
                content = target.read_bytes()
                db.add(CourseMaterial(
                    course_id=course.id,
                    kind=kind,
                    original_name=asset.original_name or source.name,
                    source_path=str(target),
                    mime_type=asset.mime_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                    file_size=len(content),
                    checksum_sha256=hashlib.sha256(content).hexdigest(),
                    conversion_status=conversion_status,
                ))
    db.query(Task).filter(Task.course_schedule_id.is_(None)).update({Task.task_kind: "legacy"}, synchronize_session=False)
    db.commit()


def migrate_course_package_authors(db) -> None:
    from backend.app.models import CoursePackage, User

    fallback = (
        db.query(User)
        .filter(User.role == "admin", User.active.is_(True))
        .order_by(User.id.asc())
        .first()
        or db.query(User).filter(User.role.in_(("teacher", "admin"))).order_by(User.id.asc()).first()
    )
    for package in db.query(CoursePackage).order_by(CoursePackage.id.asc()).all():
        author = db.get(User, package.author_user_id) if package.author_user_id else None
        if not author or author.role not in {"teacher", "admin"}:
            creator = db.get(User, package.created_by_user_id) if package.created_by_user_id else None
            author = creator if creator and creator.role in {"teacher", "admin"} else fallback
            package.author_user_id = author.id if author else None
        if author:
            package.author = author.name
    db.commit()


def migrate_school_stages(db) -> None:
    from backend.app.models import Classroom, CoursePackage, User
    from backend.app.school_stages import (
        normalize_school_stage,
        school_stages_from_json,
        school_stages_label,
    )

    for student in db.query(User).filter(User.role == "student").all():
        student.age_level = normalize_school_stage(student.age_level)
    for classroom in db.query(Classroom).all():
        if classroom.grade_level != "mixed":
            classroom.grade_level = normalize_school_stage(classroom.grade_level, default="mixed")
    for package in db.query(CoursePackage).all():
        stages = school_stages_from_json(package.school_stages_json, package.age_range)
        package.school_stages_json = json.dumps(stages, ensure_ascii=False, separators=(",", ":"))
        package.age_range = school_stages_label(stages)
    db.commit()


def backfill_teacher_ownership(db):
    from backend.app.models import (
        Asset,
        Classroom,
        Course,
        FeedbackTemplate,
        Lesson,
        ModerationLog,
        Project,
        Task,
        TaskSubmission,
        UsageLog,
        User,
        VideoTask,
        Workflow,
        WorkflowRun,
    )

    admin = db.query(User).filter(User.role == "admin").order_by(User.id.asc()).first()
    if not admin:
        return
    for model in (
        Classroom,
        Course,
        Lesson,
        Task,
        Project,
        TaskSubmission,
        FeedbackTemplate,
        Asset,
        Workflow,
        WorkflowRun,
        VideoTask,
        UsageLog,
        ModerationLog,
    ):
        db.query(model).filter(model.owner_teacher_id.is_(None)).update(
            {model.owner_teacher_id: admin.id}, synchronize_session=False
        )
    db.commit()


def migrate_global_student_accounts_and_classroom_teachers(db):
    from backend.app.models import Classroom, ClassroomTeacher, ModerationLog, Project, User

    teacher_owned_classrooms = (
        db.query(Classroom, User)
        .join(User, User.id == Classroom.owner_teacher_id)
        .filter(User.role == "teacher")
        .all()
    )
    for classroom, teacher in teacher_owned_classrooms:
        existing = db.query(ClassroomTeacher).filter_by(classroom_id=classroom.id, teacher_id=teacher.id).first()
        if not existing:
            db.add(ClassroomTeacher(
                classroom_id=classroom.id,
                teacher_id=teacher.id,
                assigned_by_user_id=classroom.owner_teacher_id,
            ))

    admin_ids = {row[0] for row in db.query(User.id).filter(User.role == "admin").all()}
    for student in db.query(User).filter(User.role == "student").all():
        pending = not student.username or not student.password_hash
        if pending and student.archived_at is None:
            student.archived_classroom_name = student.classroom.name if student.classroom else "未分配班级"
            student.archived_at = now_naive()
            student.classroom_id = None
            student.active = False
        student.access_code = ""
        student.password_change_required = False
        if student.created_by_user_id not in admin_ids:
            student.created_by_user_id = None

    for log in db.query(ModerationLog).filter(ModerationLog.classroom_id.is_(None), ModerationLog.project_id.is_not(None)).all():
        project = db.get(Project, log.project_id)
        if project and project.classroom_id is not None:
            log.classroom_id = project.classroom_id
    db.commit()


def now_naive():
    from backend.app.models import now

    return now()


def seed_default_classroom_and_student():
    from backend.app.auth import hash_password
    from backend.app.models import Classroom, Project, User, now

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.role == "admin").order_by(User.id.asc()).first()
        classroom = db.query(Classroom).filter(Classroom.name == "默认班级").first()
        if not classroom:
            classroom = Classroom(name="默认班级", grade_level="mixed", owner_teacher_id=admin.id if admin else None)
            db.add(classroom)
            db.commit()
            db.refresh(classroom)

        student = db.query(User).filter(User.username == "student.demo").first()
        if not student:
            student = User(
                name="默认学生",
                role="student",
                classroom_id=classroom.id,
                username="student.demo",
                password_hash=hash_password("bcm123456"),
                password_change_required=False,
                registered_at=now(),
                access_code="",
                age_level="primary_lower",
                active=True,
                created_by_user_id=admin.id if admin else None,
            )
            db.add(student)
            db.commit()
            db.refresh(student)

        legacy_projects = db.query(Project).filter(Project.user_id.is_(None)).all()
        for project in legacy_projects:
            project.user_id = student.id
            project.classroom_id = classroom.id
            project.owner_name = student.name
            project.owner_teacher_id = None
        if legacy_projects:
            db.commit()
    finally:
        db.close()


def backfill_asset_metadata():
    from backend.app.models import Asset

    db = SessionLocal()
    try:
        changed = False
        for asset in db.query(Asset).filter((Asset.file_extension == "") | (Asset.original_name == "")).all():
            remote = asset.file_path.startswith(("http://", "https://"))
            source_path = urlparse(asset.file_path).path if remote else asset.file_path
            path = Path(source_path)
            asset.original_name = asset.original_name or path.name
            asset.file_extension = asset.file_extension or path.suffix.lower()
            asset.mime_type = asset.mime_type or mimetypes.guess_type(source_path)[0] or "application/octet-stream"
            if remote:
                asset.safety_status = "remote_unverified"
            elif path.is_file() and path.stat().st_size <= 50 * 1024 * 1024:
                content = path.read_bytes()
                asset.file_size = len(content)
                asset.checksum_sha256 = hashlib.sha256(content).hexdigest()
                asset.safety_status = "verified"
            else:
                asset.safety_status = "missing" if not path.is_file() else "unverified"
            changed = True
        if changed:
            db.commit()
    finally:
        db.close()
