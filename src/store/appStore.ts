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
    set({ ...emptyWorkspace, studentProfile: null, loading: false, hydrated: false, error: "", activeRole: null });
  },

  refresh: async (role) => {
    const sequence = ++refreshSequence;
    set((state) => ({
      loading: true,
      error: "",
      hydrated: state.activeRole === role ? state.hydrated : false,
      activeRole: role,
    }));
    try {
      if (role === "admin") {
        const [projectRes, providerRes, usageRes, courseRes, lessonRes, taskRes, assetRes, submissionRes, videoTaskRes, classroomRes, studentRes, packageRes, scheduleRes] = await Promise.all([
          api.get("/api/projects"),
          api.get("/api/settings/provider"),
          api.get("/api/usage"),
          api.get("/api/courses"),
          api.get("/api/lessons"),
          api.get("/api/classes/tasks"),
          api.get("/api/assets"),
          api.get("/api/submissions"),
          api.get("/api/video/tasks"),
          api.get("/api/classrooms"),
          api.get("/api/students"),
          api.get("/api/course-packages"),
          api.get("/api/course-schedules"),
        ]);
        if (sequence !== refreshSequence) return;
        set({
          projects: projectRes.data.projects || [],
          classrooms: classroomRes.data.classrooms || [],
          students: studentRes.data.students || [],
          courses: courseRes.data.courses || [],
          lessons: lessonRes.data.lessons || [],
          classTasks: taskRes.data.tasks || [],
          coursePackages: packageRes.data.packages || [],
          courseSchedules: scheduleRes.data.schedules || [],
          assets: assetRes.data.assets || [],
          submissions: submissionRes.data.submissions || [],
          videoTasks: videoTaskRes.data.tasks || [],
          provider: providerRes.data.provider || { configured: false },
          usage: usageRes.data.usage || [],
          loading: false,
          hydrated: true,
          error: "",
          activeRole: role,
        });
        return;
      }
      const [projectRes, providerStatusRes, courseRes, lessonRes, taskRes, assetRes, submissionRes, videoTaskRes, packageRes, scheduleRes] = await Promise.all([
        api.get("/api/projects"),
        api.get("/api/settings/provider-status"),
        api.get("/api/courses"),
        api.get("/api/lessons"),
        api.get("/api/classes/tasks"),
        api.get("/api/assets"),
        api.get("/api/submissions"),
        api.get("/api/video/tasks"),
        api.get("/api/course-packages"),
        api.get("/api/course-schedules"),
      ]);
      const teacherData = role === "teacher"
        ? await Promise.all([
            api.get("/api/classrooms"),
            api.get("/api/students"),
          ])
        : null;
      if (sequence !== refreshSequence) return;
      set({
        projects: projectRes.data.projects || [],
        provider: providerStatusRes.data.provider,
        courses: courseRes.data.courses || [],
        lessons: lessonRes.data.lessons || [],
        classTasks: taskRes.data.tasks || [],
        coursePackages: packageRes.data.packages || [],
        courseSchedules: scheduleRes.data.schedules || [],
        assets: assetRes.data.assets || [],
        submissions: submissionRes.data.submissions || [],
        videoTasks: videoTaskRes.data.tasks || [],
        usage: [],
        classrooms: teacherData ? teacherData[0].data.classrooms || [] : [],
        students: teacherData ? teacherData[1].data.students || [] : [],
        loading: false,
        hydrated: true,
        error: "",
      });
    } catch (error) {
      if (sequence !== refreshSequence) return;
      set({ loading: false, error: explainError(error) });
      throw error;
    }
  },
}));
