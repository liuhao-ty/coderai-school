import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const API_URL = "http://127.0.0.1:18000";
const DEFAULT_TEACHER_PASSWORD = "123456";
const TEACHER_PASSWORD = "E2E#Teacher2026";
const STUDENT_USERNAME = "student.demo";
const STUDENT_PASSWORD = "bcm123456";
const IMAGE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR4nGP4z8DAwMDAxMDAwMAAAAwBAQDJ/pLvAAAAAElFTkSuQmCC",
  "base64"
);

type ViewportCase = {
  name: string;
  width: number;
  height: number;
  deviceScaleFactor?: number;
};

const viewportCases: ViewportCase[] = [
  { name: "1280x720", width: 1280, height: 720 },
  { name: "900x800", width: 900, height: 800 },
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "1280x720-hidpi", width: 1280, height: 720, deviceScaleFactor: 2 }
];

async function loginData(request: APIRequestContext) {
  const studentResponse = await request.post(`${API_URL}/api/auth/student-login`, {
    data: { username: STUDENT_USERNAME, password: STUDENT_PASSWORD }
  });
  expect(studentResponse.ok()).toBeTruthy();
  const studentAuth = await studentResponse.json();

  let teacherResponse = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username: "admin", password: TEACHER_PASSWORD, device_name: "Playwright layout audit" }
  });
  if (!teacherResponse.ok()) {
    teacherResponse = await request.post(`${API_URL}/api/auth/teacher-login`, {
      data: { username: "admin", password: DEFAULT_TEACHER_PASSWORD, device_name: "Playwright layout audit" }
    });
  }
  expect(teacherResponse.ok()).toBeTruthy();
  let teacherAuth = await teacherResponse.json();
  if (teacherAuth.password_change_required) {
    const changed = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
      headers: { "X-CoderAI-Teacher-Token": teacherAuth.token },
      data: { current_password: DEFAULT_TEACHER_PASSWORD, next_password: TEACHER_PASSWORD }
    });
    expect(changed.ok()).toBeTruthy();
    teacherAuth = await changed.json();
  }
  return { studentAuth, teacherAuth };
}

async function seedResponsiveCurriculum(request: APIRequestContext) {
  const { studentAuth, teacherAuth } = await loginData(request);
  const headers = { "X-CoderAI-Teacher-Token": teacherAuth.token as string };
  const suffix = Date.now().toString(36);
  const packageTitle = `响应式课程包 ${suffix}`;
  const courseTitle = `PPT 预览课程 ${suffix}`;

  const packageResponse = await request.post(`${API_URL}/api/course-packages`, {
    headers,
    data: {
      title: packageTitle,
      description: "用于课程目录、资料卡和 PDF 抽屉的多视口检查。",
      package_version: "1.0.0",
      author_user_id: (teacherAuth.user as { id: number }).id,
      school_stages: ["primary_lower", "primary_upper", "secondary"],
      cover_path: "",
    },
  });
  expect(packageResponse.ok()).toBeTruthy();
  const packageId = (await packageResponse.json()).package.id as number;

  const courseResponse = await request.post(`${API_URL}/api/course-packages/${packageId}/courses`, {
    headers,
    data: {
      title: courseTitle,
      description: "验证课堂 PPT 在窄窗口和高 DPI 下的预览布局。",
      order_index: 0,
      assignment_instructions: "查看 PPT 后完成课堂练习。",
      tool_scope: "text,image",
      rubric: [{ criterion: "完成度", max_score: 100 }],
    },
  });
  expect(courseResponse.ok()).toBeTruthy();
  const courseId = (await courseResponse.json()).course.id as number;
  expect((await request.post(`${API_URL}/api/course-packages/${packageId}/publish`, { headers })).ok()).toBeTruthy();

  const fixture = await readFile(resolve("tests", "fixtures", "curriculum-preview.pptx"));
  const uploadResponse = await request.put(`${API_URL}/api/curriculum-courses/${courseId}/materials/slides`, {
    headers,
    multipart: {
      file: {
        name: "curriculum-preview.pptx",
        mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        buffer: fixture,
      },
    },
  });
  expect(uploadResponse.ok()).toBeTruthy();

  const starterUploadResponse = await request.put(`${API_URL}/api/curriculum-courses/${courseId}/materials/starter_markdown`, {
    headers,
    multipart: {
      file: {
        name: "课堂工程包.md",
        mimeType: "text/markdown",
        buffer: Buffer.from("# 课堂工程\n\n在这里完成课堂项目。", "utf8"),
      },
    },
  });
  expect(starterUploadResponse.ok()).toBeTruthy();

  let conversionStatus = "pending";
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const state = await request.get(`${API_URL}/api/course-packages/${packageId}`, { headers });
    expect(state.ok()).toBeTruthy();
    conversionStatus = (await state.json()).package.courses[0].materials.slides.conversion_status;
    if (conversionStatus === "ready" || conversionStatus === "failed") break;
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 500));
  }
  expect(conversionStatus).toBe("ready");

  const studentId = (studentAuth.student as { id: number }).id;
  const scheduleResponse = await request.post(`${API_URL}/api/course-schedules/batch`, {
    headers,
    data: {
      items: [{
        course_id: courseId,
        target_type: "student",
        target_id: studentId,
        starts_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
        due_at: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
      }],
    },
  });
  expect(scheduleResponse.ok()).toBeTruthy();
  return { studentAuth, teacherAuth, packageTitle, courseTitle };
}

async function setStudentAuth(page: Page, studentAuth: Record<string, unknown>) {
  await page.goto("/");
  await page.evaluate((auth) => {
    localStorage.setItem("coderai_student_token", auth.token as string);
    localStorage.setItem("coderai_student_profile", JSON.stringify(auth.student));
    localStorage.setItem("coderai_student_password_change_required", auth.password_change_required ? "true" : "false");
  }, studentAuth);
  await page.goto("/#/student/workspace");
  await expect(page).toHaveURL(/#\/student\/workspace\/notifications$/);
  await expect(page.getByRole("heading", { name: "课堂通知" })).toBeVisible();
}

async function setTeacherAuth(page: Page, teacherAuth: Record<string, unknown>) {
  await page.goto("/");
  await page.evaluate((auth) => {
    localStorage.setItem("coderai_teacher_token", auth.token as string);
    localStorage.setItem("coderai_teacher_refresh_token", auth.refresh_token as string);
    localStorage.setItem("coderai_teacher_access_expires_at", auth.access_expires_at as string);
    localStorage.setItem("coderai_teacher_password_change_required", auth.password_change_required ? "true" : "false");
    localStorage.setItem("coderai_teacher_profile", JSON.stringify(auth.user));
  }, teacherAuth);
  const user = teacherAuth.user as { role?: string };
  const isAdmin = user?.role === "admin";
  await page.goto(isAdmin ? "/#/admin/overview" : "/#/teacher/overview");
  await expect(page.getByRole("heading", { name: isAdmin ? "系统总览" : "教学总览" })).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth
  }));
  expect(dimensions.document, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.viewport);
  expect(dimensions.body, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.viewport);
}

