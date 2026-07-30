from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db import Base
from backend.app.tenancy import TenantScopedMixin, current_organization_id


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now():
    return datetime.now(BEIJING_TZ).replace(tzinfo=None)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seat_limit: Mapped[int] = mapped_column(Integer, default=50)
    beta_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class User(TenantScopedMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="默认学生")
    role: Mapped[str] = mapped_column(String(30), default="student")
    username: Mapped[str] = mapped_column(String(80), default="", index=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    password_change_required: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_version: Mapped[int] = mapped_column(Integer, default=1)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    access_code: Mapped[str] = mapped_column(String(80), default="")
    age_level: Mapped[str] = mapped_column(String(30), default="primary_lower")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_classroom_name: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    classroom = relationship("Classroom")


class Classroom(TenantScopedMixin, Base):
    __tablename__ = "classrooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    grade_level: Mapped[str] = mapped_column(String(40), default="mixed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ClassroomTeacher(TenantScopedMixin, Base):
    __tablename__ = "classroom_teachers"

    classroom_id: Mapped[int] = mapped_column(ForeignKey("classrooms.id"), primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    classroom = relationship("Classroom")
    teacher = relationship("User")


class CoursePackage(TenantScopedMixin, Base):
    __tablename__ = "course_packages"
    __table_args__ = (UniqueConstraint("organization_id", "legacy_course_id", name="ux_course_packages_org_legacy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    author_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    legacy_course_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    package_version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    author: Mapped[str] = mapped_column(String(120), default="")
    age_range: Mapped[str] = mapped_column(String(80), default="全年龄")
    school_stages_json: Mapped[str] = mapped_column(Text, default='["primary_lower","primary_upper","secondary"]')
    cover_path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    creator = relationship("User", foreign_keys=[created_by_user_id])
    author_account = relationship("User", foreign_keys=[author_user_id])
    courses = relationship("CurriculumCourse", back_populates="package", order_by="CurriculumCourse.order_index")
    teacher_assignments = relationship("CoursePackageTeacher", back_populates="package", cascade="all, delete-orphan")


class CoursePackageTeacher(TenantScopedMixin, Base):
    __tablename__ = "course_package_teachers"

    package_id: Mapped[int] = mapped_column(ForeignKey("course_packages.id"), primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    assigned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    package = relationship("CoursePackage", back_populates="teacher_assignments")
    teacher = relationship("User", foreign_keys=[teacher_id])


class CurriculumCourse(TenantScopedMixin, Base):
    __tablename__ = "curriculum_courses"
    __table_args__ = (UniqueConstraint("organization_id", "legacy_lesson_id", name="ux_curriculum_courses_org_legacy"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("course_packages.id"), nullable=False, index=True)
    legacy_lesson_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    assignment_instructions: Mapped[str] = mapped_column(Text, default="")
    tool_scope: Mapped[str] = mapped_column(String(120), default="text,image,workflow")
    rubric_json: Mapped[str] = mapped_column(Text, default='[{"criterion":"完成度","max_score":100}]')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    package = relationship("CoursePackage", back_populates="courses")
    materials = relationship("CourseMaterial", back_populates="course")
    schedules = relationship("CourseSchedule", back_populates="course")


class CourseMaterial(TenantScopedMixin, Base):
    __tablename__ = "course_materials"
    __table_args__ = (UniqueConstraint("course_id", "kind", name="ux_course_material_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("curriculum_courses.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    source_path: Mapped[str] = mapped_column(Text, default="")
    preview_path: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    conversion_status: Mapped[str] = mapped_column(String(20), default="ready")
    conversion_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    course = relationship("CurriculumCourse", back_populates="materials")


class CourseSchedule(TenantScopedMixin, Base):
    __tablename__ = "course_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("curriculum_courses.id"), nullable=False, index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_student_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    canceled_reason: Mapped[str] = mapped_column(Text, default="")
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    course = relationship("CurriculumCourse", back_populates="schedules")
    creator = relationship("User", foreign_keys=[created_by_user_id])
    target_student = relationship("User", foreign_keys=[target_student_id])
    classroom = relationship("Classroom")
    task = relationship("Task", back_populates="course_schedule", uselist=False)


class Course(TenantScopedMixin, Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    package_version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    author: Mapped[str] = mapped_column(String(120), default="")
    age_range: Mapped[str] = mapped_column(String(80), default="全年龄")
    cover_path: Mapped[str] = mapped_column(Text, default="")
    dependencies_json: Mapped[str] = mapped_column(Text, default="[]")
    checklist_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    classroom = relationship("Classroom")


class Lesson(TenantScopedMixin, Base):
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    course = relationship("Course")


class Task(TenantScopedMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
    course_schedule_id: Mapped[int | None] = mapped_column(ForeignKey("course_schedules.id"), nullable=True, unique=True, index=True)
    target_student_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    task_kind: Mapped[str] = mapped_column(String(20), default="legacy")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="")
    tool_scope: Mapped[str] = mapped_column(String(120), default="text,image,workflow")
    status: Mapped[str] = mapped_column(String(20), default="published")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rubric_json: Mapped[str] = mapped_column(Text, default='[{"criterion":"完成度","max_score":100}]')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    lesson = relationship("Lesson")
    course_schedule = relationship("CourseSchedule", back_populates="task")
    target_student = relationship("User", foreign_keys=[target_student_id])


class Project(TenantScopedMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index(
            "ux_projects_org_student_curriculum_course",
            "organization_id",
            "user_id",
            "curriculum_course_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    project_type: Mapped[str] = mapped_column(String(40), default="text")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    curriculum_course_id: Mapped[int | None] = mapped_column(
        ForeignKey("curriculum_courses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    owner_name: Mapped[str] = mapped_column(String(120), default="默认学生")
    summary: Mapped[str] = mapped_column(Text, default="")
    workspace_answers_json: Mapped[str] = mapped_column(Text, default="{}")
    file_path: Mapped[str] = mapped_column(Text, default="")
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active")
    moderation_status: Mapped[str] = mapped_column(String(20), default="approved")
    moderation_reason: Mapped[str] = mapped_column(Text, default="")
    moderation_log_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    user = relationship("User")
    curriculum_course = relationship("CurriculumCourse")
    classroom = relationship("Classroom")


class TaskSubmission(TenantScopedMixin, Base):
    __tablename__ = "task_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    feedback: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_count: Mapped[int] = mapped_column(Integer, default=1)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    rubric_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    max_score_snapshot: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    task = relationship("Task")
    project = relationship("Project")
    user = relationship("User")
    classroom = relationship("Classroom")


class SubmissionVersion(TenantScopedMixin, Base):
    __tablename__ = "submission_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("task_submissions.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    project_title: Mapped[str] = mapped_column(String(160), default="")
    project_summary: Mapped[str] = mapped_column(Text, default="")
    project_file_path: Mapped[str] = mapped_column(Text, default="")
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    submission = relationship("TaskSubmission")
    project = relationship("Project")


class FeedbackTemplate(TenantScopedMixin, Base):
    __tablename__ = "feedback_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Asset(TenantScopedMixin, Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    original_name: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(120), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_extension: Mapped[str] = mapped_column(String(20), default="")
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    safety_status: Mapped[str] = mapped_column(String(30), default="verified")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    classroom = relationship("Classroom")
    lesson = relationship("Lesson")


class Workflow(TenantScopedMixin, Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    owner = relationship("User")
    classroom = relationship("Classroom")


class WorkflowRun(TenantScopedMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    node_states_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class VideoTask(TenantScopedMixin, Base):
    __tablename__ = "video_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_task_id: Mapped[str] = mapped_column(String(160), default="")
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    source_image_path: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(40), default="submitted")
    file_id: Mapped[str] = mapped_column(String(160), default="")
    download_url: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    download_url_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    project = relationship("Project")
    user = relationship("User")
    classroom = relationship("Classroom")


class AIProvider(TenantScopedMixin, Base):
    __tablename__ = "ai_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), default="OpenAI Compatible")
    provider_type: Mapped[str] = mapped_column(String(40), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(Text, default="https://api.openai.com/v1")
    api_key: Mapped[str] = mapped_column(Text, default="")
    text_model: Mapped[str] = mapped_column(String(120), default="gpt-4o-mini")
    image_model: Mapped[str] = mapped_column(String(120), default="gpt-image-1")
    video_model: Mapped[str] = mapped_column(String(120), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_status: Mapped[str] = mapped_column(String(20), default="untested")
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AIProviderRoute(TenantScopedMixin, Base):
    __tablename__ = "ai_provider_routes"
    __table_args__ = (UniqueConstraint("organization_id", "capability", name="ux_ai_routes_org_capability"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capability: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    provider_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class UsageLog(TenantScopedMixin, Base):
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True, index=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(80), default="")
    feature: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="success")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ProviderAcceptanceRun(TenantScopedMixin, Base):
    __tablename__ = "provider_acceptance_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_name: Mapped[str] = mapped_column(String(80), default="")
    provider_type: Mapped[str] = mapped_column(String(40), default="")
    capability: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(30), default="failed", index=True)
    scenario: Mapped[str] = mapped_column(String(40), default="unknown")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(String(300), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ModerationLog(TenantScopedMixin, Base):
    __tablename__ = "moderation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    classroom_id: Mapped[int | None] = mapped_column(ForeignKey("classrooms.id"), nullable=True, index=True)
    input_text: Mapped[str] = mapped_column(Text, default="")
    content_stage: Mapped[str] = mapped_column(String(20), default="input")
    passed: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    resource_type: Mapped[str] = mapped_column(String(20), default="text")
    resource_path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="approved")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AppSetting(TenantScopedMixin, Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="ux_app_settings_org_key"),)

    # Keep the default organization's legacy `Session.get(AppSetting, key)`
    # contract while allowing the same setting key in additional organizations.
    id: Mapped[str] = mapped_column(
        String(220),
        primary_key=True,
        default=lambda context: (
            str(context.get_current_parameters().get("key") or "")
            if int(context.get_current_parameters().get("organization_id") or current_organization_id()) == 1
            else (
                f"{int(context.get_current_parameters().get('organization_id') or current_organization_id())}:"
                f"{str(context.get_current_parameters().get('key') or '')}"
            )
        ),
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class PrivacyPolicy(TenantScopedMixin, Base):
    __tablename__ = "privacy_policies"
    __table_args__ = (UniqueConstraint("organization_id", "version", name="ux_privacy_policies_org_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    require_guardian_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_external_ai_processing: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    effective_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class GuardianConsent(TenantScopedMixin, Base):
    __tablename__ = "guardian_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("privacy_policies.id"), nullable=False, index=True)
    guardian_name: Mapped[str] = mapped_column(String(120), nullable=False)
    guardian_relationship: Mapped[str] = mapped_column(String(60), default="监护人")
    guardian_contact_encrypted: Mapped[str] = mapped_column(Text, default="")
    consent_method: Mapped[str] = mapped_column(String(40), default="written")
    evidence_reference: Mapped[str] = mapped_column(String(240), default="")
    scope_json: Mapped[str] = mapped_column(Text, default="[]")
    recorded_by_session_id: Mapped[int | None] = mapped_column(ForeignKey("teacher_sessions.id"), nullable=True)
    consented_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revocation_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    user = relationship("User")
    policy = relationship("PrivacyPolicy")


class TeacherAuditLog(TenantScopedMixin, Base):
    __tablename__ = "teacher_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("teacher_sessions.id"), nullable=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_username: Mapped[str] = mapped_column(String(80), default="")
    actor_name: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(60), default="")
    target_id: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(String(300), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class AuthLoginAttempt(TenantScopedMixin, Base):
    __tablename__ = "auth_login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    identifier_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class TeacherSession(TenantScopedMixin, Base):
    __tablename__ = "teacher_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(160), default="此设备")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User")


class StudentSession(TenantScopedMixin, Base):
    __tablename__ = "student_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(160), default="此设备")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User")


class RetentionException(TenantScopedMixin, Base):
    __tablename__ = "retention_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class RetentionRequest(TenantScopedMixin, Base):
    __tablename__ = "retention_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    executed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approval_note: Mapped[str] = mapped_column(String(300), default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


Index(
    "ux_users_org_username_ci",
    User.organization_id,
    func.lower(User.username),
    unique=True,
    sqlite_where=User.username != "",
    postgresql_where=User.username != "",
)
