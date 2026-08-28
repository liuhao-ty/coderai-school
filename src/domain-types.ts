export type SchoolStage = "primary_lower" | "primary_upper" | "secondary";
export type ProjectCategory = "course_workspace" | "ai_generated";

export type Project = {
  id: number;
  owner_teacher_id?: number | null;
  title: string;
  project_type: string;
  project_category: ProjectCategory;
  workspace_is_primary?: boolean;
  user_id?: number | null;
  curriculum_course_id?: number | null;
  student_archived?: boolean;
  classroom_id?: number | null;
  owner_name: string;
  summary: string;
  file_path: string;
  original_file_name?: string;
  mime_type?: string;
  file_size?: number;
  file_exists: boolean;
  file_status: string;
  latest_submitted_at?: string | null;
  created_at: string;
  updated_at: string;
  lifecycle_status: "active" | "archived" | "trashed";
  moderation_status: "approved" | "pending" | "rejected";
  moderation_reason: string;
  moderation_log_id?: number | null;
  archived_at?: string | null;
  trashed_at?: string | null;
};

export type Classroom = {
  id: number;
  owner_teacher_id?: number | null;
  name: string;
  grade_level: SchoolStage | "mixed";
  grade_level_label: string;
  teacher_ids: number[];
  teachers: Array<{ id: number; name: string; username: string; active: boolean }>;
  can_manage: boolean;
  assignment_status: "assigned" | "unassigned";
  created_at: string;
};