async function expectNoSeriousAccessibilityViolations(page: Page) {
  await page.keyboard.press("Escape");
  await expect(page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)")).toHaveCount(0);
  const results = await new AxeBuilder({ page }).analyze();
  const violations = results.violations.filter((item) => item.impact === "critical" || item.impact === "serious");
  expect(violations, violations.map((item) => `${item.id}: ${item.help}`).join("\n")).toEqual([]);
}

test("默认管理员密码首次登录必须修改", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /教师端 \/ 管理员端/ }).click();
  await page.getByLabel("职员用户名").fill("admin");
  await page.getByLabel("职员密码").fill(DEFAULT_TEACHER_PASSWORD);
  await page.getByRole("button", { name: "进入对应工作台" }).click();
  await expect(page).toHaveURL(/#\/teacher\/change-password$/);
  await expect(page.getByRole("heading", { name: "修改登录密码" })).toBeVisible();
  await page.getByLabel("当前或临时密码").fill(DEFAULT_TEACHER_PASSWORD);
  await page.getByLabel("新密码", { exact: true }).fill(TEACHER_PASSWORD);
  await page.getByLabel("确认新密码").fill(TEACHER_PASSWORD);
  await page.getByRole("button", { name: "修改密码并进入对应工作台" }).click();
  await expect(page).toHaveURL(/#\/admin\/overview$/);
  await expect(page.getByRole("heading", { name: "系统总览" })).toBeVisible();
});

test("管理员单独和批量创建学生，学生端不提供自行注册", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  const username = `student.ui.${Date.now()}`;
  const importedUsername = `student.csv.${Date.now()}`;

  await setTeacherAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "账号管理" }).click();
  await page.getByRole("tab", { name: "学生开户" }).click();
  await expect(page.getByRole("button", { name: "选择 CSV 批量开户" })).toBeVisible();
  await page.getByLabel("学生姓名").fill("管理员创建学生");
  await page.getByLabel("唯一用户名").fill(username);
  await page.getByLabel("学龄分类").focus();
  await page.getByLabel("学龄分类").press("ArrowDown");
  await page.locator(".ant-select-item-option").filter({ hasText: "初中高中" }).click();
  await page.getByRole("button", { name: "创建学生账号" }).click();
  await expect(page.getByRole("dialog", { name: "学生账号已创建" })).toBeVisible();
  await expect(page.getByText("bcm123456", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "知道了" }).click();

  const csv = `姓名,用户名,学龄分类\n批量创建学生,${importedUsername},小学低龄\n`;
  await page.locator('input[type="file"][accept*=".csv"]').setInputFiles({
    name: "students.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv, "utf-8")
  });
  await expect(page.getByText(/新增 1，更新 0，跳过 0/)).toBeVisible();
  const studentsResponse = await request.get(`${API_URL}/api/students`, {
    headers: { "X-CoderAI-Teacher-Token": teacherAuth.token as string },
  });
  expect(studentsResponse.ok()).toBeTruthy();
  const students = (await studentsResponse.json()).students as Array<{ username?: string; age_level?: string; school_stage_label?: string }>;
  expect(students.find((student) => student.username === username)).toMatchObject({ age_level: "secondary", school_stage_label: "初中高中" });
  expect(students.find((student) => student.username === importedUsername)).toMatchObject({ age_level: "primary_lower", school_stage_label: "小学低龄" });

  await page.getByRole("button", { name: "退出当前账号" }).click();
  await page.getByRole("button", { name: /学生端/ }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码", { exact: true }).fill("bcm123456");
  await expect(page.getByRole("button", { name: "首次注册" })).toHaveCount(0);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/#\/student\/workspace\/notifications$/);
  await expect(page.getByRole("heading", { name: "课堂通知" })).toBeVisible();
  await expect(page.getByText(/管理员创建学生/).first()).toBeVisible();

  const disabled = await request.post(`${API_URL}/api/auth/student-register`, {
    data: { invitation_code: "DISABLED", username: "disabled.self", password: "Student2026" }
  });
  expect(disabled.status()).toBe(410);
  expect((await disabled.json()).detail.code).toBe("STUDENT_SELF_REGISTRATION_DISABLED");
});

test("身份页和工作台通过严重级别可访问性扫描", async ({ page, request }) => {
  const { studentAuth, teacherAuth } = await loginData(request);
  await page.goto("/");
  await expectNoSeriousAccessibilityViolations(page);

  const legacyStudentAuth = {
    ...studentAuth,
    student: { ...studentAuth.student, age_level: "junior", school_stage_label: "低龄引导" },
  };
  await setStudentAuth(page, legacyStudentAuth);
  await expect(page.getByText("小学低龄", { exact: false }).first()).toBeVisible();
  const cachedProfile = await page.evaluate(() => JSON.parse(localStorage.getItem("coderai_student_profile") || "{}"));
  expect(cachedProfile).toMatchObject({ age_level: "primary_lower", school_stage_label: "小学低龄" });
  await expectNoSeriousAccessibilityViolations(page);

  await page.evaluate(() => localStorage.clear());
  await setTeacherAuth(page, teacherAuth);
  await expectNoSeriousAccessibilityViolations(page);
});

