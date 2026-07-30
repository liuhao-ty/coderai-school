import { Card, Col, List, Row, Space, Statistic, Tag, Typography } from "antd";
import { ClipboardList, GraduationCap, Library, ShieldCheck } from "lucide-react";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type {
  Classroom,
  CoursePackageItem,
  CourseScheduleItem,
  Project,
  TaskSubmission,
  TeacherSectionKey,
} from "../../domain-types";
import { submissionStatusColor, submissionStatusLabel } from "../../lib/domain";
import { formatBeijingTime } from "../../lib/format";
import type { StudentProfile } from "../../types";
import { ClassroomStudentManager } from "../classes/ClassroomStudentManager";
import { StudentAccountManager } from "../classes/StudentAccountManager";
import { CurriculumManager } from "../courses/CurriculumManager";
import { ScheduleManager } from "../courses/ScheduleManager";
import { SubmissionReviewPanel } from "../submissions/SubmissionReviewPanel";
import { AccountSecurityPanel } from "./AccountSecurityPanel";
import { ModerationCheckPanel } from "./ModerationCheckPanel";


const { Title, Text } = Typography;

const sectionCopy: Record<TeacherSectionKey, { title: string; description: string }> = {
  overview: { title: "教学总览", description: "仅统计你获授权班级、班内学员及这些班级的作品和提交。" },
  classes: { title: "班级管理", description: "查看全机构班级，并维护自己获授权班级及共同授课教师。" },
  students: { title: "学员管理", description: "查询全机构未归档学员，执行分班、批量转班和密码重置。" },
  courses: { title: "课程管理", description: "预览管理员发布的课程包、课堂 PPT、工程包和成果包，用于课前备课。" },
  schedules: { title: "排课管理", description: "按学员或获授权班级安排课程，并维护上课与截止时间。" },
  submissions: { title: "作业提交与批改", description: "查看并批改获授权班级的学员提交。" },
  moderation: { title: "课堂安全检测", description: "检测课堂内容，并复核获授权班级的图片和审核记录。" },
  account: { title: "账号安全", description: "维护当前教师账号的密码、会话和个人操作记录。" },
};

function studentAccessState(student: StudentProfile) {
  if (student.account_status === "archived") return { color: "default", label: "已归档" };
  if (!student.active) return { color: "red", label: "已停用" };
  return { color: "green", label: "可登录" };
}

