import { App as AntApp, Button, ConfigProvider, Layout, Menu, Modal, Space, Tag, Typography } from "antd";
import dayjs from "dayjs";
import timezone from "dayjs/plugin/timezone";
import utc from "dayjs/plugin/utc";
import {
  BookOpen,
  Bot,
  Boxes,
  CalendarClock,
  ClipboardCheck,
  DatabaseBackup,
  Download,
  FileText,
  GraduationCap,
  Image as ImageIcon,
  KeyRound,
  LayoutDashboard,
  Library,
  LockKeyhole,
  LogOut,
  Plug,
  School,
  Settings,
  ShieldCheck,
  UserRoundCog,
  UsersRound,
  Video,
  WifiOff,
  Workflow,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { WorkspaceError, WorkspaceSkeleton } from "./components/PageState";
import type { AdminSectionKey, AppMode, TeacherSectionKey } from "./domain-types";
import {
  LaunchScreen,
  StudentLoginScreen,
  StudentPasswordChangeScreen,
  TeacherLoginScreen,
  TeacherPasswordChangeScreen,
} from "./features/auth/AuthScreens";
import { PrivacyPolicyPage } from "./features/privacy/PrivacyPolicyPage";
import { ProjectLibrary } from "./features/projects/ProjectLibrary";
import { StudentWorkspace, type StudentWorkspaceView } from "./features/student/StudentWorkspace";
import { StudentCourseReader } from "./features/courses/StudentCourseReader";
import { StudentSubmissionVersionPage } from "./features/courses/StudentSubmissionVersionPage";
import { StudentAgentPage } from "./features/student/StudentAgentPage";
import { AdminPanel } from "./features/teacher/AdminPanel";
import { TeacherPanel } from "./features/teacher/TeacherPanel";
import { WorkflowBuilder } from "./features/workflows/WorkflowBuilder";
import {
  api,
  clearStudentAuth,
  clearTeacherAuth,
  getAuthValue,
  loadStudentProfile,
  loadTeacherProfile,
  storeStudentAuth,
  storeTeacherAuth,
  STUDENT_PASSWORD_CHANGE_REQUIRED_KEY,
  STUDENT_TOKEN_KEY,
  TEACHER_ACCESS_EXPIRES_KEY,
  TEACHER_PASSWORD_CHANGE_REQUIRED_KEY,
  TEACHER_REFRESH_TOKEN_KEY,
  TEACHER_WEAK_PASSWORD_SESSION_KEY,
} from "./lib/api";
import { allowedToolsFromTasks } from "./lib/domain";
import { checkDesktopUpdate, compareVersions, currentClientVersion, installDesktopUpdate } from "./lib/desktopRuntime";
import { isTransientConnectionError } from "./lib/errors";
import { useAppStore, type WorkspaceRole } from "./store/appStore";
import type { StudentAuth, TeacherAuth } from "./types";


const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

dayjs.extend(utc);
dayjs.extend(timezone);

type StudentPage = "workspace" | "courses" | "workflows" | "projects" | "submission-version";
type TeacherPage = TeacherSectionKey | "workflows" | "projects";
type AdminPage = AdminSectionKey;
type WorkspacePage = StudentPage | TeacherPage | AdminPage;
type ScheduleView = "create" | "records";
type StudentWorkspaceRoute = StudentWorkspaceView | "workflow" | "agent";

const studentPages = new Set<StudentPage>(["workspace", "courses", "workflows", "projects", "submission-version"]);
const studentWorkspaceRoutes = new Set<StudentWorkspaceRoute>(["notifications", "materials", "text", "image", "video", "workflow", "agent"]);
const teacherPages = new Set<TeacherPage>(["overview", "classes", "students", "courses", "schedules", "submissions", "moderation", "account", "workflows", "projects"]);
const adminPages = new Set<AdminPage>([
  "overview", "accounts", "models", "privacy", "security", "operations", "extensions",
  "teaching", "classes", "students", "courses", "schedules", "submissions", "moderation", "workflows", "projects",
]);

function routeState(pathname: string): { mode: AppMode; page: WorkspacePage } {
  if (pathname === "/student/login") return { mode: "student-login", page: "workspace" };
  if (pathname === "/student/change-password") return { mode: "student-password-change", page: "workspace" };
  if (pathname === "/teacher/login") return { mode: "teacher-login", page: "overview" };
  if (pathname === "/teacher/change-password") return { mode: "teacher-password-change", page: "overview" };
  if (pathname.startsWith("/student/submissions/")) return { mode: "student", page: "submission-version" };
  if (pathname.startsWith("/student/")) {
    const candidate = pathname.split("/")[2] as StudentPage;
    return { mode: "student", page: studentPages.has(candidate) ? candidate : "workspace" };
  }
  if (pathname.startsWith("/teacher/")) {
    const candidate = pathname.split("/")[2] as TeacherPage;
    return { mode: "teacher", page: teacherPages.has(candidate) ? candidate : "overview" };
  }
  if (pathname.startsWith("/admin/")) {
    const candidate = pathname.split("/")[2] as AdminPage;
    return { mode: "admin", page: adminPages.has(candidate) ? candidate : "overview" };
  }
  return { mode: "launch", page: "workspace" };
}

const pageTitles: Record<string, string> = {
  workspace: "学习工作台",
  courses: "课程学习",
  workflows: "工作流",
  projects: "作品库",
  overview: "总览",
  teaching: "教学总览",
  classes: "班级管理",
  students: "学员管理",
  schedules: "排课管理",
  submissions: "作业批改",
  moderation: "安全检测",
  account: "账号安全",
  accounts: "账号管理",
  models: "模型服务",
  privacy: "隐私政策",
  security: "账号安全",
  operations: "运维备份",
  extensions: "扩展授权",
  "submission-version": "提交版本",
};

export default function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const returningToLaunch = useRef(false);
  const [weakPasswordPromptOpen, setWeakPasswordPromptOpen] = useState(false);
  const [staffAuthReady, setStaffAuthReady] = useState(false);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [updateState, setUpdateState] = useState<{ version: string; required: boolean; body: string } | null>(null);
  const [updateProgress, setUpdateProgress] = useState<number | null>(null);
  const { mode, page } = routeState(location.pathname);
  const {
    projects,
    classrooms,
    students,
    classTasks,
    coursePackages,
    courseSchedules,
    assets,
    submissions,
    videoTasks,
    studentProfile,
    provider,
    usage,
    loading,
    hydrated,
    error,
    setStudentProfile,
    refresh,
    clearWorkspace,
  } = useAppStore();

  const studentAllowedTools = useMemo(() => allowedToolsFromTasks(classTasks), [classTasks]);
  const teacherStudentProjects = useMemo(() => projects.filter((project) => project.user_id != null), [projects]);
  const teacherProfile = loadTeacherProfile();
  const staffMode = mode === "teacher" || mode === "admin";
  const adminTeachingActive = mode === "admin" && ["teaching", "classes", "students", "courses", "schedules", "submissions", "moderation", "workflows", "projects"].includes(page as string);
  const workspaceRole: WorkspaceRole | null = mode === "student" ? "student" : mode === "teacher" ? "teacher" : mode === "admin" ? "admin" : null;
  const scheduleView: ScheduleView = location.pathname.split("/")[3] === "create" ? "create" : "records";
  const studentWorkspaceCandidate = location.pathname.split("/")[3] as StudentWorkspaceRoute | undefined;
  const studentWorkspaceView: StudentWorkspaceRoute = studentWorkspaceCandidate && studentWorkspaceRoutes.has(studentWorkspaceCandidate)
    ? studentWorkspaceCandidate
    : "notifications";
  const selectedCoursePackageId = useMemo(() => {
    if (page !== "courses") return null;
    const raw = new URLSearchParams(location.search).get("package");
    if (!raw || !/^\d+$/.test(raw)) return null;
    const packageId = Number(raw);
    return coursePackages.some((item) => item.id === packageId) ? packageId : null;
  }, [coursePackages, location.search, page]);
  const selectedMenuKey = mode === "student" && page === "workspace"
    ? `workspace-${studentWorkspaceView}`
    : mode === "student" && page === "submission-version"
      ? "courses"
    : selectedCoursePackageId !== null
    ? `course-package-${selectedCoursePackageId}`
    : page === "schedules"
      ? `schedules-${scheduleView}`
      : page;

  useEffect(() => {
    const updateOnline = () => setOnline(navigator.onLine);
    window.addEventListener("online", updateOnline);
    window.addEventListener("offline", updateOnline);
    return () => {
      window.removeEventListener("online", updateOnline);
      window.removeEventListener("offline", updateOnline);
    };
  }, []);

  useEffect(() => {
    const inspectVersion = async () => {
      try {
        const [versionResponse, currentVersion, update] = await Promise.all([
          api.get("/api/version"),
          currentClientVersion(),
          checkDesktopUpdate().catch(() => null),
        ]);
        const minimumVersion = String(versionResponse.data.minimum_client_version || "");
        const required = Boolean(minimumVersion && compareVersions(currentVersion, minimumVersion) < 0);
        if (update || required) {
          setUpdateState({
            version: update?.version || minimumVersion,
            required,
            body: update?.body || (required ? "当前客户端版本已停止访问，请安装管理员发布的新版本。" : ""),
          });
        }
      } catch {
        // Network state is presented separately; version checks retry on the next launch.
      }
    };
    void inspectVersion();
  }, []);

  useEffect(() => {
    const authTitle = mode === "teacher-password-change" || mode === "student-password-change"
      ? "修改密码"
      : mode === "teacher-login"
          ? "教师与管理员登录"
          : mode === "student-login"
            ? "学生登录"
            : "CoderAI 学堂";
    document.title = location.pathname === "/privacy"
      ? "隐私与数据保护 - CoderAI 学堂"
      : mode === "launch"
        ? "CoderAI 学堂"
        : `${pageTitles[page] || authTitle} - CoderAI 学堂`;
    document.getElementById("main-content")?.scrollTo({ top: 0, left: 0, behavior: "auto" });
    const timer = window.setTimeout(() => {
      const heading = document.querySelector<HTMLElement>(".content h2, .launchScreen h1, .launchScreen h2, .privacyPolicyPage h2");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus();
      }
    });
    return () => window.clearTimeout(timer);
  }, [hydrated, location.pathname, location.search, mode, page]);

  useEffect(() => {
    const bodyClass = "workspaceScrollLocked";
    document.body.classList.toggle(bodyClass, workspaceRole !== null);
    return () => document.body.classList.remove(bodyClass);
  }, [workspaceRole]);

  useEffect(() => {
    if (
      (mode === "teacher" || mode === "admin")
      && page === "schedules"
      && location.pathname.split("/").length < 4
    ) {
      navigate(`/${mode}/schedules/records`, { replace: true });
    }
  }, [location.pathname, mode, navigate, page]);

  useEffect(() => {
    if (returningToLaunch.current) {
      if (mode === "launch") returningToLaunch.current = false;
      return;
    }
    if (mode === "student" && !getAuthValue(STUDENT_TOKEN_KEY)) {
      navigate("/student/login", { replace: true });
      return;
    }
    if (mode === "student-password-change" && !getAuthValue(STUDENT_TOKEN_KEY)) {
      navigate("/student/login", { replace: true });
      return;
    }
    if (staffMode && !getAuthValue(TEACHER_REFRESH_TOKEN_KEY)) {
      navigate("/teacher/login", { replace: true });
      return;
    }
    if (mode === "teacher-password-change" && !getAuthValue(TEACHER_REFRESH_TOKEN_KEY)) {
      navigate("/teacher/login", { replace: true });
      return;
    }
    if (staffMode && localStorage.getItem(TEACHER_PASSWORD_CHANGE_REQUIRED_KEY) === "true") {
      navigate("/teacher/change-password", { replace: true });
      return;
    }
    if (mode === "student" && localStorage.getItem(STUDENT_PASSWORD_CHANGE_REQUIRED_KEY) === "true") {
      navigate("/student/change-password", { replace: true });
      return;
    }
    if (mode === "admin" && teacherProfile?.role !== "admin") {
      navigate("/teacher/overview", { replace: true });
      return;
    }
    if (mode === "teacher" && teacherProfile?.role === "admin") {
      navigate("/admin/overview", { replace: true });
      return;
    }
    if (workspaceRole && (!staffMode || staffAuthReady)) {
      if (mode === "student" && !studentProfile) setStudentProfile(loadStudentProfile());
      void refresh(workspaceRole).catch(() => undefined);
    }
  }, [mode, navigate, refresh, setStudentProfile, staffAuthReady, staffMode, studentProfile, teacherProfile?.role, workspaceRole]);

  useEffect(() => {
    if (!staffMode) {
      setStaffAuthReady(false);
      return;
    }
    const refreshAuthIfNeeded = async () => {
      const refreshToken = getAuthValue(TEACHER_REFRESH_TOKEN_KEY);
      const accessExpiresAt = localStorage.getItem(TEACHER_ACCESS_EXPIRES_KEY);
      if (!refreshToken) {
        clearTeacherAuth();
        navigate("/teacher/login", { replace: true });
        return;
      }
      if (accessExpiresAt && dayjs(accessExpiresAt).diff(dayjs(), "minute") >= 30) {
        setStaffAuthReady(true);
        return;
      }
      try {
        const response = await api.post("/api/auth/teacher-refresh", { refresh_token: refreshToken });
        const nextAuth = response.data as TeacherAuth;
        storeTeacherAuth(nextAuth);
        if (nextAuth.password_change_required) {
          clearWorkspace();
          navigate("/teacher/change-password", { replace: true });
          return;
        }
        setStaffAuthReady(true);
      } catch (error) {
        if (isTransientConnectionError(error)) {
          setStaffAuthReady(true);
          return;
        }
        clearTeacherAuth();
        clearWorkspace();
        navigate("/teacher/login", { replace: true });
      }
    };
    void refreshAuthIfNeeded();
    const timer = window.setInterval(() => void refreshAuthIfNeeded(), 5 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [clearWorkspace, navigate, staffMode]);

  useEffect(() => {
    if (mode !== "student" || !hydrated) return;
    if (page === "workflows") {
      navigate(
        studentAllowedTools.has("workflow")
          ? "/student/workspace/workflow"
          : "/student/workspace/notifications",
        { replace: true },
      );
      return;
    }
    if (page !== "workspace") return;
    const requestedView = location.pathname.split("/")[3] as StudentWorkspaceRoute | undefined;
    const toolRequired = requestedView === "text" || requestedView === "image" || requestedView === "video" || requestedView === "workflow" || requestedView === "agent";
    const requestedTool = requestedView === "agent" ? "text" : requestedView;
    if (
      !requestedView
      || !studentWorkspaceRoutes.has(requestedView)
      || (toolRequired && !studentAllowedTools.has(requestedTool as string))
    ) {
      navigate("/student/workspace/notifications", { replace: true });
    }
  }, [hydrated, location.pathname, mode, navigate, page, studentAllowedTools]);

  useEffect(() => {
    if (!staffMode || teacherProfile?.role !== "teacher") {
      setWeakPasswordPromptOpen(false);
      return;
    }
    const weakSession = sessionStorage.getItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
    setWeakPasswordPromptOpen(Boolean(weakSession));
  }, [staffMode, teacherProfile?.role]);

  const enterStudent = () => {
    clearTeacherAuth();
    clearStudentAuth();
    clearWorkspace();
    navigate("/student/login");
  };

  const enterStudentWorkspace = (auth: StudentAuth) => {
    clearTeacherAuth();
    storeStudentAuth(auth);
    setStudentProfile(auth.student);
    navigate(auth.password_change_required ? "/student/change-password" : "/student/workspace");
  };

  const enterTeacher = (auth: TeacherAuth, interactiveLogin = true) => {
    clearStudentAuth();
    storeTeacherAuth(auth);
    clearWorkspace();
    setStaffAuthReady(false);
    if (interactiveLogin && auth.user.role === "teacher" && auth.password_is_weak && !auth.password_change_required) {
      sessionStorage.setItem(TEACHER_WEAK_PASSWORD_SESSION_KEY, String(auth.session_id));
      setWeakPasswordPromptOpen(true);
    } else {
      sessionStorage.removeItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
      setWeakPasswordPromptOpen(false);
    }
    const landing = auth.user.role === "admin" ? "/admin/overview" : "/teacher/overview";
    navigate(auth.password_change_required ? "/teacher/change-password" : landing);
  };

  const ignoreWeakPassword = () => {
    sessionStorage.removeItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
    setWeakPasswordPromptOpen(false);
  };

  const resetWeakPassword = () => {
    sessionStorage.removeItem(TEACHER_WEAK_PASSWORD_SESSION_KEY);
    setWeakPasswordPromptOpen(false);
    navigate("/teacher/change-password");
  };

  const backToLaunch = () => {
    returningToLaunch.current = true;
    navigate("/", { replace: true });
    clearTeacherAuth();
    clearStudentAuth();
    clearWorkspace();
  };

  const leaveWorkspace = async () => {
    const refreshToken = getAuthValue(TEACHER_REFRESH_TOKEN_KEY);
    const studentToken = getAuthValue(STUDENT_TOKEN_KEY);
    if (staffMode && refreshToken) {
      try {
        await api.post("/api/auth/teacher-logout", { refresh_token: refreshToken });
      } catch {
        // Credentials must still be removed when the API is unavailable.
      }
    }
    if (mode === "student" && studentToken) {
      try {
        await api.post("/api/auth/student-logout");
      } catch {
        // Credentials must still be removed when the API is unavailable.
      }
    }
    backToLaunch();
  };

  const retry = () => {
    if (workspaceRole) void refresh(workspaceRole);
  };

  const renderPage = () => {
    if (error && !hydrated) return <WorkspaceError message={error} onRetry={retry} />;
    if (!hydrated) return <WorkspaceSkeleton />;
    return (
      <>
        {error && <WorkspaceError message={error} onRetry={retry} />}
        {mode === "student" && page === "workspace" && studentWorkspaceView !== "workflow" && studentWorkspaceView !== "agent" && (
          <StudentWorkspace
            view={studentWorkspaceView}
            onRefresh={() => refresh("student")}
            provider={provider}
            classTasks={classTasks}
            projects={projects}
            assets={assets}
            submissions={submissions}
            videoTasks={videoTasks}
            studentProfile={studentProfile}
          />
        )}
        {mode === "student" && page === "courses" && <StudentCourseReader packages={coursePackages} schedules={courseSchedules} projects={projects} submissions={submissions} onRefresh={() => refresh("student")} />}
        {mode === "student" && page === "submission-version" && <StudentSubmissionVersionPage />}
        {mode === "student" && page === "workspace" && studentWorkspaceView === "agent" && studentAllowedTools.has("text") && (
          <StudentAgentPage onRefresh={() => refresh("student")} />
        )}
        {mode === "student" && page === "workspace" && studentWorkspaceView === "workflow" && studentAllowedTools.has("workflow") && (
          <WorkflowBuilder onRefresh={() => refresh("student")} provider={provider} classrooms={[]} isTeacher={false} />
        )}
        {mode === "student" && page === "projects" && (
          <ProjectLibrary projects={projects} onRefresh={() => refresh("student")} audience="student" students={students} classrooms={classrooms} />
        )}
        {mode === "teacher" && page === "workflows" && (
          <WorkflowBuilder onRefresh={() => refresh("teacher")} provider={provider} classrooms={classrooms} isTeacher />
        )}
        {mode === "teacher" && page === "projects" && (
          <ProjectLibrary projects={teacherStudentProjects} onRefresh={() => refresh("teacher")} audience="teacher" students={students} classrooms={classrooms} />
        )}
        {mode === "teacher" && page !== "workflows" && page !== "projects" && (
          <TeacherPanel
            section={page as TeacherSectionKey}
            projects={teacherStudentProjects}
            submissions={submissions}
            classrooms={classrooms}
            students={students}
            coursePackages={coursePackages}
            courseSchedules={courseSchedules}
            scheduleView={scheduleView}
            onRefresh={() => refresh("teacher")}
          />
        )}
        {mode === "admin" && page === "workflows" && (
          <WorkflowBuilder onRefresh={() => refresh("admin")} provider={provider} classrooms={classrooms} isTeacher />
        )}
        {mode === "admin" && page === "projects" && (
          <ProjectLibrary projects={teacherStudentProjects} onRefresh={() => refresh("admin")} audience="admin" students={students} classrooms={classrooms} />
        )}
        {mode === "admin" && ["teaching", "classes", "students", "courses", "schedules", "submissions", "moderation"].includes(page as string) && (
          <TeacherPanel
            audience="admin"
            section={(page === "teaching" ? "overview" : page) as TeacherSectionKey}
            projects={teacherStudentProjects}
            submissions={submissions}
            classrooms={classrooms}
            students={students}
            coursePackages={coursePackages}
            courseSchedules={courseSchedules}
            scheduleView={scheduleView}
            onRefresh={() => refresh("admin")}
          />
        )}
        {mode === "admin" && !["teaching", "classes", "students", "courses", "schedules", "submissions", "moderation", "workflows", "projects"].includes(page as string) && (
          <AdminPanel
            section={page as AdminSectionKey}
            provider={provider}
            usage={usage}
            classrooms={classrooms}
            onRefresh={() => refresh("admin")}
          />
        )}
      </>
    );
  };

  const studentMenu = [
    {
      key: "student-workspace-root",
      icon: <Bot size={18} />,
      label: "学习工作台",
      children: [
        { key: "workspace-notifications", icon: <ClipboardCheck size={17} />, label: "课堂通知" },
        { key: "workspace-materials", icon: <Library size={17} />, label: "课堂素材" },
        ...(studentAllowedTools.has("text") ? [{ key: "workspace-text", icon: <FileText size={17} />, label: "文字生成" }] : []),
        ...(studentAllowedTools.has("image") ? [{ key: "workspace-image", icon: <ImageIcon size={17} />, label: "图片生成" }] : []),
        ...(studentAllowedTools.has("video") ? [{ key: "workspace-video", icon: <Video size={17} />, label: "视频生成" }] : []),
        ...(studentAllowedTools.has("workflow") ? [{ key: "workspace-workflow", icon: <Workflow size={17} />, label: "工作流生成" }] : []),
        ...(studentAllowedTools.has("text") ? [{ key: "workspace-agent", icon: <Bot size={17} />, label: "AI 助手" }] : []),
      ],
    },
    { key: "courses", icon: <BookOpen size={18} />, label: "课程学习" },
    { key: "projects", icon: <Library size={18} />, label: "我的作品" },
  ];
  const courseMenuChildren = [
    {
      key: "courses",
      icon: <BookOpen size={16} />,
      label: mode === "admin" ? "课程包管理" : "课程概览",
    },
    ...coursePackages.map((coursePackage) => ({
      key: `course-package-${coursePackage.id}`,
      label: (
        <span className="courseMenuLabel" title={coursePackage.title}>
          <span className="courseMenuTitle">{coursePackage.title}</span>
          {mode === "admin" && (
            <span className={`courseMenuStatus ${coursePackage.status}`}>
              {coursePackage.status === "published" ? "已发布" : coursePackage.status === "archived" ? "已归档" : "草稿"}
            </span>
          )}
        </span>
      ),
    })),
  ];
  const teacherMenu = [
    {
      key: "teaching",
      icon: <School size={18} />,
      label: "教学管理",
      children: [
        { key: "overview", icon: <LayoutDashboard size={17} />, label: "教学总览" },
        { key: "classes", icon: <GraduationCap size={17} />, label: "班级管理" },
        { key: "students", icon: <UsersRound size={17} />, label: "学员管理" },
        { key: "courses-root", icon: <BookOpen size={17} />, label: "课程管理", children: courseMenuChildren },
        {
          key: "schedules-root",
          icon: <CalendarClock size={17} />,
          label: "排课管理",
          children: [
            { key: "schedules-create", label: "新建排课" },
            { key: "schedules-records", label: "排课记录" },
          ],
        },
        { key: "submissions", icon: <ClipboardCheck size={17} />, label: "作业批改" },
        { key: "moderation", icon: <ShieldCheck size={17} />, label: "安全检测" },
      ],
    },
    {
      key: "creation",
      icon: <Boxes size={18} />,
      label: "教学创作",
      children: [
        { key: "workflows", icon: <Workflow size={17} />, label: "工作流" },
        { key: "projects", icon: <Library size={17} />, label: "学员作品" },
      ],
    },
    { key: "account", icon: <LockKeyhole size={18} />, label: "账号安全" },
  ];
  const adminMenu = [
    {
      key: "teaching-management",
      icon: <School size={18} />,
      label: "教学管理",
      children: [
        { key: "teaching", icon: <LayoutDashboard size={17} />, label: "教学总览" },
        { key: "classes", icon: <GraduationCap size={17} />, label: "班级管理" },
        { key: "students", icon: <UsersRound size={17} />, label: "学员管理" },
        { key: "courses-root", icon: <BookOpen size={17} />, label: "课程管理", children: courseMenuChildren },
        {
          key: "schedules-root",
          icon: <CalendarClock size={17} />,
          label: "排课管理",
          children: [
            { key: "schedules-create", label: "新建排课" },
            { key: "schedules-records", label: "排课记录" },
          ],
        },
        { key: "submissions", icon: <ClipboardCheck size={17} />, label: "作业批改" },
        { key: "moderation", icon: <ShieldCheck size={17} />, label: "安全检测" },
        { key: "workflows", icon: <Workflow size={17} />, label: "工作流" },
        { key: "projects", icon: <Library size={17} />, label: "学员作品" },
      ],
    },
    {
      key: "system",
      icon: <Settings size={18} />,
      label: "系统管理",
      children: [
        { key: "overview", icon: <LayoutDashboard size={17} />, label: "系统总览" },
        { key: "accounts", icon: <UserRoundCog size={17} />, label: "账号管理" },
        { key: "models", icon: <KeyRound size={17} />, label: "模型服务" },
        { key: "privacy", icon: <ShieldCheck size={17} />, label: "隐私政策" },
      ],
    },
    {
      key: "maintenance",
      icon: <DatabaseBackup size={18} />,
      label: "系统维护",
      children: [
        { key: "operations", icon: <DatabaseBackup size={17} />, label: "运维与备份" },
        { key: "extensions", icon: <Plug size={17} />, label: "扩展与授权" },
      ],
    },
    { key: "security", icon: <LockKeyhole size={18} />, label: "账号安全" },
  ];

  const workspace = workspaceRole ? (
    <>
      <a
        className="skipLink"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主要内容
      </a>
      <Layout className={`shell ${mode === "student" ? "studentShell" : mode === "admin" ? "adminShell" : "teacherShell"}`}>
        <Sider width={248} className="sidebar">
          <div className="brand">
            <div className="brandMark">AI</div>
            <div>
              <Title level={4}>CoderAI 学堂</Title>
              <Text>{mode === "student" ? "学生创作空间" : mode === "admin" ? "机构管理中心" : "教师教学空间"}</Text>
            </div>
          </div>
          <Menu
            mode="inline"
            selectedKeys={[selectedMenuKey]}
            defaultOpenKeys={mode === "student" ? ["student-workspace-root"] : mode === "teacher" ? ["teaching", "courses-root", "schedules-root", "creation"] : mode === "admin" ? ["teaching-management", "courses-root", "schedules-root", "system", "maintenance"] : []}
            onClick={({ key }) => {
              if (mode === "student" && key.startsWith("workspace-")) {
                navigate(`/student/workspace/${key.slice("workspace-".length)}`);
                return;
              }
              if (key.startsWith("course-package-")) {
                navigate(`/${mode}/courses?package=${key.slice("course-package-".length)}`);
                return;
              }
              if (key === "schedules-create" || key === "schedules-records") {
                navigate(`/${mode}/schedules/${key.slice("schedules-".length)}`);
                return;
              }
              navigate(`/${mode}/${key}`);
            }}
            items={mode === "student" ? studentMenu : mode === "teacher" ? teacherMenu : adminMenu}
          />
          <div className="sidebarFooter">
            <Space direction="vertical" size={8} className="fullWidth">
              <Button icon={<ShieldCheck size={16} />} onClick={() => navigate("/privacy")} block>隐私与数据保护</Button>
              <Button icon={<LogOut size={16} />} onClick={() => void leaveWorkspace()} block>退出当前账号</Button>
            </Space>
          </div>
        </Sider>
        <Layout className="workspaceMain">
          <Header className="topbar">
            <div className="topbarTitle">
              <Text strong>{mode === "student" ? "AI 创作课堂" : mode === "admin" ? (adminTeachingActive ? "全机构教学管理" : "系统管理中心") : "教学管理中心"}</Text>
              <Text type="secondary">
                {mode === "student" ? "任务、生成工具与作品" : mode === "admin" ? (adminTeachingActive ? "班级、学员、课程、作业与安全" : "账号、模型、运维与授权") : "获授权班级、学员、任务与批改"}
              </Text>
            </div>
            <Space size={12} wrap>
              {!online && <Tag color="error" icon={<WifiOff size={14} />}>网络已断开</Tag>}
              <Tag className="workspaceIdentityChip" color={mode === "admin" ? "purple" : mode === "teacher" ? "cyan" : "blue"}>
                {mode === "student"
                  ? `${studentProfile?.name || "学生"}${studentProfile?.username ? ` · ${studentProfile.username}` : ""}`
                  : `${teacherProfile?.name || "教师"} · ${teacherProfile?.username || ""}`}
              </Tag>
            </Space>
          </Header>
          <Content id="main-content" className="content" tabIndex={-1}>{renderPage()}</Content>
        </Layout>
      </Layout>
    </>
  ) : null;

  return (
    <ConfigProvider
      theme={{
        token: {
          borderRadius: 8,
          colorPrimary: "#2563eb",
          colorSuccess: "#15803d",
          colorWarning: "#d97706",
          colorError: "#b4232c",
          fontFamily: "Inter, 'Microsoft YaHei', system-ui, sans-serif",
        },
      }}
    >
      <AntApp>
        <Modal
          open={Boolean(updateState)}
          title={updateState?.required ? "需要更新后继续使用" : "发现新版本"}
          closable={!updateState?.required && updateProgress === null}
          maskClosable={false}
          keyboard={!updateState?.required}
          onCancel={() => !updateState?.required && setUpdateState(null)}
          footer={[
            !updateState?.required && (
              <Button key="later" disabled={updateProgress !== null} onClick={() => setUpdateState(null)}>稍后更新</Button>
            ),
            <Button
              key="install"
              type="primary"
              icon={<Download size={16} />}
              loading={updateProgress !== null}
              onClick={() => {
                setUpdateProgress(0);
                void installDesktopUpdate(setUpdateProgress).catch(() => setUpdateProgress(null));
              }}
            >
              {updateProgress === null ? `安装 ${updateState?.version || "新版本"}` : `正在更新 ${updateProgress}%`}
            </Button>,
          ].filter(Boolean)}
        >
          <Typography.Paragraph>{updateState?.body || "新版本已准备好，安装完成后应用会自动重新启动。"}</Typography.Paragraph>
        </Modal>
        <Modal
          open={weakPasswordPromptOpen && mode === "teacher"}
          title="当前教师密码安全性较弱"
          closable={false}
          maskClosable={false}
          keyboard={false}
          footer={[
            <Button key="ignore" onClick={ignoreWeakPassword}>本次登录忽略</Button>,
            <Button key="reset" type="primary" onClick={resetWeakPassword}>重新设置密码</Button>,
          ]}
        >
          <Typography.Paragraph>
            当前密码少于 10 位，或未同时包含字母和数字。账号仍可继续使用，建议在本次登录中完成修改。
          </Typography.Paragraph>
        </Modal>
        <Routes>
          <Route path="/" element={<LaunchScreen onStudent={enterStudent} onTeacher={() => navigate("/teacher/login")} onPrivacy={() => navigate("/privacy")} />} />
          <Route path="/privacy" element={<PrivacyPolicyPage onBack={() => navigate(-1)} />} />
          <Route path="/student/login" element={<StudentLoginScreen onBack={backToLaunch} onSuccess={enterStudentWorkspace} />} />
          <Route path="/student/change-password" element={<StudentPasswordChangeScreen onBack={backToLaunch} onSuccess={enterStudentWorkspace} />} />
          <Route path="/teacher/login" element={<TeacherLoginScreen onBack={backToLaunch} onSuccess={enterTeacher} />} />
          <Route path="/teacher/change-password" element={<TeacherPasswordChangeScreen onBack={backToLaunch} onSuccess={(auth) => enterTeacher(auth, false)} />} />
          <Route path="/student/:page" element={workspace} />
          <Route path="/student/:page/:subpage" element={workspace} />
          <Route path="/student/submissions/:submissionId/versions/:versionId" element={workspace} />
          <Route path="/teacher/:page" element={workspace} />
          <Route path="/teacher/:page/:subpage" element={workspace} />
          <Route path="/admin/:page" element={workspace} />
          <Route path="/admin/:page/:subpage" element={workspace} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AntApp>
    </ConfigProvider>
  );
}