test("普通教师进入独立教学工作台且看不到系统管理入口", async ({ page, request }) => {
  const { teacherAuth: adminAuth } = await loginData(request);
  const adminHeaders = { "X-CoderAI-Teacher-Token": adminAuth.token as string };
  const username = `teacher.ui.${Date.now()}`;
  const password = "TeacherUi#2027";
  const created = await request.post(`${API_URL}/api/accounts/teachers`, {
    headers: adminHeaders,
    data: { name: "界面测试教师", username, role: "teacher" }
  });
  expect(created.ok()).toBeTruthy();
  const login = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username, password: (await created.json()).temporary_password }
  });
  expect(login.ok()).toBeTruthy();
  const temporaryAuth = await login.json();
  const changed = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
    headers: { "X-CoderAI-Teacher-Token": temporaryAuth.token as string },
    data: { current_password: (await created.json()).temporary_password, next_password: password }
  });
  expect(changed.ok()).toBeTruthy();
  const teacherAuth = await changed.json();
  const teacherHeaders = { "X-CoderAI-Teacher-Token": teacherAuth.token as string };
  const classroom = await request.post(`${API_URL}/api/classrooms`, {
    headers: teacherHeaders,
    data: { name: "界面测试班级", grade_level: "primary_lower" }
  });
  expect(classroom.ok()).toBeTruthy();
  const classroomId = (await classroom.json()).classroom.id as number;
  expect((await request.post(`${API_URL}/api/students`, {
    headers: adminHeaders,
    data: { name: "界面测试学员", username: `student.teacher.ui.${Date.now()}`, classroom_id: classroomId }
  })).ok()).toBeTruthy();

  await setTeacherAuth(page, teacherAuth);
  await expect(page.getByText("教学管理", { exact: true })).toBeVisible();
  await expect(page.getByText("系统管理", { exact: true })).toHaveCount(0);
  await expect(page.getByText("账号管理", { exact: true })).toHaveCount(0);
  await expect(page.getByText("模型服务", { exact: true })).toHaveCount(0);
  await expect(page.getByText("P0验收", { exact: true })).toHaveCount(0);
  await expect(page.getByText("我的班级").first()).toBeVisible();
  await page.getByText("班级管理", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "班级管理" })).toBeVisible();
  await expect(page.getByText("界面测试班级", { exact: true })).toBeVisible();
  await page.getByRole("menuitem", { name: "学员管理" }).click();
  await expect(page.getByRole("heading", { name: "学员管理" })).toBeVisible();
  await expect(page.getByText("界面测试学员", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建学生账号" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "选择 CSV 批量开户" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "归档" })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "账号安全" }).click();
  await expect(page.getByRole("heading", { name: "账号安全" })).toBeVisible();
  await expect(page.getByLabel("当前密码")).toBeVisible();
  await expect(page.getByLabel("新密码")).toBeVisible();
  await expect(page.getByRole("button", { name: "更新密码" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "登录设备" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "操作审计" })).toBeVisible();
  await page.goto("/#/admin/models");
  await expect(page).toHaveURL(/#\/teacher\/overview$/);
  await expect(page.getByRole("heading", { name: "教学总览" })).toBeVisible();
});

test("教师弱密码每次登录提醒并支持忽略或重新设置", async ({ page, request }) => {
  const { teacherAuth: adminAuth } = await loginData(request);
  const adminHeaders = { "X-CoderAI-Teacher-Token": adminAuth.token as string };
  const username = `teacher.weak.${Date.now()}`;
  const created = await request.post(`${API_URL}/api/accounts/teachers`, {
    headers: adminHeaders,
    data: { name: "弱密码测试教师", username, role: "teacher" }
  });
  expect(created.ok()).toBeTruthy();
  const temporaryPassword = (await created.json()).temporary_password as string;
  const temporaryLogin = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username, password: temporaryPassword }
  });
  const temporaryAuth = await temporaryLogin.json();
  const changed = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
    headers: { "X-CoderAI-Teacher-Token": temporaryAuth.token as string },
    data: { current_password: temporaryPassword, next_password: "abcdefgh" }
  });
  expect(changed.ok()).toBeTruthy();
  expect((await changed.json()).password_is_weak).toBe(true);

  const loginThroughUi = async () => {
    await page.goto("/");
    await page.getByRole("button", { name: /教师端 \/ 管理员端/ }).click();
    await page.getByLabel("职员用户名").fill(username);
    await page.getByLabel("职员密码").fill("abcdefgh");
    await page.getByRole("button", { name: "进入对应工作台" }).click();
    await expect(page.getByRole("dialog", { name: "当前教师密码安全性较弱" })).toBeVisible();
  };

  await loginThroughUi();
  await page.getByRole("button", { name: "本次登录忽略" }).click();
  await expect(page.getByRole("dialog", { name: "当前教师密码安全性较弱" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "教学总览" })).toBeVisible();
  await page.getByRole("button", { name: "退出当前账号" }).click();

  await loginThroughUi();
  await page.getByRole("button", { name: "重新设置密码" }).click();
  await expect(page).toHaveURL(/#\/teacher\/change-password$/);
  await expect(page.getByText(/特殊字符/)).toHaveCount(0);
  await page.getByLabel("当前或临时密码").fill("abcdefgh");
  await page.getByLabel("新密码", { exact: true }).fill("teacher2027");
  await page.getByLabel("确认新密码").fill("teacher2027");
  await page.getByRole("button", { name: "修改密码并进入对应工作台" }).click();
  await expect(page).toHaveURL(/#\/teacher\/overview$/);
  await expect(page.getByRole("dialog", { name: "当前教师密码安全性较弱" })).toHaveCount(0);
});

test("隐私政策和管理员数据治理入口可访问", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await page.goto("/");
  await page.getByRole("button", { name: "隐私与未成年人数据保护政策" }).click();
  await expect(page).toHaveURL(/#\/privacy$/);
  await expect(page.getByRole("heading", { name: "隐私与未成年人数据保护", exact: true })).toBeVisible();
  await expect(page.getByText("我们处理哪些数据", { exact: true })).toBeVisible();
  const publicScrollState = await page.evaluate(() => ({
    bodyOverflowY: getComputedStyle(document.body).overflowY,
    scrollHeight: document.scrollingElement?.scrollHeight || 0,
    clientHeight: document.scrollingElement?.clientHeight || 0,
  }));
  expect(publicScrollState.bodyOverflowY).not.toBe("hidden");
  expect(publicScrollState.scrollHeight).toBeGreaterThan(publicScrollState.clientHeight);
  await page.evaluate(() => window.scrollTo({ top: document.scrollingElement?.scrollHeight || 0, behavior: "auto" }));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: "test-results/layout-privacy-public.png", fullPage: true });

  await setTeacherAuth(page, teacherAuth);
  await page.getByText("隐私政策", { exact: true }).click();
  await expect(page.getByText("隐私政策版本", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "选择学生" }).click();
  await page.locator(".ant-select-item-option").filter({ hasText: "默认学生" }).click();
  await expect(page.getByText("AI 数据处理权限有效", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "导出个人数据" })).toBeVisible();
  await expect(page.getByRole("button", { name: "删除个人数据" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: "test-results/layout-privacy-teacher.png", fullPage: true });
});