export type Course = {
  id: number;
  owner_teacher_id?: number | null;
  classroom_id?: number | null;
  classroom_name: string;
  title: string;
  description: string;
  package_version: string;
  author: string;
  age_range: string;
  cover_path: string;
  dependencies: Array<{ name: string; type?: string; required?: boolean }>;
  checklist: string[];
  status: "draft" | "published";
  starts_at?: string | null;
  ends_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type Lesson = {
  id: number;
  owner_teacher_id?: number | null;
  course_id?: number | null;
  course_title: string;
  title: string;
  content: string;
  order_index: number;
  created_at: string;
};

export type AssetItem = {
  id: number;
  owner_teacher_id?: number | null;
  project_id?: number | null;
  classroom_id?: number | null;
  classroom_name: string;
  lesson_id?: number | null;
  lesson_title: string;
  course_title: string;
  asset_type: string;
  file_path: string;
  metadata_json: string;
  metadata: { name?: string; description?: string; tags?: string[] };
  original_name: string;
  mime_type: string;
  file_size: number;
  file_extension: string;
  checksum_sha256: string;
  safety_status: string;
  file_exists: boolean;
  file_status: string;
  created_at: string;
};

export type ClassTask = {
  id: number;
  owner_teacher_id?: number | null;
  classroom_id?: number | null;
  lesson_id?: number | null;
  course_schedule_id?: number | null;
  target_student_id?: number | null;
  task_kind?: "legacy" | "schedule";
  lesson_title: string;
  course_id?: number | null;
  course_title: string;
  package_id?: number | null;
  package_title?: string;
  title: string;
  instructions: string;
  tool_scope: string;
  status: "draft" | "published";
  starts_at?: string | null;
  due_at?: string | null;
  rubric: Array<{ criterion: string; max_score: number }>;
  max_score: number;
  created_at: string;
};

export type TaskSubmission = {
  id: number;
  owner_teacher_id?: number | null;
  task_id: number;
  task_title: string;
  project_id: number | null;
  source_type?: "project" | "attachment";
  project_title: string;
  user_id: number;
  student_name: string;
  student_archived?: boolean;
  classroom_id?: number | null;
  classroom_name: string;
  status: string;
  feedback: string;
  score?: number | null;
  version_count: number;
  is_late: boolean;
  due_at?: string | null;
  is_featured: boolean;
  max_score: number;
  rubric?: Array<{ criterion: string; max_score: number }>;
  package_id?: number | null;
  package_title?: string;
  course_access_status?: "active" | "revoked";
  can_review?: boolean;
  read_only_reason?: "" | "course_access_revoked" | "schedule_forbidden" | "archived_student";
  created_at: string;
  updated_at: string;
};

export type CourseMaterialKind = "slides" | "starter_markdown" | "result_markdown";

export type CourseMaterialState = {
  id?: number | null;
  kind: CourseMaterialKind;
  label: string;
  missing: boolean;
  can_preview: boolean;
  can_download: boolean;
  conversion_status: "missing" | "pending" | "processing" | "ready" | "failed";
  conversion_error: string;
  original_name: string;
  file_size: number;
  updated_at?: string | null;
};

export type CurriculumCourseItem = {
  id: number;
  package_id: number;
  package_title: string;
  title: string;
  description: string;
  order_index: number;
  assignment_instructions: string;
  tool_scope: string;
  rubric: Array<{ criterion: string; max_score: number }>;
  submission_extensions: string[];
  submission_max_bytes: number;
  materials: Record<CourseMaterialKind, CourseMaterialState>;
  schedule_ids: number[];
  created_at: string;
  updated_at: string;
};

export type CoursePackageItem = {
  id: number;
  title: string;
  description: string;
  package_version: string;
  author_user_id: number | null;
  author: string;
  author_account: {
    id: number;
    name: string;
    role: "teacher" | "admin";
    active: boolean;
  } | null;
  school_stages: SchoolStage[];
  age_range: string;
  cover_path: string;
  status: "draft" | "published" | "archived";
  published_at?: string | null;
  archived_at?: string | null;
  course_count: number;
  courses: CurriculumCourseItem[];
  teacher_ids: number[];
  teachers: Array<{ id: number; name: string; username: string; active: boolean }>;
  assignment_status: "assigned" | "unassigned";
  can_preview: boolean;
  can_schedule: boolean;
  created_at: string;
  updated_at: string;
};

export type CourseScheduleItem = {
  id: number;
  course_id: number;
  course_title: string;
  package_id: number;
  package_title: string;
  target_type: "student" | "classroom";
  target_id: number;
  target_name: string;
  created_by_user_id: number;
  created_by_name: string;
  starts_at: string;
  due_at?: string | null;
  status: "scheduled" | "active" | "overdue" | "canceled";
  stored_status: string;
  canceled_reason: string;
  submission_count: number;
  has_submitted: boolean;
  can_manage: boolean;
  course_access_status: "active" | "revoked";
  read_only_reason: "" | "course_access_revoked";
  task_id?: number | null;
  assignment_instructions: string;
  tool_scope: string;
  student_archived: boolean;
  created_at: string;
  updated_at: string;
};

export type SubmissionVersion = {
  id: number;
  submission_id: number;
  version_number: number;
  project_id: number | null;
  source_type: "project" | "attachment";
  project_title: string;
  project_summary: string;
  project_file_path: string;
  file_available: boolean;
  file?: {
    original_file_name: string;
    mime_type: string;
    file_size: number;
    safety_status: "approved" | "pending" | "rejected";
    source_type: "project" | "attachment";
  } | null;
  attachment?: {
    id: number;
    original_file_name: string;
    mime_type: string;
    file_size: number;
    safety_status: "approved" | "pending" | "rejected";
  } | null;
  is_late: boolean;
  review_status: string;
  feedback: string;
  score?: number | null;
  max_score: number;
  created_at: string;
};

export type AIGenerationJob = {
  id: number;
  client_request_id: string;
  capability: "text" | "image" | "video" | "workflow";
  operation: string;
  provider_id?: number | null;
  model: string;
  status: "queued" | "running" | "succeeded" | "failed" | "timed_out" | "canceled";
  result: Record<string, unknown> & {
    text?: string;
    artifact_id?: number;
    video_task_id?: number;
    file_available?: boolean;
    moderation_status?: "approved" | "pending" | "rejected";
    moderation_reason?: string;
  };
  error_code: string;
  error_message: string;
  retry_count: number;
  cancel_requested: boolean;
  project_id?: number | null;
  conversation_id?: number | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
};

export type AgentConversation = {
  id: number;
  title: string;
  selected_provider_id?: number | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AgentMessage = {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  status: "pending" | "completed" | "failed";
  provider_id?: number | null;
  model: string;
  sequence: number;
  tool_suggestion?: { capability?: "image" | "video" | "workflow"; prompt?: string };
  created_at: string;
};

export type AgentArtifact = {
  id: number;
  conversation_id: number;
  message_id?: number | null;
  generation_job_id?: number | null;
  saved_project_id?: number | null;
  artifact_type: "image" | "video";
  title: string;
  original_file_name: string;
  mime_type: string;
  file_size: number;
  status: "available" | "pending_review" | "rejected" | "saved" | "expired";
  file_available: boolean;
  expires_at?: string | null;
  created_at: string;
};

export type FeedbackTemplateItem = { id: number; name: string; content: string; created_at: string };
export type SubmissionStatistics = {
  overall: { total: number; reviewed: number; returned: number; late: number; featured: number; average_score?: number | null };
  by_classroom: Array<{ classroom_id?: number | null; classroom_name: string; total: number; reviewed: number; returned: number; late: number; featured: number; average_score?: number | null }>;
  by_task: Array<{ task_id: number; task_title: string; total: number; reviewed: number; returned: number; late: number; featured: number; average_score?: number | null }>;
};

export type VideoTask = {
  id: number;
  owner_teacher_id?: number | null;
  provider_task_id: string;
  provider_id?: number | null;
  provider_type: string;
  model: string;
  prompt: string;
  source_image_path: string;
  duration_seconds: number;
  status: string;
  file_id: string;
  download_url: string;
  file_path: string;
  error_message: string;
  retry_count: number;
  max_retries: number;
  can_retry: boolean;
  file_available: boolean;
  timeout_at?: string | null;
  last_checked_at?: string | null;
  download_url_expires_at?: string | null;
  project_id?: number | null;
  user_id?: number | null;
  classroom_id?: number | null;
  student_name: string;
  classroom_name: string;
  created_at: string;
  updated_at: string;
};

export type ModerationLogItem = {
  id: number;
  owner_teacher_id?: number | null;
  user_id?: number | null;
  classroom_id?: number | null;
  input_text: string;
  content_stage: string;
  passed: boolean;
  reason: string;
  resource_type: string;
  resource_path: string;
  status: "approved" | "pending" | "rejected";
  project_id?: number | null;
  review_note: string;
  reviewed_at?: string | null;
  created_at: string;
};

export type TeacherAuditLogItem = {
  id: number;
  session_id?: number | null;
  actor_user_id?: number | null;
  actor_username: string;
  actor_name: string;
  action: string;
  target_type: string;
  target_id: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
};

export type PrivacyPolicy = {
  id: number;
  version: string;
  title: string;
  content_markdown: string;
  status: string;
  active: boolean;
  require_guardian_consent: boolean;
  allow_external_ai_processing: boolean;
  retention_days: number;
  effective_at: string;
  published_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type GuardianConsent = {
  id: number;
  user_id: number;
  policy_id: number;
  policy_version: string;
  guardian_name: string;
  relationship: string;
  guardian_contact_masked: string;
  consent_method: "written" | "digital" | "in_person" | "phone";
  evidence_reference: string;
  scopes: string[];
  status: "granted" | "revoked";
  consented_at: string;
  revoked_at?: string | null;
  revocation_reason: string;
  created_at: string;
};

export type StudentPrivacyState = {
  student: {
    id: number;
    name: string;
    classroom_id?: number | null;
    classroom_name: string;
    account_status: "active" | "disabled" | "archived";
  };
  policy: PrivacyPolicy;
  consent_required: boolean;
  external_ai_allowed: boolean;
  ai_access_allowed: boolean;
  active_consent?: GuardianConsent | null;
  consent_history: GuardianConsent[];
};

export type StudentDeletionPreflight = {
  student_id: number;
  student_name: string;
  counts: Record<string, number>;
  managed_file_count: number;
  managed_file_bytes: number;
  shared_file_count: number;
  external_reference_count: number;
  workflow_cache_file_count: number;
  backup_file_count: number;
  consequences: string[];
};

export type ProviderState = {
  configured: boolean;
  id?: number | null;
  name?: string;
  provider_type?: string;
  base_url?: string;
  text_model?: string;
  image_model?: string;
  video_model?: string;
  capabilities?: string[];
  description?: string;
  api_key_masked?: string;
  api_key_error?: string;
  enabled?: boolean;
  student_selectable?: boolean;
  last_test_status?: "untested" | "success" | "failed";
  last_test_message?: string;
  last_tested_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  usage_count?: number;
  routed_capabilities?: ProviderCapability[];
  provider_count?: number;
  requires_api_key?: boolean;
};

export type ProviderCapability = "text" | "image" | "video";

export type ProviderRouteMap = Record<ProviderCapability, number[]>;

export type ProviderModelOption = {
  id: string;
  name: string;
  durations?: number[];
  supports_image?: boolean;
};

export type ProviderPreset = {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  text_model: string;
  image_model: string;
  video_model: string;
  capabilities: string[];
  requires_api_key?: boolean;
  models: Record<ProviderCapability, ProviderModelOption[]>;
  description: string;
};

export type SystemModelOption = {
  key: string;
  provider_id: number | null;
  provider_type: string;
  provider_name: string;
  configuration_name: string;
  capability: ProviderCapability;
  model: string;
  display_name: string;
  configured: boolean;
  available: boolean;
  student_selectable?: boolean;
  parameters?: { durations?: number[]; supports_image?: boolean };
  reason: string;
};

export type WorkflowRunResponse = {
  run_id: number;
  status: string;
  output: {
    text?: string;
    refined_prompt?: string;
    output_title?: string;
    image?: {
      url?: string;
      file_path?: string;
      moderation_status?: "approved" | "pending" | "rejected";
      moderation_reason?: string;
      moderation_message?: string;
    };
    images?: Array<{
      url?: string;
      file_path?: string;
      moderation_status?: "approved" | "pending" | "rejected";
      moderation_reason?: string;
      moderation_message?: string;
    }>;
    terminal_outputs?: Array<{
      node_id: string;
      label: string;
      type: string;
      status: string;
      output?: unknown;
      error?: string;
    }>;
  };
  project?: Project | null;
};

export type WorkflowTemplate = {
  id: string;
  name: string;
  description: string;
  nodes: { id: string; type: string; label: string }[];
};

export type SavedWorkflow = {
  id: number;
  name: string;
  description: string;
  owner_user_id?: number | null;
  owner_name: string;
  classroom_id?: number | null;
  classroom_name: string;
  status: string;
  version: number;
  definition: {
    nodes: Array<{ id: string; type: string; label: string; params?: Record<string, unknown>; position?: { x: number; y: number } }>;
    edges: Array<{ id: string; source: string; target: string }>;
  };
};

export type WorkflowRunItem = {
  id: number;
  workflow_id?: number | null;
  status: string;
  input_json: string;
  output_json: string;
  node_states_json: string;
  node_states: Record<string, { status: string; label: string; type: string; output?: unknown; error?: string; cached?: boolean }>;
  error_message: string;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
};

export type ProviderFormValues = {
  name: string;
  provider_type: string;
  base_url: string;
  api_key: string;
  text_model: string;
  image_model: string;
  video_model: string;
  enabled: boolean;
  student_selectable: boolean;
};

export type AppMode = "launch" | "student-login" | "student-password-change" | "student" | "teacher-login" | "teacher-password-change" | "teacher" | "admin";
export type ActivePage = "student" | "courses" | "workflow" | "projects" | "teacher";
export type TeacherSectionKey = "overview" | "classes" | "students" | "courses" | "schedules" | "submissions" | "moderation" | "account";
export type AdminSectionKey = "overview" | "accounts" | "models" | "privacy" | "security" | "operations" | "extensions" | "teaching" | "classes" | "students" | "courses" | "schedules" | "submissions" | "moderation" | "workflows" | "projects";
