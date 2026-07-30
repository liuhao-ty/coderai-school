import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const API_URL = "http://127.0.0.1:18000";
const DEFAULT_ADMIN_PASSWORD = "123456";
const ADMIN_PASSWORD = "E2E#Teacher2026";
const TEACHER_PASSWORD = "E2EClass#2027";
const STUDENT_PASSWORD = "bcm123456";
const RUN_ID = Date.now().toString(36);
const TEACHER_USERNAME = `e2e.class.teacher.${RUN_ID}`;
const STUDENT_USERNAME = `class.student.${RUN_ID}`;
const CLASSROOM_NAME = `E2E 闭环班级 ${RUN_ID}`;
const STUDENT_NAME = `E2E 闭环学生 ${RUN_ID}`;
const PACKAGE_TITLE = `E2E AI 创作课程包 ${RUN_ID}`;
const PERSONAL_COURSE_TITLE = `E2E 个人创意课 ${RUN_ID}`;
const CLASS_COURSE_TITLE = `E2E 班级协作课 ${RUN_ID}`;
const PROJECT_TITLE = `E2E 文字作品 ${RUN_ID}`;
const TEACHER_FEEDBACK = "E2E 批改通过，内容完整。";

type AuthPayload = Record<string, unknown> & {
  token: string;
  refresh_token?: string;
  access_expires_at?: string;
  password_change_required?: boolean;
  user?: { id?: number; role?: string };
  student?: Record<string, unknown>;
};

function localDateTime(offsetMinutes = -60) {
  const date = new Date(Date.now() + offsetMinutes * 60_000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function loginAdmin(request: APIRequestContext): Promise<AuthPayload> {
  let response = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username: "admin", password: ADMIN_PASSWORD },
  });
  if (!response.ok()) {
    response = await request.post(`${API_URL}/api/auth/teacher-login`, {
      data: { username: "admin", password: DEFAULT_ADMIN_PASSWORD },
    });
  }
  expect(response.ok()).toBeTruthy();
  let auth = await response.json() as AuthPayload;
  if (auth.password_change_required) {
    const changed = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
      headers: { "X-CoderAI-Teacher-Token": auth.token },
      data: { current_password: DEFAULT_ADMIN_PASSWORD, next_password: ADMIN_PASSWORD },
    });
    expect(changed.ok()).toBeTruthy();
    auth = await changed.json() as AuthPayload;
  }
  return auth;
}

async function seedActors(request: APIRequestContext) {
  const adminAuth = await loginAdmin(request);
  const adminHeaders = { "X-CoderAI-Teacher-Token": adminAuth.token };

  const createdTeacher = await request.post(`${API_URL}/api/accounts/teachers`, {
    headers: adminHeaders,
    data: { name: "E2E 任课教师", username: TEACHER_USERNAME, role: "teacher" },
  });
  expect(createdTeacher.ok()).toBeTruthy();
  const teacherAccount = await createdTeacher.json();
  const temporaryLogin = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username: TEACHER_USERNAME, password: teacherAccount.temporary_password },
  });
  expect(temporaryLogin.ok()).toBeTruthy();
  const temporaryAuth = await temporaryLogin.json() as AuthPayload;
  const changedTeacher = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
    headers: { "X-CoderAI-Teacher-Token": temporaryAuth.token },
    data: { current_password: teacherAccount.temporary_password, next_password: TEACHER_PASSWORD },
  });
  expect(changedTeacher.ok()).toBeTruthy();
  const teacherAuth = await changedTeacher.json() as AuthPayload;
  const teacherHeaders = { "X-CoderAI-Teacher-Token": teacherAuth.token };

  const classroomResponse = await request.post(`${API_URL}/api/classrooms`, {
    headers: teacherHeaders,
    data: { name: CLASSROOM_NAME, grade_level: "mixed" },
  });
  expect(classroomResponse.ok()).toBeTruthy();
  const classroomId = (await classroomResponse.json()).classroom.id as number;

  const studentResponse = await request.post(`${API_URL}/api/students`, {
    headers: adminHeaders,
    data: { name: STUDENT_NAME, username: STUDENT_USERNAME, classroom_id: classroomId },
  });
  expect(studentResponse.ok()).toBeTruthy();

  const studentLogin = await request.post(`${API_URL}/api/auth/student-login`, {
    data: { username: STUDENT_USERNAME, password: STUDENT_PASSWORD },
  });
  expect(studentLogin.ok()).toBeTruthy();
  const studentAuth = await studentLogin.json() as AuthPayload;
  const studentHeaders = { "X-CoderAI-Student-Token": studentAuth.token };

  const projectResponse = await request.post(`${API_URL}/api/projects`, {
    headers: studentHeaders,
    data: { title: PROJECT_TITLE, project_type: "text", summary: "# E2E 完成作品\n\n这是新课程排课闭环中的学生作品。" },
  });
  expect(projectResponse.ok()).toBeTruthy();

  return { adminAuth, adminHeaders, teacherAuth, teacherId: (teacherAccount.account as { id: number }).id, studentAuth };
}