test("签名授权页面显示社区席位和设备安装码", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  const licenseResponse = await request.get(`${API_URL}/api/system/license`, {
    headers: { "X-CoderAI-Teacher-Token": teacherAuth.token as string }
  });
  expect(licenseResponse.ok()).toBeTruthy();
  const { license } = await licenseResponse.json();
  await setTeacherAuth(page, teacherAuth);
  await page.getByText("扩展与授权", { exact: true }).click();
  await expect(page.getByText("本地课堂", { exact: true })).toBeVisible();
  await expect(page.getByText("社区授权", { exact: true })).toBeVisible();
  await expect(page.getByText(`${license.seats_used} / ${license.seats}`, { exact: true })).toBeVisible();
  await expect(page.getByText(/DEV-[A-F0-9]{20}/)).toBeVisible();
  await expect(page.getByLabel("签名许可证")).toBeVisible();
  await expect(page.getByRole("button", { name: "导入并验证" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: "test-results/layout-signed-license.png", fullPage: true });
});

test("云端内测授权隐藏设备许可证入口", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await page.route("**/api/system/license", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        license: {
          license_key: "",
          organization: "CoderAI 试点机构",
          plan: "cloud_pilot",
          valid: true,
          usable: true,
          signature_verified: false,
          status: "cloud_pilot",
          message: "当前为机构云端内测授权。",
          license_id: "CLOUD-PILOT-CODERAI-PILOT",
          issued_at: "2026-07-22T00:00:00",
          not_before: "",
          expires_at: "",
          seats: 50,
          seats_used: 12,
          seats_remaining: 38,
          features: ["student_workspace", "teacher_console", "plugins"],
          device_id: "",
          device_bound: false,
          authorized_devices: ["cloud-managed"],
          issuer: "CoderAI Cloud Pilot",
        },
      }),
    });
  });
  await setTeacherAuth(page, teacherAuth);
  await page.getByText("扩展与授权", { exact: true }).click();
  await expect(page.getByText("平台托管授权", { exact: true })).toBeVisible();
  await expect(page.getByText("云端授权由平台管理", { exact: true })).toBeVisible();
  await expect(page.getByLabel("签名许可证")).toHaveCount(0);
  await expect(page.getByText("当前设备安装码：", { exact: true })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousAccessibilityViolations(page);
});

test("程序验收入口不出现在产品控制台", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await setTeacherAuth(page, teacherAuth);
  await expect(page.getByText("P0验收", { exact: true })).toHaveCount(0);
  const readiness = await request.get(`${API_URL}/api/readiness`, {
    headers: { "X-CoderAI-Teacher-Token": teacherAuth.token as string }
  });
  expect(readiness.ok()).toBeTruthy();
  await expectNoSeriousAccessibilityViolations(page);
});

test("管理员侧栏提供全机构教学管理并保持系统总览独立", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await setTeacherAuth(page, teacherAuth);
  await expect(page.getByRole("heading", { name: "系统总览" })).toBeVisible();
  await expect(page.getByText("教师的班级、学员、作品和批改数据不会出现在此总览。")).toBeVisible();

  await page.getByRole("menuitem", { name: "教学总览" }).click();
  await expect(page).toHaveURL(/#\/admin\/teaching$/);
  await expect(page.getByRole("heading", { name: "教学总览" })).toBeVisible();
  await expect(page.getByText("全机构班级").first()).toBeVisible();

  await page.getByRole("menuitem", { name: "班级管理" }).click();
  await expect(page.getByRole("heading", { name: "班级管理" })).toBeVisible();
  await expect(page.getByText("班级与授课教师分开授权")).toBeVisible();

  await page.getByRole("menuitem", { name: "学员管理" }).click();
  await expect(page.getByRole("heading", { name: "学员管理" })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建学生账号" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "选择 CSV 批量开户" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "导出全部账号" })).toBeVisible();
  await expect(page.getByRole("button", { name: "编辑账号" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "重置密码" }).first()).toBeVisible();

  await page.getByRole("menuitem", { name: "系统总览" }).click();
  await expect(page).toHaveURL(/#\/admin\/overview$/);
  await expect(page.getByRole("heading", { name: "系统总览" })).toBeVisible();
});

test("管理员账号管理集中开户与密码修改，账号安全只保留活动子页", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await setTeacherAuth(page, teacherAuth);

  await page.getByRole("menuitem", { name: "账号管理" }).click();
  await expect(page.getByRole("heading", { name: "账号与身份管理" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "教师与管理员" })).toBeVisible();
  await page.getByRole("tab", { name: "学生开户" }).click();
  await expect(page.getByRole("button", { name: "创建学生账号" })).toBeVisible();
  await expect(page.getByRole("button", { name: "选择 CSV 批量开户" })).toBeVisible();
  const templateDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载模板" }).click();
  const templateDownload = await templateDownloadPromise;
  expect(templateDownload.suggestedFilename()).toBe("学生账号导入模板.csv");
  expect((await templateDownload.createReadStream())?.readable).toBeTruthy();
  await page.getByRole("tab", { name: "修改登录密码" }).click();
  const passwordPanel = page.getByRole("tabpanel", { name: "修改登录密码" });
  await expect(passwordPanel.getByLabel("当前密码")).toBeVisible();
  await expect(passwordPanel.getByLabel("新密码")).toBeVisible();
  await expect(passwordPanel.getByRole("button", { name: "更新密码" })).toBeVisible();

  await page.getByRole("menuitem", { name: "账号安全" }).click();
  await expect(page.getByRole("heading", { name: "管理员账号安全" })).toBeVisible();
  await expect(page.getByLabel("当前密码")).toHaveCount(0);
  const sessionsTab = page.getByRole("tab", { name: "登录设备" });
  const auditTab = page.getByRole("tab", { name: "操作审计" });
  await expect(sessionsTab).toHaveAttribute("aria-selected", "true");
  await sessionsTab.focus();
  await sessionsTab.press("ArrowRight");
  await expect(auditTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("全部高风险操作", { exact: true })).toBeVisible();
});

test("云端短暂 503 后只读请求会自动恢复", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  let studentRequestAttempts = 0;
  await page.route("**/api/students", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    studentRequestAttempts += 1;
    if (studentRequestAttempts === 1) {
      await route.fulfill({
        status: 503,
        headers: {
          "access-control-allow-origin": "http://127.0.0.1:15173",
          "content-type": "application/json",
        },
        body: JSON.stringify({ detail: { code: "E2E_TRANSIENT_503", message: "模拟云端服务启动中" } }),
      });
      return;
    }
    await route.continue();
  });

  await setTeacherAuth(page, teacherAuth);
  await expect.poll(() => studentRequestAttempts).toBe(2);
  await expect(page.getByText("Network Error", { exact: true })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "学员管理" }).click();
  await expect(page.getByRole("heading", { name: "学员管理" })).toBeVisible();
});

