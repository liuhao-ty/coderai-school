from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.submission_files import DEFAULT_SUBMISSION_EXTENSIONS, MAX_SUBMISSION_FILE_BYTES


class TextGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    mode: Literal["general", "story", "polish", "prompt_refine", "code_explain"] = "general"
    age_level: Literal["primary_lower", "primary_upper", "secondary", "mixed"] = "primary_lower"
    save_project: bool = True


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    style: str = "classroom-friendly"
    size: str = "1024x1024"
    source_image_path: str | None = None
    save_project: bool = True


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    source_image_path: str | None = None
    duration_seconds: int = 5
    save_project: bool = True


class WorkflowRunRequest(BaseModel):
    template_id: str = "text_to_image"
    workflow_id: int | None = None
    prompt: str = Field(min_length=1)


class WorkflowNodeRetryRequest(BaseModel):
    node_id: str = Field(min_length=1)


class WorkflowSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    classroom_id: int | None = None
    status: str = "draft"
    definition: dict = Field(default_factory=dict)


class ProjectCreateRequest(BaseModel):
    title: str
    project_type: str = "text"
    owner_name: str = "默认学生"
    summary: str = ""
    file_path: str = ""


class ProjectUpdateRequest(BaseModel):
    title: str
    summary: str = ""


class AssetRegisterRequest(BaseModel):
    asset_type: str = "document"
    file_path: str = Field(min_length=1)
    metadata_json: str = "{}"
    display_name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    project_id: int | None = None
    classroom_id: int | None = None
    lesson_id: int | None = None


class ProviderSettingsRequest(BaseModel):
    name: str = Field(default="OpenAI Compatible", min_length=1, max_length=80)
    provider_type: str = Field(default="openai_compatible", min_length=1, max_length=40)
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1, max_length=2_000)
    api_key: str = Field(default="", max_length=8_000)
    text_model: str = Field(default="gpt-4o-mini", max_length=120)
    image_model: str = Field(default="gpt-image-1", max_length=120)
    video_model: str = Field(default="", max_length=120)
    enabled: bool = True
    student_selectable: bool = False


class AIGenerationJobRequest(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=80)
    capability: Literal["text", "image"]
    prompt: str = Field(min_length=1, max_length=50_000)
    provider_id: int | None = Field(default=None, ge=1)
    mode: Literal["general", "story", "polish", "prompt_refine"] = "general"
    style: str = Field(default="classroom-friendly", max_length=120)
    size: str = Field(default="1024x1024", max_length=40)
    source_image_path: str | None = Field(default=None, max_length=2_000)
    save_project: bool = True


class AgentConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=160)
    selected_provider_id: int | None = Field(default=None, ge=1)


class AgentConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    selected_provider_id: int | None = Field(default=None, ge=1)


class AgentMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    client_request_id: str = Field(min_length=8, max_length=80)


class AgentToolRunRequest(BaseModel):
    capability: Literal["image", "video", "workflow"]
    prompt: str = Field(min_length=1, max_length=20_000)
    client_request_id: str = Field(min_length=8, max_length=80)
    duration_seconds: int = Field(default=5, ge=1, le=10)
    workflow_id: int | None = Field(default=None, ge=1)


class AgentArtifactSaveRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ProviderConnectionTestRequest(BaseModel):
    capability: Literal["text", "image", "video"] = "text"
    provider_id: int | None = Field(default=None, ge=1)


class ProviderEnabledRequest(BaseModel):
    enabled: bool


class ProviderRoutesRequest(BaseModel):
    routes: dict[Literal["text", "image", "video"], list[int]]


class ProviderAcceptanceTestRequest(BaseModel):
    capability: Literal["text", "image", "video"]
    provider_id: int | None = Field(default=None, ge=1)
    confirmation: str = Field(default="", max_length=80)


class ProviderAcceptanceScopeItem(BaseModel):
    provider_type: str = Field(min_length=1, max_length=80)
    capabilities: list[Literal["text", "image", "video"]] = Field(min_length=1, max_length=3)


class ProviderAcceptanceScopeRequest(BaseModel):
    providers: list[ProviderAcceptanceScopeItem] = Field(min_length=1, max_length=6)


class ProviderFailureEvidencePolicyRequest(BaseModel):
    required: bool = True
    reason: str = Field(default="", max_length=500)
    confirmation: str = Field(default="", max_length=100)


class ClassroomAcceptanceRequest(BaseModel):
    confirmation: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=500)


class TeacherRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=500)


class TeacherLogoutRequest(BaseModel):
    refresh_token: str = Field(default="", max_length=500)


class ModerationRequest(BaseModel):
    text: str