async function setStaffAuth(page: Page, auth: AuthPayload) {
  await page.goto("/");
  await page.evaluate((payload) => {
    localStorage.clear();
    localStorage.setItem("coderai_teacher_token", payload.token);
    localStorage.setItem("coderai_teacher_refresh_token", payload.refresh_token || "");
    localStorage.setItem("coderai_teacher_access_expires_at", payload.access_expires_at || "");
    localStorage.setItem("coderai_teacher_password_change_required", payload.password_change_required ? "true" : "false");
    localStorage.setItem("coderai_teacher_profile", JSON.stringify(payload.user));
  }, auth);
  await page.goto(auth.user?.role === "admin" ? "/#/admin/overview" : "/#/teacher/overview");
  await expect(page.getByRole("heading", { name: auth.user?.role === "admin" ? "系统总览" : "教学总览" })).toBeVisible();
}

async function setStudentAuth(page: Page, auth: AuthPayload) {
  await page.goto("/");
  await page.evaluate((payload) => {
    localStorage.clear();
    localStorage.setItem("coderai_student_token", payload.token);
    localStorage.setItem("coderai_student_profile", JSON.stringify(payload.student));
    localStorage.setItem("coderai_student_password_change_required", payload.password_change_required ? "true" : "false");
  }, auth);
  await page.goto("/#/student/workspace");
  await expect(page.getByRole("heading", { name: "学生AI创作工作台" })).toBeVisible();
}

async function selectAntOption(page: Page, label: string, optionText: string) {
  await page.getByLabel(label, { exact: true }).click();
  await page.locator(".ant-select-item-option").filter({ hasText: optionText }).last().click();
}

async function createSchedule(page: Page, courseTitle: string, targetLabel: "学员" | "班级", targetText: string) {
  await selectAntOption(page, "课程范围", courseTitle);
  await selectAntOption(page, targetLabel, targetText);
  const startsAt = page.locator(".scheduleComposer").getByLabel("开始时间", { exact: true });
  await startsAt.fill(localDateTime());
  await startsAt.press("Enter");
  await page.getByRole("button", { name: "确认排课" }).click();
  await expect(page.getByText("已创建 1 条排课", { exact: true }).last()).toBeVisible();
}

