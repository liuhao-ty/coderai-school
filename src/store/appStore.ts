import axios from "axios";
import { create } from "zustand";

import type { AssetItem, ClassTask, Classroom, Course, CoursePackageItem, CourseScheduleItem, Lesson, Project, ProviderState, TaskSubmission, VideoTask } from "../domain-types";
import { api } from "../lib/api";
import { explainError } from "../lib/errors";
import type { StudentProfile } from "../types";


export type WorkspaceRole = "student" | "teacher" | "admin";

type UsageItem = { feature: string; status: string; count: number };

type AppStore = {
  projects: Project[];
  classrooms: Classroom[];
  students: StudentProfile[];
  courses: Course[];
  lessons: Lesson[];
  classTasks: ClassTask[];
  coursePackages: CoursePackageItem[];
  courseSchedules: CourseScheduleItem[];
  assets: AssetItem[];
  submissions: TaskSubmission[];
  videoTasks: VideoTask[];
  studentProfile: StudentProfile | null;
  provider: ProviderState;
  usage: UsageItem[];
  loading: boolean;
  hydrated: boolean;
  error: string;
  activeRole: WorkspaceRole | null;
  setStudentProfile: (student: StudentProfile | null) => void;
  refresh: (role: WorkspaceRole) => Promise<void>;
  clearWorkspace: () => void;
};

let refreshSequence = 0;
const refreshControllers = new Map<WorkspaceRole, AbortController>();
const inFlightRefreshes = new Map<WorkspaceRole, Promise<void>>();

const emptyWorkspace = {
  projects: [],
  classrooms: [],
  students: [],
  courses: [],
  lessons: [],
  classTasks: [],
  coursePackages: [],
  courseSchedules: [],
  assets: [],
  submissions: [],
  videoTasks: [],
  provider: { configured: false },
  usage: [],
};

export const useAppStore = create<AppStore>((set) => ({
  ...emptyWorkspace,
  studentProfile: null,
  loading: false,
  hydrated: false,
  error: "",
  activeRole: null,

  setStudentProfile: (studentProfile) => set({ studentProfile }),

  clearWorkspace: () => {
    refreshSequence += 1;
    refreshControllers.forEach((controller) => controller.abort());
    refreshControllers.clear();
    inFlightRefreshes.clear();
    set({ ...emptyWorkspace, studentProfile: null, loading: false, hydrated: false, error: "", activeRole: null });
  },

  refresh: (role) => {
    const existing = inFlightRefreshes.get(role);
    if (existing) return existing;
    refreshControllers.forEach((controller, activeRole) => {
      if (activeRole !== role) controller.abort();
    });
    const controller = new AbortController();
    refreshControllers.set(role, controller);
    const sequence = ++refreshSequence;
    set((state) => ({
      ...(state.activeRole === role ? {} : emptyWorkspace),
      loading: true,
      error: "",
      hydrated: state.activeRole === role ? state.hydrated : false,
      activeRole: role,
    }));
    const refreshPromise = (async () => {
      const endpoints = [
        { key: "projects", label: "作品", url: "/api/projects", responseKey: "projects" },
        { key: "provider", label: "模型状态", url: "/api/settings/provider-status", responseKey: "provider" },
        { key: "courses", label: "历史课程", url: "/api/courses", responseKey: "courses" },
        { key: "lessons", label: "历史课时", url: "/api/lessons", responseKey: "lessons" },
        { key: "classTasks", label: "历史任务", url: "/api/classes/tasks", responseKey: "tasks" },
        { key: "assets", label: "素材", url: "/api/assets", responseKey: "assets" },
        { key: "submissions", label: "提交", url: "/api/submissions", responseKey: "submissions" },
        { key: "videoTasks", label: "视频任务", url: "/api/video/tasks", responseKey: "tasks" },
        { key: "coursePackages", label: "课程包", url: "/api/course-packages", responseKey: "packages" },
        { key: "courseSchedules", label: "排课", url: "/api/course-schedules", responseKey: "schedules" },
        ...(role === "teacher" || role === "admin"
          ? [
              { key: "classrooms", label: "班级", url: "/api/classrooms", responseKey: "classrooms" },
              { key: "students", label: "学员", url: "/api/students", responseKey: "students" },
            ]
          : []),
        ...(role === "admin"
          ? [{ key: "usage", label: "用量", url: "/api/usage", responseKey: "usage" }]
          : []),
      ];
      const results = await Promise.allSettled(
        endpoints.map((endpoint) => api.get(endpoint.url, { signal: controller.signal })),
      );
      if (sequence !== refreshSequence || controller.signal.aborted) return;

      const nextState: Record<string, unknown> = {
        loading: false,
        hydrated: true,
        activeRole: role,
      };
      const failedLabels: string[] = [];
      let firstFailure: unknown = null;
      let successfulRequests = 0;
      results.forEach((result, index) => {
        const endpoint = endpoints[index];
        if (result.status === "fulfilled") {
          successfulRequests += 1;
          const value = result.value.data?.[endpoint.responseKey];
          nextState[endpoint.key] = endpoint.key === "provider"
            ? value || { configured: false }
            : value || [];
          return;
        }
        if (!axios.isCancel(result.reason)) {
          failedLabels.push(endpoint.label);
          firstFailure ||= result.reason;
        }
      });
      if (!successfulRequests && firstFailure) {
        const detail = explainError(firstFailure);
        set({ loading: false, error: detail });
        throw firstFailure;
      }
      nextState.error = failedLabels.length
        ? `部分数据暂未更新：${failedLabels.join("、")}。已保留上次成功加载的内容。`
        : "";
      set(nextState as Partial<AppStore>);
    })().finally(() => {
      if (refreshControllers.get(role) === controller) refreshControllers.delete(role);
      if (inFlightRefreshes.get(role) === refreshPromise) inFlightRefreshes.delete(role);
    });
    inFlightRefreshes.set(role, refreshPromise);
    return refreshPromise;
  },
}));