test("工作台只滚动右侧内容并去除顶部重复状态", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await page.setViewportSize({ width: 900, height: 800 });
  await setTeacherAuth(page, teacherAuth);

  const topbar = page.locator(".topbar");
  await expect(topbar).not.toContainText(/AI 服务可用|AI 服务待配置|北京时间/);
  await page.getByRole("menuitem", { name: "模型服务" }).click();
  await expect(page.locator(".teacherHero .ant-tag").filter({ hasText: /模型服务可用|模型服务待配置/ })).toHaveCount(1);
  await expect(topbar).not.toContainText(/AI 服务可用|AI 服务待配置|北京时间/);

  await page.getByRole("menuitem", { name: "运维与备份" }).click();
  await expect(page.getByRole("heading", { name: "运维与备份" })).toBeVisible();
  const before = await page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>(".sidebar")!;
    const header = document.querySelector<HTMLElement>(".topbar")!;
    const content = document.querySelector<HTMLElement>(".content")!;
    return {
      bodyTop: document.body.scrollTop,
      bodyOverflowY: getComputedStyle(document.body).overflowY,
      documentTop: document.documentElement.scrollTop,
      sidebarTop: sidebar.getBoundingClientRect().top,
      headerTop: header.getBoundingClientRect().top,
      contentScrollTop: content.scrollTop,
      contentScrollHeight: content.scrollHeight,
      contentClientHeight: content.clientHeight,
    };
  });
  expect(before.bodyOverflowY).toBe("hidden");
  expect(before.contentScrollHeight).toBeGreaterThan(before.contentClientHeight);
  await page.locator(".content").evaluate((element) => element.scrollTo({ top: element.scrollHeight, behavior: "auto" }));
  await expect.poll(() => page.locator(".content").evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  const after = await page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>(".sidebar")!;
    const header = document.querySelector<HTMLElement>(".topbar")!;
    const content = document.querySelector<HTMLElement>(".content")!;
    return {
      bodyTop: document.body.scrollTop,
      documentTop: document.documentElement.scrollTop,
      sidebarTop: sidebar.getBoundingClientRect().top,
      headerTop: header.getBoundingClientRect().top,
      contentScrollTop: content.scrollTop,
    };
  });
  expect(after.bodyTop).toBe(0);
  expect(after.documentTop).toBe(0);
  expect(after.sidebarTop).toBeCloseTo(before.sidebarTop, 1);
  expect(after.headerTop).toBeCloseTo(before.headerTop, 1);
  expect(after.contentScrollTop).toBeGreaterThan(0);
});