test("管理员建课、教师双模式排课、学生提交和教师批改形成完整闭环", async ({ page, request }) => {
  test.setTimeout(240_000);
  const { adminAuth, adminHeaders, teacherAuth, teacherId, studentAuth } = await seedActors(request);

  await setStaffAuth(page, adminAuth);
  await page.getByRole("menuitem", { name: "课程包管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "课程管理" })).toBeVisible();
  await page.getByRole("button", { name: "新建课程包" }).click();
  const packageDialog = page.getByRole("dialog", { name: "新建课程包" });
  await packageDialog.getByLabel("课程包名称").fill(PACKAGE_TITLE);
  await packageDialog.getByLabel("课程包说明").fill("用于验证新课程体系的端到端课程包。");
  await packageDialog.getByLabel("课程包作者").click();
  await page.locator(".ant-select-item-option").filter({ hasText: "admin" }).last().click();
  await packageDialog.getByRole("button", { name: "保存课程包" }).click();
  await expect(page.getByText("课程包已创建", { exact: true })).toBeVisible();

  for (const [title, instructions] of [
    [PERSONAL_COURSE_TITLE, "完成个人创意作品并提交。"],
    [CLASS_COURSE_TITLE, "完成班级协作作品并提交。"],
  ] as const) {
    await page.getByRole("button", { name: "添加课程" }).click();
    const courseDialog = page.getByRole("dialog", { name: "添加课程" });
    await courseDialog.getByLabel("课程名称").fill(title);
    await courseDialog.getByLabel("课程简介").fill(`${title} 的课程简介`);
    await courseDialog.getByLabel("提交说明").fill(instructions);
    await courseDialog.getByRole("button", { name: "保存课程" }).click();
    await expect(page.getByText("课程已添加", { exact: true }).last()).toBeVisible();
  }

  await expect(page.getByText("待补充", { exact: true })).toHaveCount(6);
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(page.getByText("课程包已发布", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "教师权限", exact: true }).click();
  const permissionDrawer = page.locator(".ant-drawer").filter({ hasText: `${PACKAGE_TITLE} · 教师权限` }).last();
  await permissionDrawer.getByRole("combobox").click();
  await page.locator(".ant-select-item-option").filter({ hasText: TEACHER_USERNAME }).last().click();
  await permissionDrawer.getByRole("button", { name: "保存权限" }).click();
  await expect(page.getByText("教师课程权限已更新", { exact: true })).toBeVisible();
  const assignment = await request.get(`${API_URL}/api/course-packages`, { headers: adminHeaders });
  expect((await assignment.json()).packages.find((item: { title: string }) => item.title === PACKAGE_TITLE)?.teacher_ids).toContain(teacherId);

  await setStaffAuth(page, teacherAuth);
  await page.getByRole("menuitem").filter({ hasText: PACKAGE_TITLE }).click();
  await expect(page).toHaveURL(/#\/teacher\/courses\?package=\d+$/);
  await expect(page.getByText(PACKAGE_TITLE, { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: PERSONAL_COURSE_TITLE })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/#\/teacher\/overview$/);
  await page.goForward();
  await expect(page.getByRole("heading", { name: PERSONAL_COURSE_TITLE })).toBeVisible();
  await expect(page.getByRole("heading", { name: CLASS_COURSE_TITLE })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建课程包" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "添加课程" })).toHaveCount(0);
  await expect(page.getByText("管理员暂未补充", { exact: true })).toHaveCount(6);

  await page.getByRole("menuitem", { name: "新建排课" }).click();
  await expect(page.getByRole("heading", { name: "排课管理" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "创建排课" })).toBeVisible();
  await createSchedule(page, PERSONAL_COURSE_TITLE, "学员", STUDENT_NAME);
  await page.locator(".ant-segmented").getByText("按班级", { exact: true }).click();
  await createSchedule(page, CLASS_COURSE_TITLE, "班级", CLASSROOM_NAME);
  await page.getByRole("menuitem", { name: "排课记录" }).click();
  await expect(page.locator(".ant-table-row").filter({ hasText: PERSONAL_COURSE_TITLE })).toBeVisible();
  await expect(page.locator(".ant-table-row").filter({ hasText: CLASS_COURSE_TITLE })).toBeVisible();

  await setStudentAuth(page, studentAuth);
  await page.getByRole("menuitem", { name: "课程学习" }).click();
  await expect(page.getByRole("heading", { name: "课程学习" })).toBeVisible();
  await expect(page.locator(".studentScheduleItem").filter({ hasText: PERSONAL_COURSE_TITLE })).toBeVisible();
  await expect(page.locator(".studentScheduleItem").filter({ hasText: CLASS_COURSE_TITLE })).toBeVisible();
  await page.locator(".studentScheduleItem").filter({ hasText: PERSONAL_COURSE_TITLE }).click();
  await expect(page.locator(".studentMaterial")).toHaveCount(1);
  await expect(page.locator(".studentMaterial")).toContainText("工程包");
  await expect(page.getByText("管理员暂未补充", { exact: true })).toHaveCount(1);
  await expect(page.getByText("课堂PPT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("成果包", { exact: true })).toHaveCount(0);
  await page.locator(".studentSubmitBar").getByRole("combobox").click();
  await page.locator(".ant-select-item-option").filter({ hasText: PROJECT_TITLE }).last().click();
  await page.getByRole("button", { name: "提交作品" }).click();
  await expect(page.getByText("作品已提交", { exact: true })).toBeVisible();
  await expect(page.getByText("作品已提交，等待老师批改", { exact: true })).toBeVisible();

  await setStaffAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "作业批改" }).click();
  const reviewItem = page.locator(".ant-list-item").filter({ hasText: PERSONAL_COURSE_TITLE }).filter({ hasText: PROJECT_TITLE }).first();
  await expect(reviewItem).toBeVisible();
  const scoreInput = reviewItem.getByRole("spinbutton");
  await scoreInput.fill("92");
  await expect(scoreInput).toHaveValue("92");
  const scoreBounds = await scoreInput.boundingBox();
  const maximumBounds = await reviewItem.getByText("/ 100", { exact: true }).boundingBox();
  expect(scoreBounds).not.toBeNull();
  expect(maximumBounds).not.toBeNull();
  expect(scoreBounds!.x + scoreBounds!.width).toBeLessThanOrEqual(maximumBounds!.x);
  await reviewItem.getByPlaceholder("给学生的反馈").fill(TEACHER_FEEDBACK);
  await reviewItem.getByRole("checkbox", { name: "优秀作品" }).check();
  await reviewItem.getByRole("button", { name: "保存批改" }).click();
  await expect(page.getByText("批改已保存", { exact: true })).toBeVisible();

  await setStudentAuth(page, studentAuth);
  await page.getByRole("menuitem", { name: "课程学习" }).click();
  await page.locator(".studentScheduleItem").filter({ hasText: PERSONAL_COURSE_TITLE }).click();
  await expect(page.getByText("老师已批改：92/100 分", { exact: true })).toBeVisible();
  await expect(page.getByText(TEACHER_FEEDBACK, { exact: true })).toBeVisible();

  await setStaffAuth(page, adminAuth);
  await page.getByRole("menuitem", { name: "学员管理" }).click();
  const adminStudentItem = page.locator(".ant-list-item").filter({ hasText: STUDENT_NAME }).first();
  await adminStudentItem.getByRole("checkbox").check();
  await page.getByRole("button", { name: "归档", exact: true }).click();
  await page.locator(".ant-popconfirm-buttons .ant-btn-primary").click();
  await expect(page.getByText("学生账号已归档", { exact: true })).toBeVisible();

  await setStaffAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "学员管理" }).click();
  await expect(page.getByText(STUDENT_NAME, { exact: true })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "作业批改" }).click();
  const archivedReview = page.locator(".ant-list-item").filter({ hasText: PERSONAL_COURSE_TITLE }).filter({ hasText: PROJECT_TITLE }).first();
  await expect(archivedReview.getByText("归档学员 · 历史只读", { exact: true })).toBeVisible();
  await expect(archivedReview.getByRole("button", { name: "保存批改" })).toHaveCount(0);

  await setStaffAuth(page, adminAuth);
  await page.getByRole("menuitem", { name: "学员管理" }).click();
  const archivedStudentItem = page.locator(".ant-list-item").filter({ hasText: STUDENT_NAME }).first();
  await archivedStudentItem.getByRole("checkbox").check();
  await page.locator(".studentBatchClassroom").click();
  await page.locator(".ant-select-item-option").filter({ hasText: CLASSROOM_NAME }).last().click();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText("学生账号已恢复", { exact: true })).toBeVisible();

  const backupResponse = await request.get(`${API_URL}/api/system/backups/export`, { headers: adminHeaders });
  expect(backupResponse.ok()).toBeTruthy();
  const backupPath = resolve("test-results", `e2e-backup-${RUN_ID}.zip`);
  await writeFile(backupPath, await backupResponse.body());
  await page.getByRole("menuitem", { name: "运维与备份" }).click();
  await expect(page.getByText("完整压缩备份", { exact: true })).toBeVisible();
  await page.locator('input[type="file"][accept*=".zip"]').setInputFiles(backupPath);
  await expect(page.getByText("备份包预检通过", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始扫描" }).click();
  await expect(page.getByText("文件一致性扫描完成", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "退出当前账号" }).click();
  await page.getByRole("button", { name: "学生端" }).click();
  await page.getByLabel("用户名").fill(STUDENT_USERNAME);
  await page.getByLabel("密码").fill(STUDENT_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/#\/student\/workspace$/);
});