class ModerationSettingsRequest(BaseModel):
    blocked_words: list[str] = Field(default_factory=list)


class ImageModerationReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=1000)


class CoursePackageTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    instructions: str = Field(default="", max_length=20_000)
    tool_scope: str = Field(default="text,image,workflow", max_length=120)
    status: str = Field(default="published", pattern="^(draft|published)$")
    starts_at: datetime | None = None
    due_at: datetime | None = None
    lesson_title: str = Field(default="", max_length=160)
    rubric: list[dict] = Field(default_factory=lambda: [{"criterion": "完成度", "max_score": 100}], max_length=30)


class CoursePackageLessonRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=100_000)
    order_index: int = Field(default=0, ge=0, le=10_000)


class CourseImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    package_version: str = Field(default="1.0.0", max_length=40)
    author: str = Field(default="", max_length=120)
    age_range: str = Field(default="全年龄", max_length=80)
    cover_path: str = Field(default="", max_length=2_000)
    dependencies: list[dict] = Field(default_factory=list, max_length=100)
    checklist: list[str] = Field(default_factory=list, max_length=100)
    conflict_strategy: str = Field(default="rename", pattern="^(skip|rename|replace)$")
    classroom_id: int | None = None
    status: str = Field(default="draft", pattern="^(draft|published)$")
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    lessons: list[CoursePackageLessonRequest] = Field(default_factory=list, max_length=200)
    tasks: list[CoursePackageTaskRequest] = Field(default_factory=list, max_length=200)


class CourseUpdateRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    package_version: str = "1.0.0"
    author: str = ""
    age_range: str = "全年龄"
    cover_path: str = ""
    dependencies: list[dict] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    classroom_id: int | None = None
    status: str = "draft"
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class CoursePackageRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    package_version: str = Field(default="1.0.0", min_length=1, max_length=40)
    author_user_id: int = Field(ge=1)
    school_stages: list[Literal["primary_lower", "primary_upper", "secondary"]] = Field(
        default_factory=lambda: ["primary_lower", "primary_upper", "secondary"],
        min_length=1,
        max_length=3,
    )
    cover_path: str = Field(default="", max_length=2_000)


class CoursePackageTeachersUpdateRequest(BaseModel):
    teacher_ids: list[int] = Field(default_factory=list, max_length=500)


class CurriculumCourseRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    order_index: int = Field(default=0, ge=0, le=10_000)
    assignment_instructions: str = Field(default="", max_length=20_000)
    tool_scope: str = Field(default="text,image,workflow", max_length=120)
    rubric: list[dict] = Field(default_factory=lambda: [{"criterion": "完成度", "max_score": 100}], min_length=1, max_length=30)
    submission_extensions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUBMISSION_EXTENSIONS),
        min_length=1,
        max_length=len(DEFAULT_SUBMISSION_EXTENSIONS),
    )
    submission_max_bytes: int = Field(
        default=MAX_SUBMISSION_FILE_BYTES,
        ge=1024 * 1024,
        le=MAX_SUBMISSION_FILE_BYTES,
    )


class CurriculumCourseOrderRequest(BaseModel):
    course_ids: list[int] = Field(min_length=1, max_length=500)


class StudentCourseWorkspaceSaveRequest(BaseModel):
    content_markdown: str = Field(default="", max_length=500_000)
    answers: dict[str, str | list[str]] | None = None


class CourseScheduleItemRequest(BaseModel):
    course_id: int = Field(ge=1)
    target_type: Literal["student", "classroom"]
    target_id: int = Field(ge=1)
    starts_at: datetime
    due_at: datetime | None = None


class CourseScheduleBatchRequest(BaseModel):
    items: list[CourseScheduleItemRequest] = Field(min_length=1, max_length=500)


class CourseScheduleUpdateRequest(BaseModel):
    starts_at: datetime
    due_at: datetime | None = None


class CourseScheduleCancelRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class LessonRequest(BaseModel):
    course_id: int | None = None
    title: str = Field(min_length=1)
    content: str = ""
    order_index: int = 0


class ClassTaskRequest(BaseModel):
    title: str
    instructions: str = ""
    tool_scope: str = "text,image,workflow"
    classroom_id: int | None = None
    lesson_id: int | None = None
    status: str = "published"
    starts_at: datetime | None = None
    due_at: datetime | None = None
    rubric: list[dict] = Field(default_factory=lambda: [{"criterion": "完成度", "max_score": 100}])


class ClassroomCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    grade_level: Literal["primary_lower", "primary_upper", "secondary", "mixed"] = "mixed"


class ClassroomUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    grade_level: Literal["primary_lower", "primary_upper", "secondary", "mixed"] = "mixed"