test("图片作品支持本地与云端预览下载且不显示云端地址", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  const createdAt = "2026-07-16T20:00:00+08:00";
  const remoteUrl = "https://images.example.test/remote.png";
  const failedRemoteUrl = "https://images.example.test/failure.png";
  const baseProject = {
    owner_teacher_id: null,
    user_id: 1,
    classroom_id: 1,
    owner_name: "图片测试学生",
    project_type: "image",
    summary: "## 图片作品说明",
    lifecycle_status: "active",
    moderation_status: "approved",
    moderation_reason: "",
    moderation_log_id: null,
    archived_at: null,
    trashed_at: null,
    latest_submitted_at: createdAt,
    created_at: createdAt,
    updated_at: createdAt,
  };
  const projects = [
    { ...baseProject, id: 9101, title: "E2E 本地图片", file_path: "E:\\e2e-data\\local.png", file_exists: true, file_status: "ok" },
    { ...baseProject, id: 9102, title: "E2E 云端图片", file_path: remoteUrl, file_exists: false, file_status: "remote" },
    { ...baseProject, id: 9103, title: "E2E 缺失图片", file_path: "E:\\e2e-data\\missing.png", file_exists: false, file_status: "missing" },
    { ...baseProject, id: 9104, title: "E2E 审核失败图片", file_path: failedRemoteUrl, file_exists: false, file_status: "remote", moderation_status: "rejected", moderation_reason: "测试审核未通过" },
    { ...baseProject, id: 9105, title: "E2E 下载失败图片", file_path: "E:\\e2e-data\\download-failure.png", file_exists: true, file_status: "ok" },
  ];
  const corsHeaders = { "access-control-allow-origin": "*" };
  let failedDownloadFileRequests = 0;

  await page.route("https://images.example.test/remote.png", (route) => route.fulfill({ status: 200, contentType: "image/png", body: IMAGE_PNG }));
  await page.route("https://images.example.test/failure.png", (route) => route.abort("failed"));
  await page.route("**/api/projects**", async (route) => {
    const requestUrl = new URL(route.request().url());
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    if (requestUrl.pathname === "/api/projects") {
      await route.fulfill({ status: 200, headers: corsHeaders, contentType: "application/json", body: JSON.stringify({ projects, total: projects.length }) });
      return;
    }
    const fileMatch = requestUrl.pathname.match(/^\/api\/projects\/(\d+)\/file$/);
    if (fileMatch?.[1] === "9101") {
      await route.fulfill({ status: 200, headers: corsHeaders, contentType: "image/png", body: IMAGE_PNG });
      return;
    }
    if (fileMatch?.[1] === "9105") {
      failedDownloadFileRequests += 1;
      if (failedDownloadFileRequests === 1) {
        await route.fulfill({ status: 200, headers: corsHeaders, contentType: "image/png", body: IMAGE_PNG });
      } else {
        await route.fulfill({ status: 503, headers: corsHeaders, contentType: "application/json", body: JSON.stringify({ detail: "模拟文件下载失败" }) });
      }
      return;
    }
    const detailMatch = requestUrl.pathname.match(/^\/api\/projects\/(\d+)$/);
    const project = detailMatch ? projects.find((item) => item.id === Number(detailMatch[1])) : undefined;
    if (project) {
      await route.fulfill({ status: 200, headers: corsHeaders, contentType: "application/json", body: JSON.stringify({ project }) });
      return;
    }
    await route.continue();
  });

  await setTeacherAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "学员作品" }).click();
  await expect(page.getByRole("heading", { name: "作品管理" })).toBeVisible();
  await expect(page.getByText(/最近提交：/).first()).toBeVisible();
  const studentFilter = page.getByRole("combobox", { name: "按学生筛选作品" });
  await studentFilter.fill("默认");
  await page.locator(".ant-select-item-option").filter({ hasText: "默认学生" }).click();
  const studentSelect = studentFilter.locator("xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]");
  await studentSelect.hover();
  await studentSelect.locator(".ant-select-clear").click();
  await expect(page.locator(".projectCard")).toHaveCount(projects.length);
  const classroomFilter = page.getByRole("combobox", { name: "按班级筛选作品" });
  await classroomFilter.fill("默认");
  await page.locator(".ant-select-item-option").filter({ hasText: "默认班级" }).click();
  const classroomSelect = classroomFilter.locator("xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' ant-select ')][1]");
  await classroomSelect.hover();
  await classroomSelect.locator(".ant-select-clear").click();
  await expect(page.locator(".projectCard")).toHaveCount(projects.length);
  await page.getByPlaceholder("提交开始日期").fill("2026-07-16");
  await page.getByPlaceholder("提交开始日期").press("Enter");
  await page.getByPlaceholder("提交结束日期").fill("2026-07-16");
  await page.getByPlaceholder("提交结束日期").press("Enter");
  await expect(page.locator(".projectCard")).toHaveCount(projects.length);

  await page.locator(".projectCard").filter({ hasText: "E2E 本地图片" }).click();
  let drawer = page.locator(".ant-drawer-content").last();
  await expect(drawer.getByRole("tab", { name: /图片预览/ })).toBeVisible();
  const localImage = drawer.getByRole("img", { name: "图片作品：E2E 本地图片" });
  await expect.poll(() => localImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  const downloadPromise = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "下载原图" }).click();
  const localDownload = await downloadPromise;
  expect(localDownload.suggestedFilename()).toBe("E2E 本地图片.png");
  await drawer.locator(".ant-drawer-close").click();

  await page.locator(".projectCard").filter({ hasText: "E2E 云端图片" }).click();
  drawer = page.locator(".ant-drawer-content").last();
  const remoteImage = drawer.getByRole("img", { name: "图片作品：E2E 云端图片" });
  await expect.poll(() => remoteImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  expect(await drawer.evaluate((element) => element.innerText.includes("https://"))).toBeFalsy();
  await page.evaluate(() => {
    const target = window as typeof window & { __coderaiDownloadClicks?: Array<{ href: string; download: string; target: string }> };
    target.__coderaiDownloadClicks = [];
    HTMLAnchorElement.prototype.click = function click() {
      target.__coderaiDownloadClicks!.push({ href: this.href, download: this.download, target: this.target });
    };
  });
  await drawer.getByRole("button", { name: "下载原图" }).click();
  const remoteDownload = await page.evaluate(() => (window as typeof window & { __coderaiDownloadClicks?: Array<{ href: string; download: string; target: string }> }).__coderaiDownloadClicks?.[0]);
  expect(remoteDownload).toEqual({ href: remoteUrl, download: "E2E 云端图片.png", target: "_blank" });
  await drawer.locator(".ant-drawer-close").click();

  await page.locator(".projectCard").filter({ hasText: "E2E 缺失图片" }).click();
  drawer = page.locator(".ant-drawer-content").last();
  await expect(drawer.getByText("图片暂时无法预览", { exact: true })).toBeVisible();
  await expect(drawer.getByText("作品记录存在，但图片文件已经缺失。", { exact: true })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "下载原图" })).toBeDisabled();
  await drawer.locator(".ant-drawer-close").click();

  await page.locator(".projectCard").filter({ hasText: "E2E 审核失败图片" }).click();
  drawer = page.locator(".ant-drawer-content").last();
  await expect(drawer.getByText("审核未通过", { exact: true })).toBeVisible();
  await expect(drawer.getByText("云端图片加载失败，请稍后重试或使用下载按钮打开原图。", { exact: true })).toBeVisible();
  expect(await drawer.evaluate((element, url) => element.innerText.includes(url), failedRemoteUrl)).toBeFalsy();
  await drawer.locator(".ant-drawer-close").click();

  await page.locator(".projectCard").filter({ hasText: "E2E 下载失败图片" }).click();
  drawer = page.locator(".ant-drawer-content").last();
  const failedDownloadImage = drawer.getByRole("img", { name: "图片作品：E2E 下载失败图片" });
  await expect.poll(() => failedDownloadImage.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await drawer.getByRole("button", { name: "下载原图" }).click();
  await expect(page.getByText("图片下载失败：模拟文件下载失败", { exact: true })).toBeVisible();
});