export function TeacherPanel({
  section,
  projects,
  submissions,
  classrooms,
  students,
  coursePackages,
  courseSchedules,
  scheduleView = "records",
  onRefresh,
  audience = "teacher",
}: {
  section: TeacherSectionKey;
  projects: Project[];
  submissions: TaskSubmission[];
  classrooms: Classroom[];
  students: StudentProfile[];
  coursePackages: CoursePackageItem[];
  courseSchedules: CourseScheduleItem[];
  scheduleView?: "create" | "records";
  onRefresh: () => Promise<void>;
  audience?: "teacher" | "admin";
}) {
  const isAdmin = audience === "admin";
  const managedClassrooms = isAdmin ? classrooms : classrooms.filter((classroom) => classroom.can_manage);
  const managedClassroomIds = new Set(managedClassrooms.map((classroom) => classroom.id));
  const dashboardStudents = isAdmin ? students : students.filter((student) => student.classroom_id != null && managedClassroomIds.has(student.classroom_id));
  const pendingSubmissions = submissions.filter((submission) => submission.status === "submitted").length;
  const recentSubmissions = submissions.slice(0, 5);
  const studentProgress = dashboardStudents.map((student) => {
    const studentProjects = projects.filter((project) => project.user_id === student.id);
    const studentSubmissions = submissions.filter((submission) => submission.user_id === student.id);
    return {
      student,
      projectCount: studentProjects.length,
      submissionCount: studentSubmissions.length,
      reviewedCount: studentSubmissions.filter((submission) => submission.status === "reviewed").length,
      latestAt: studentSubmissions[0]?.updated_at || "",
    };
  });
  const copy = isAdmin
    ? {
        overview: { title: "教学总览", description: "统计全机构班级、学员、作品、任务和作业提交。" },
        classes: { title: "班级管理", description: "维护全机构班级及授课教师分配。" },
        students: { title: "学员管理", description: "维护全机构学生账号、班级和登录状态。" },
        courses: { title: "课程管理", description: "创建课程包、维护课程规则与可选资料，并控制发布和归档。" },
        schedules: { title: "排课管理", description: "为全机构学员或班级安排课程，并处理时间冲突和取消。" },
        submissions: { title: "作业提交与批改", description: "查看并批改全机构学员提交。" },
        moderation: { title: "课堂安全检测", description: "管理内容安全设置并复核全机构审核记录。" },
        account: sectionCopy.account,
      }[section]
    : sectionCopy[section];

  return (
    <div className="page">
      <div className="teacherHero">
        <div>
          <Title level={2}>{copy.title}</Title>
          <Text>{copy.description}</Text>
        </div>
        <Space wrap>
          <Tag color={isAdmin ? "purple" : "blue"}>{isAdmin ? "全机构班级" : "我的班级"} {managedClassrooms.length}</Tag>
          <Tag color={pendingSubmissions ? "orange" : "green"}>待批改 {pendingSubmissions}</Tag>
        </Space>
      </div>

      {section === "overview" && (
        <Space direction="vertical" size={16} className="fullWidth">
          <Row gutter={[12, 12]}>
            <Col xs={12} lg={6}><Card className="teacherMetric"><Statistic title={isAdmin ? "全机构班级" : "我的班级"} value={managedClassrooms.length} /></Card></Col>
            <Col xs={12} lg={6}><Card className="teacherMetric"><Statistic title={isAdmin ? "全机构学员" : "我的学员"} value={dashboardStudents.length} /></Card></Col>
            <Col xs={12} lg={6}><Card className="teacherMetric"><Statistic title="学员作品" value={projects.length} /></Card></Col>
            <Col xs={12} lg={6}><Card className="teacherMetric"><Statistic title="待批改" value={pendingSubmissions} /></Card></Col>
          </Row>
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={13}>
              <Card title={<IconTitle icon={<ClipboardList size={18} />} text="最近提交" />}>
                <List
                  dataSource={recentSubmissions}
                  locale={{ emptyText: <EmptyState title="暂无提交记录" description="学员提交课堂任务后会显示在这里" /> }}
                  renderItem={(submission) => (
                    <List.Item>
                      <List.Item.Meta
                        title={submission.task_title}
                        description={(
                          <Space wrap>
                            <Tag color="blue">{submission.student_name}</Tag>
                            <Tag color={submissionStatusColor(submission.status)}>{submissionStatusLabel(submission.status)}</Tag>
                            <Text type="secondary">{formatBeijingTime(submission.updated_at)}</Text>
                          </Space>
                        )}
                      />
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
            <Col xs={24} xl={11}>
              <Row gutter={[12, 12]}>
                <Col span={12}><Card><Statistic title="已发布课程包" value={coursePackages.filter((item) => item.status === "published").length} /></Card></Col>
                <Col span={12}><Card><Statistic title="当前排课" value={courseSchedules.length} /></Card></Col>
                <Col span={12}><Card><Statistic title="已批改" value={submissions.filter((item) => item.status === "reviewed").length} /></Card></Col>
                <Col span={12}><Card><Statistic title="需修改" value={submissions.filter((item) => item.status === "returned").length} /></Card></Col>
              </Row>
            </Col>
          </Row>
          <Card title={<IconTitle icon={<GraduationCap size={18} />} text="学员学习进度" />}>
            <List
              dataSource={studentProgress}
              locale={{ emptyText: <EmptyState title="暂无学员" description={isAdmin ? "请先创建学生账号并完成分班" : "当前获授权班级中暂无学员"} /> }}
              renderItem={(item) => {
                const access = studentAccessState(item.student);
                return (
                  <List.Item>
                    <List.Item.Meta
                      title={<Space wrap><Text strong>{item.student.name}</Text><Tag color={access.color}>{access.label}</Tag><Tag>{item.student.classroom_name || "未分配班级"}</Tag></Space>}
                      description={(
                        <Space wrap>
                          <Tag icon={<Library size={13} />} color="blue">作品 {item.projectCount}</Tag>
                          <Tag color="orange">提交 {item.submissionCount}</Tag>
                          <Tag color="green">已批改 {item.reviewedCount}</Tag>
                          <Text type="secondary">{item.latestAt ? `最近提交 ${formatBeijingTime(item.latestAt)}` : "暂无提交"}</Text>
                        </Space>
                      )}
                    />
                  </List.Item>
                );
              }}
            />
          </Card>
        </Space>
      )}

      {section === "classes" && <ClassroomStudentManager classrooms={classrooms} students={students} onRefresh={onRefresh} />}
      {section === "students" && <StudentAccountManager mode={isAdmin ? "admin" : "teacher"} students={students} classrooms={classrooms} onRefresh={onRefresh} />}
      {section === "courses" && <CurriculumManager audience={isAdmin ? "admin" : "teacher"} packages={coursePackages} onRefresh={onRefresh} />}
      {section === "schedules" && <ScheduleManager view={scheduleView} audience={isAdmin ? "admin" : "teacher"} packages={coursePackages} schedules={courseSchedules} students={students} classrooms={classrooms} onRefresh={onRefresh} />}
      {section === "submissions" && <SubmissionReviewPanel submissions={submissions} onRefresh={onRefresh} audience={isAdmin ? "admin" : "teacher"} />}
      {section === "moderation" && <ModerationCheckPanel canManageSettings={isAdmin} />}
      {section === "account" && <AccountSecurityPanel />}
    </div>
  );
}