class ClassroomTeachersUpdateRequest(BaseModel):
    teacher_ids: list[int] = Field(default_factory=list, max_length=200)


class StudentCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    username: str = Field(min_length=4, max_length=40)
    classroom_id: int | None = None
    age_level: Literal["primary_lower", "primary_upper", "secondary"] = "primary_lower"


class StudentUpdateRequest(BaseModel):
    name: str = Field(min_length=1)
    username: str | None = Field(default=None, min_length=4, max_length=40)
    classroom_id: int | None = None
    age_level: Literal["primary_lower", "primary_upper", "secondary"] = "primary_lower"
    active: bool = True


class StudentBatchRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=500)


class StudentBatchTransferRequest(StudentBatchRequest):
    classroom_id: int


class StudentClassroomUpdateRequest(BaseModel):
    classroom_id: int | None = None


class StudentBatchRestoreRequest(StudentBatchRequest):
    classroom_id: int


class StudentLoginRequest(BaseModel):
    organization_code: str = Field(default="coderai-pilot", min_length=2, max_length=80)
    username: str = Field(default="", max_length=80)
    password: str = Field(default="", max_length=200)
    device_name: str = Field(default="此设备", max_length=160)


class StudentRegisterRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=4, max_length=40)
    password: str = Field(min_length=8, max_length=200)
    device_name: str = Field(default="此设备", max_length=160)


class StudentPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    next_password: str = Field(min_length=8, max_length=200)


class PrivacyPolicyPublishRequest(BaseModel):
    version: str = Field(min_length=1, max_length=40, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
    title: str = Field(min_length=1, max_length=160)
    content_markdown: str = Field(min_length=20, max_length=50_000)
    require_guardian_consent: bool = False
    allow_external_ai_processing: bool = True
    retention_days: int = Field(default=365, ge=30, le=3650)
    effective_at: datetime | None = None


class GuardianConsentCreateRequest(BaseModel):
    guardian_name: str = Field(min_length=1, max_length=120)
    relationship: str = Field(default="监护人", min_length=1, max_length=60)
    guardian_contact: str = Field(default="", max_length=160)
    consent_method: Literal["written", "digital", "in_person", "phone"] = "written"
    evidence_reference: str = Field(default="", max_length=240)
    scopes: list[Literal["course_learning", "ai_generation", "cloud_provider_transfer", "portfolio_storage"]] = Field(
        default_factory=lambda: ["course_learning", "ai_generation", "cloud_provider_transfer", "portfolio_storage"],
        min_length=1,
        max_length=4,
    )
    consented_at: datetime | None = None


class GuardianConsentRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class StudentDataDeleteRequest(BaseModel):
    preflight_token: str = Field(min_length=20, max_length=4_000)
    confirm_student_name: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


class SubmissionCreateRequest(BaseModel):
    project_id: int


class SubmissionReviewRequest(BaseModel):
    status: str = "reviewed"
    feedback: str = ""
    score: int | None = None
    is_featured: bool | None = None


class FeedbackTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)


class TeacherLoginRequest(BaseModel):
    organization_code: str = Field(default="coderai-pilot", min_length=2, max_length=80)
    username: str = Field(default="admin", max_length=80)
    password: str = Field(min_length=1)
    device_name: str = Field(default="此设备", max_length=160)


class TeacherPasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    next_password: str = Field(min_length=8, max_length=200)


class TeacherAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=4, max_length=40)
    role: Literal["teacher", "admin"] = "teacher"


class TeacherAccountUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: Literal["teacher", "admin"] = "teacher"
    active: bool = True


class SystemRestoreRequest(BaseModel):
    classrooms: list[dict] = Field(default_factory=list)
    classroom_teachers: list[dict] = Field(default_factory=list)
    students: list[dict] = Field(default_factory=list)
    courses: list[dict] = Field(default_factory=list)
    lessons: list[dict] = Field(default_factory=list)
    tasks: list[dict] = Field(default_factory=list)
    assets: list[dict] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    submissions: list[dict] = Field(default_factory=list)
    submission_versions: list[dict] = Field(default_factory=list)
    feedback_templates: list[dict] = Field(default_factory=list)
    moderation_logs: list[dict] = Field(default_factory=list)


class LicenseSettingsRequest(BaseModel):
    license_key: str = Field(default="", max_length=32 * 1024)


class TrustedPluginPublisherRequest(BaseModel):
    key_id: str = Field(min_length=3, max_length=64, pattern="^[a-z0-9][a-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    public_key: str = Field(min_length=40, max_length=100)


class PluginEnabledRequest(BaseModel):
    enabled: bool


class PluginRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4_000)
    save_project: bool = True
    age_level: Literal["primary_lower", "primary_upper", "secondary"] = "primary_lower"