test("管理员可以管理多模型配置与能力路由", async ({ page, request }) => {
  const { teacherAuth } = await loginData(request);
  await setTeacherAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "模型服务" }).click();
  await expect(page.getByText("能力路由", { exact: true })).toBeVisible();
  await expect(page.getByText("服务商与模型配置", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存路由" })).toBeVisible();
  await expect(page.getByRole("button", { name: "添加服务" })).toBeVisible();

  await page.getByRole("button", { name: "添加服务" }).click();
  const dialog = page.getByRole("dialog", { name: "添加模型服务" });
  await expect(dialog).toBeVisible();
  const textModelSelect = dialog.getByRole("combobox", { name: "文字模型" });
  await expect(textModelSelect).toBeVisible();
  await textModelSelect.focus();
  await textModelSelect.press("ArrowDown");
  const alternateTextModel = page.locator(".ant-select-item-option").filter({ hasText: "GPT-4.1 mini" });
  await expect(alternateTextModel).toBeVisible();
  await alternateTextModel.click();
  await expect(dialog.getByRole("combobox", { name: "图片模型" })).toBeVisible();
  await dialog.getByLabel("配置名称").fill("E2E 课堂主模型");
  await dialog.getByLabel("API Key").fill("e2e-provider-secret");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("模型服务已添加")).toBeVisible();
  await expect(page.getByText("E2E 课堂主模型").first()).toBeVisible();
  await expect(page.getByText("主", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("combobox", { name: /路由添加模型/ })).toHaveCount(3);
  const checkCapabilityLabels = async () => {
    const metrics = await page.locator(".modelLine").evaluateAll((lines) => lines.map((line) => {
      const tag = line.querySelector<HTMLElement>(".ant-tag");
      const model = line.querySelector<HTMLElement>(".ant-typography");
      if (!tag || !model) return null;
      const tagBox = tag.getBoundingClientRect();
      const modelBox = model.getBoundingClientRect();
      return {
        width: tagBox.width,
        height: tagBox.height,
        scrollHeight: tag.scrollHeight,
        whiteSpace: getComputedStyle(tag).whiteSpace,
        separated: tagBox.right <= modelBox.left,
      };
    }).filter(Boolean));
    expect(metrics.length).toBeGreaterThan(0);
    for (const metric of metrics as Array<{ width: number; height: number; scrollHeight: number; whiteSpace: string; separated: boolean }>) {
      expect(metric.width).toBe(76);
      expect(metric.height).toBe(26);
      expect(metric.scrollHeight).toBeLessThanOrEqual(26);
      expect(metric.whiteSpace).toBe("nowrap");
      expect(metric.separated).toBeTruthy();
    }
  };
  await checkCapabilityLabels();
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousAccessibilityViolations(page);
  await page.screenshot({ path: "test-results/layout-multi-model-manager.png", fullPage: true });
  await page.setViewportSize({ width: 900, height: 800 });
  await checkCapabilityLabels();
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: "test-results/layout-multi-model-manager-narrow.png", fullPage: true });
});

test("工作流节点可以行内选择模型并灰显未配置模型", async ({ page, request }) => {
  const { teacherAuth: adminAuth } = await loginData(request);
  const adminHeaders = { "X-CoderAI-Teacher-Token": adminAuth.token as string };
  const providerResponse = await request.post(`${API_URL}/api/settings/providers`, {
    headers: adminHeaders,
    data: {
      name: "E2E 工作流 DeepSeek",
      provider_type: "deepseek",
      base_url: "https://api.deepseek.com",
      api_key: "e2e-workflow-provider-secret",
      text_model: "deepseek-chat",
      image_model: "",
      video_model: "",
      enabled: true
    }
  });
  expect(providerResponse.ok()).toBeTruthy();
  const providerId = (await providerResponse.json()).provider.id as number;

  const username = `teacher.workflow.${Date.now()}`;
  const password = "WorkflowTeacher#2027";
  const accountResponse = await request.post(`${API_URL}/api/accounts/teachers`, {
    headers: adminHeaders,
    data: { name: "工作流测试教师", username, role: "teacher" }
  });
  expect(accountResponse.ok()).toBeTruthy();
  const temporaryPassword = (await accountResponse.json()).temporary_password as string;
  const temporaryLogin = await request.post(`${API_URL}/api/auth/teacher-login`, {
    data: { username, password: temporaryPassword }
  });
  expect(temporaryLogin.ok()).toBeTruthy();
  const temporaryAuth = await temporaryLogin.json();
  const changed = await request.post(`${API_URL}/api/auth/change-teacher-password`, {
    headers: { "X-CoderAI-Teacher-Token": temporaryAuth.token as string },
    data: { current_password: temporaryPassword, next_password: password }
  });
  expect(changed.ok()).toBeTruthy();
  const teacherAuth = await changed.json();
  const teacherHeaders = { "X-CoderAI-Teacher-Token": teacherAuth.token as string };

  await setTeacherAuth(page, teacherAuth);
  await page.getByRole("menuitem", { name: "工作流" }).click();
  await page.getByRole("button", { name: "添加文字分支" }).click();
  const modelSelect = page.getByRole("combobox", { name: "文字生成使用模型" });
  await expect(modelSelect).toBeVisible();
  await page.locator(".workflowNodeModelSelect").click();
  await modelSelect.fill("DeepSeek");
  const configuredModel = page.locator(".ant-select-item-option").filter({ hasText: "E2E 工作流 DeepSeek" });
  await expect(configuredModel).not.toHaveClass(/ant-select-item-option-disabled/);
  const unavailableModel = page.locator(".ant-select-item-option").filter({ hasText: "DeepSeek Reasoner（尚未在系统中配置）" });
  await expect(unavailableModel).toHaveClass(/ant-select-item-option-disabled/);
  await configuredModel.click();
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText("工作流草稿已保存")).toBeVisible();

  const workflowsResponse = await request.get(`${API_URL}/api/workflows`, { headers: teacherHeaders });
  expect(workflowsResponse.ok()).toBeTruthy();
  const workflow = (await workflowsResponse.json()).workflows.find((item: { name: string }) => item.name === "我的工作流");
  const textNode = workflow.definition.nodes.find((node: { type: string }) => node.type === "text.generate");
  expect(textNode.params.provider_id).toBe(providerId);
  expect(textNode.params.model).toBe("deepseek-chat");
  await expectNoHorizontalOverflow(page);
});

test("键盘可以完成学生登录、跳过导航和页面切换", async ({ page, request }) => {
  await loginData(request);
  await page.goto("/");
  const launchHeading = page.getByRole("heading", { name: "CoderAI 学堂" });
  await expect(launchHeading).toBeFocused();

  await page.keyboard.press("Tab");
  const studentEntry = page.getByRole("button", { name: /学生端/ });
  await expect(studentEntry).toBeFocused();
  const outline = await studentEntry.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).toBe("solid");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "学生登录" })).toBeFocused();
  await page.keyboard.press("Tab");
  const username = page.getByLabel("用户名");
  await expect(username).toBeFocused();
  await username.fill(STUDENT_USERNAME);
  await page.keyboard.press("Tab");
  const password = page.getByLabel("密码");
  await expect(password).toBeFocused();
  await password.fill(STUDENT_PASSWORD);
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "课堂通知" })).toBeFocused();

  const skipLink = page.getByRole("link", { name: "跳到主要内容" });
  await skipLink.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await expect(page).toHaveURL(/#\/student\/workspace\/notifications$/);

  const courseMenuItem = page.getByRole("menuitem", { name: "课程学习" });
  await courseMenuItem.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/student\/courses$/);
  await expect(page.getByRole("heading", { name: "课程学习" })).toBeFocused();
});

