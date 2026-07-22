import type { SchoolStage } from "./domain-types";


export type StudentProfile = {
  id: number;
  name: string;
  role: string;
  username: string;
  classroom_id?: number | null;
  classroom_name?: string;
  password_change_required: boolean;
  registered_at?: string | null;
  last_login_at?: string | null;
  age_level: SchoolStage;
  school_stage_label: string;
  active: boolean;
  account_status: "active" | "disabled" | "archived";
  archived_at?: string | null;
  archived_classroom_name: string;
  created_at: string;
};

export type AccountProfile = {
  id: number;
  organization_id?: number;
  organization_code?: string;
  name: string;
  username: string;
  role: "student" | "teacher" | "admin";
  active: boolean;
  password_change_required: boolean;
  registered_at?: string | null;
  last_login_at?: string | null;
  created_at: string;
};

export type StudentAuth = {
  organization?: { id: number; code: string; name: string };
  token: string;
  session_id: number;
  expires_at: string;
  password_change_required: boolean;
  registration_required?: boolean;
  student: StudentProfile;
};

export type TeacherAuth = {
  organization?: { id: number; code: string; name: string };
  token: string;
  refresh_token: string;
  session_id: number;
  access_expires_at: string;
  expires_at: string;
  password_change_required: boolean;
  password_is_weak?: boolean;
  user: AccountProfile;
};

export type TeacherSessionState = {
  session_id: number;
  device_name: string;
  created_at: string;
  last_seen_at: string;
  access_expires_at: string;
  expires_at: string;
  current: boolean;
  user?: AccountProfile | null;
};

export type StorageStatistics = {
  data_dir: string;
  total_bytes: number;
  total_files: number;
  categories: Array<{ key: string; label: string; path: string; size_bytes: number; file_count: number }>;
};

export type OperationLog = {
  name: string;
  size_bytes: number;
  modified_at: string;
};

export type FileConsistencyItem = {
  id: number;
  title: string;
  file_path: string;
  managed: boolean;
};

export type FileConsistencyScan = {
  scanned_projects: number;
  scanned_assets: number;
  missing_projects: FileConsistencyItem[];
  missing_assets: FileConsistencyItem[];
  orphan_count: number;
  orphan_files: Array<{ file_path: string; size_bytes: number }>;
};