test("学生学习工作台使用二级导航并兼容旧工作流地址", async ({ page, request }) => {
  const { studentAuth } = await loginData(request);
  await page.route("**/api/classes/tasks", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ tasks: [] }),
  }));
  await setStudentAuth(page, studentAuth);
  for (const label of ["课堂通知", "课堂素材", "文字生成", "图片生成", "视频生成", "工作流生成"]) {
    await expect(page.getByRole("menuitem", { name: label })).toBeVisible();
  }
  await expect(page.getByText("编程助手", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "提交作品" })).toHaveCount(0);

  await page.getByRole("menuitem", { name: "课堂素材" }).click();
  await expect(page).toHaveURL(/#\/student\/workspace\/materials$/);
  await expect(page.getByRole("heading", { name: "课堂素材" })).toBeVisible();
  await page.getByRole("menuitem", { name: "文字生成" }).click();
  await expect(page).toHaveURL(/#\/student\/workspace\/text$/);
  await expect(page.getByRole("heading", { name: "文字生成" })).toBeVisible();
  await expect(page.getByText("代码解释", { exact: true })).toHaveCount(0);

  await page.goto("/#/student/workflows");
  await expect(page).toHaveURL(/#\/student\/workspace\/workflow$/);
  await expect(page.getByRole("heading", { name: "工作流制作" })).toBeVisible();
  await expect(page.getByText("代码解释", { exact: true })).toHaveCount(0);
});

test("学生端和管理员端适配目标 Windows 分辨率及高 DPI", async ({ browser, request }) => {
  const { studentAuth, teacherAuth } = await loginData(request);
  for (const viewport of viewportCases) {
    await test.step(viewport.name, async () => {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.deviceScaleFactor ?? 1
      });
      const page = await context.newPage();
      await setStudentAuth(page, studentAuth);
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: `test-results/layout-student-${viewport.name}.png`, fullPage: true });

      await page.evaluate(() => localStorage.clear());
      await setTeacherAuth(page, teacherAuth);
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: `test-results/layout-admin-${viewport.name}.png`, fullPage: true });
      await context.close();
    });
  }
});

test("课程目录、排课表和管理员 PDF 抽屉适配目标 Windows 分辨率及高 DPI", async ({ browser, request }) => {
  test.setTimeout(180_000);
  const { studentAuth, teacherAuth, packageTitle, courseTitle } = await seedResponsiveCurriculum(request);

  for (const viewport of viewportCases) {
    await test.step(viewport.name, async () => {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: viewport.deviceScaleFactor ?? 1,
      });
      const page = await context.newPage();

      await setTeacherAuth(page, teacherAuth);
      const packageMenuItem = page.getByRole("menuitem").filter({ hasText: packageTitle });
      await expect(packageMenuItem).toBeVisible();
      await packageMenuItem.click();
      await expect(page).toHaveURL(/#\/admin\/courses\?package=\d+$/);
      await expect(page.getByText(packageTitle, { exact: true }).first()).toBeVisible();
      await expect(page.getByRole("heading", { name: courseTitle })).toBeVisible();
      await expect(page.getByText("小学低龄", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("小学高龄", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("初中高中", { exact: true }).first()).toBeVisible();
      await expect(page.locator(".curriculumCatalog")).toHaveCount(0);
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: `test-results/layout-curriculum-${viewport.name}.png`, fullPage: true });

      const slidesSlot = page.locator(".materialSlot").filter({ hasText: "课堂PPT" });
      await slidesSlot.getByRole("button", { name: "预览", exact: true }).click();
      const drawer = page.locator(".ant-drawer-content-wrapper").last();
      const pdfFrame = drawer.locator("iframe.coursePdfPreview");
      await expect(pdfFrame).toBeVisible();
      await expect.poll(async () => {
        const current = await drawer.boundingBox();
        return current ? current.x + current.width : Number.POSITIVE_INFINITY;
      }).toBeLessThanOrEqual(viewport.width + 1);
      const bounds = await drawer.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
      await page.screenshot({ path: `test-results/layout-course-pdf-${viewport.name}.png`, fullPage: true });
      await page.locator(".ant-drawer-close").last().click();

      await page.getByRole("menuitem", { name: "排课记录" }).click();
      await expect(page.locator(".ant-table-row").filter({ hasText: courseTitle })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: `test-results/layout-schedules-${viewport.name}.png`, fullPage: true });

      await page.evaluate(() => localStorage.clear());
      await setStudentAuth(page, studentAuth);
      await page.getByRole("menuitem", { name: "课程学习" }).click();
      await page.locator(".studentScheduleItem").filter({ hasText: courseTitle }).click();
      await expectNoHorizontalOverflow(page);
      await expect(page.locator(".studentMaterial")).toHaveCount(1);
      await expect(page.locator(".studentMaterial").filter({ hasText: "课堂PPT" })).toHaveCount(0);
      await expect(page.locator(".studentMaterial").filter({ hasText: "成果包" })).toHaveCount(0);
      await expect(page.locator("iframe.coursePdfPreview")).toHaveCount(0);
      const starterCard = page.locator(".studentMaterial").filter({ hasText: "工程包" });
      await expect(starterCard.getByRole("button", { name: "查看", exact: true })).toBeVisible();
      await starterCard.getByRole("button", { name: "在线填写与编辑", exact: true }).click();
      const workspaceDrawer = page.locator(".ant-drawer-content-wrapper").last();
      const workspaceEditor = workspaceDrawer.getByLabel("工程包 Markdown 编辑器");
      await expect(workspaceEditor).toBeVisible();
      const workspaceContent = `# 我的课堂工程\n\n${viewport.name} 已完成在线编辑。`;
      await workspaceEditor.fill(workspaceContent);
      await workspaceDrawer.getByRole("button", { name: "保存", exact: true }).click();
      await expect(workspaceDrawer.getByText(/保存于/)).toBeVisible();
      await workspaceDrawer.locator(".ant-segmented-item").filter({ hasText: "预览" }).click();
      await expect(workspaceDrawer.getByRole("heading", { name: "我的课堂工程" })).toBeVisible();
      await page.screenshot({ path: `test-results/layout-student-course-${viewport.name}.png`, fullPage: true });
      await context.close();
    });
  }
});
