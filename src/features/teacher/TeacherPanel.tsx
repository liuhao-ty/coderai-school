import { Button, Card, Progress, Space, Table, Tag, Typography } from "antd";
import {
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  GraduationCap,
  Inbox,
  UsersRound,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

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

export function TeacherPanel({
  section,
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
  const navigate = useNavigate();
  const isAdmin = audience === "admin";
  const managedClassrooms = isAdmin ? classrooms : classrooms.filter((classroom) => classroom.can_manage);
  const managedClassroomIds = new Set(managedClassrooms.map((classroom) => classroom.id));
  const dashboardStudents = isAdmin ? students : students.filter((student) => student.classroom_id != null && managedClassroomIds.has(student.classroom_id));
  const pendingSubmissions = submissions.filter((submission) => submission.status === "submitted").length;
  const reviewedSubmissions = submissions.filter((submission) => submission.status === "reviewed").length;
  const returnedSubmissions = submissions.filter((submission) => submission.status === "returned").length;
  const publishedPackages = coursePackages.filter((item) => item.status === "published").length;
  const activeSchedules = courseSchedules
    .filter((schedule) => schedule.status !== "canceled")
    .sort((left, right) => new Date(left.starts_at).getTime() - new Date(right.starts_at).getTime());
  const recentSubmissions = submissions.slice(0, 10);
  const classroomProgress = managedClassrooms.map((classroom) => {
    const roster = dashboardStudents.filter((student) => student.classroom_id === classroom.id);
    const classroomSubmissions = submissions.filter((submission) => submission.classroom_id === classroom.id);
    const classroomReviewed = classroomSubmissions.filter((submission) => submission.status === "reviewed").length;
    return {
      classroom,
      studentCount: roster.length,
      submissionCount: classroomSubmissions.length,
      reviewedCount: classroomReviewed,
      percent: classroomSubmissions.length ? Math.round((classroomReviewed / classroomSubmissions.length) * 100) : 0,
    };
  });
  const currentDate = formatBeijingTime(new Date().toISOString()).slice(0, 10);
  const roleBasePath = isAdmin ? "/admin" : "/teacher";
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
      <div className={`teacherHero ${section === "overview" ? "teachingLedgerHeader" : ""}`}>
        <div>
          <Title level={2}>{copy.title}</Title>
          <Text>{copy.description}</Text>
        </div>
        {section === "overview" ? (
          <Space wrap className="teachingLedgerActions">
            <span className="teachingLedgerDate"><CalendarDays size={16} />{currentDate}</span>
            <Button type="primary" icon={<ClipboardCheck size={16} />} onClick={() => navigate(`${roleBasePath}/submissions`)}>批改作业</Button>
            <Button icon={<CalendarDays size={16} />} onClick={() => navigate(`${roleBasePath}/schedules/records`)}>查看排课</Button>
          </Space>
        ) : (
          <Space wrap>
            <Tag color={isAdmin ? "purple" : "blue"}>{isAdmin ? "全机构班级" : "我的班级"} {managedClassrooms.length}</Tag>
            <Tag color={pendingSubmissions ? "orange" : "green"}>待批改 {pendingSubmissions}</Tag>
          </Space>
        )}
      </div>

      {section === "overview" && (
        <div className="teachingLedger">
          <section className="teachingMetricStrip" aria-label="教学统计">
            <div className="teachingMetricItem metricBlue">
              <span className="teachingMetricIcon"><GraduationCap size={22} /></span>
              <span><Text type="secondary">{isAdmin ? "全机构班级" : "我的班级"}</Text><strong>{managedClassrooms.length}</strong></span>
            </div>
            <div className="teachingMetricItem metricGreen">
              <span className="teachingMetricIcon"><UsersRound size={22} /></span>
              <span><Text type="secondary">{isAdmin ? "全机构学员" : "我的学员"}</Text><strong>{dashboardStudents.length}</strong></span>
            </div>
            <div className="teachingMetricItem metricAmber">
              <span className="teachingMetricIcon"><Inbox size={22} /></span>
              <span><Text type="secondary">待批改</Text><strong>{pendingSubmissions}</strong></span>
            </div>
            <div className="teachingMetricItem metricCoral">
              <span className="teachingMetricIcon"><CheckCircle2 size={22} /></span>
              <span><Text type="secondary">已批改</Text><strong>{reviewedSubmissions}</strong></span>
            </div>
          </section>

          <div className="teachingLedgerLayout">
            <aside className="teachingLedgerSide">
              <Card className="ledgerPanel" title={<IconTitle icon={<CalendarDays size={18} />} text="近期课务" />}>
                {activeSchedules.length ? (
                  <div className="ledgerScheduleList">
                    {activeSchedules.slice(0, 4).map((schedule) => (
                      <div className="ledgerScheduleItem" key={schedule.id}>
                        <span className={`ledgerScheduleDot schedule-${schedule.status}`} />
                        <div>
                          <Text strong>{schedule.course_title}</Text>
                          <Text type="secondary">{schedule.target_name}</Text>
                          <Text type="secondary">{formatBeijingTime(schedule.starts_at)}</Text>
                        </div>
                        <Tag color={schedule.status === "active" ? "green" : schedule.status === "overdue" ? "red" : "blue"}>
                          {schedule.status === "active" ? "进行中" : schedule.status === "overdue" ? "已逾期" : "待开始"}
                        </Tag>
                      </div>
                    ))}
                  </div>
                ) : <EmptyState title="暂无排课" description="新建排课后会显示近期课务" />}
              </Card>
              <Card className="ledgerPanel" title={<IconTitle icon={<AlertTriangle size={18} />} text="课务提醒" />}>
                <div className="ledgerReminderList">
                  <button type="button" onClick={() => navigate(`${roleBasePath}/submissions`)}>
                    <span><Inbox size={17} />待批改作业</span><strong>{pendingSubmissions}</strong>
                  </button>
                  <button type="button" onClick={() => navigate(`${roleBasePath}/submissions`)}>
                    <span><AlertTriangle size={17} />需修改作业</span><strong>{returnedSubmissions}</strong>
                  </button>
                  <button type="button" onClick={() => navigate(`${roleBasePath}/schedules/records`)}>
                    <span><Clock3 size={17} />逾期排课</span><strong>{courseSchedules.filter((item) => item.status === "overdue").length}</strong>
                  </button>
                </div>
              </Card>
            </aside>

            <div className="teachingLedgerMain">
              <Card
                className="ledgerPanel classProgressPanel"
                title={<IconTitle icon={<GraduationCap size={18} />} text="班级批改进度" />}
                extra={<Text type="secondary">已发布课程包 {publishedPackages}</Text>}
              >
                {classroomProgress.length ? (
                  <div className="classProgressList">
                    {classroomProgress.slice(0, 4).map((item) => (
                      <div className="classProgressRow" key={item.classroom.id}>
                        <div>
                          <Text strong>{item.classroom.name}</Text>
                          <Text type="secondary">{item.studentCount} 名学员 · {item.submissionCount} 份提交</Text>
                        </div>
                        <Progress percent={item.percent} showInfo={false} strokeColor="#1677df" trailColor="#e8edf3" />
                        <Text type="secondary">已批改 {item.reviewedCount}/{item.submissionCount}</Text>
                      </div>
                    ))}
                  </div>
                ) : <EmptyState title="暂无班级" description={isAdmin ? "请先创建班级并完成分班" : "管理员授权班级后会显示进度"} />}
              </Card>

              <Card
                className="ledgerPanel submissionLedgerPanel"
                title={<IconTitle icon={<ClipboardCheck size={18} />} text="最新提交" />}
                extra={<Text type="secondary">共 {submissions.length} 条</Text>}
              >
                <Table
                  rowKey="id"
                  size="small"
                  dataSource={recentSubmissions}
                  pagination={recentSubmissions.length > 6 ? { pageSize: 6, showSizeChanger: false, size: "small" } : false}
                  scroll={{ x: 780 }}
                  locale={{ emptyText: <EmptyState title="暂无提交记录" description="学员提交课程作品后会显示在这里" /> }}
                  columns={[
                    { title: "学员", dataIndex: "student_name", key: "student", width: 110, ellipsis: true },
                    { title: "课程 / 作业", dataIndex: "task_title", key: "task", ellipsis: true },
                    { title: "班级", dataIndex: "classroom_name", key: "classroom", width: 130, ellipsis: true, render: (value: string) => value || "未分配班级" },
                    { title: "提交时间", dataIndex: "updated_at", key: "time", width: 152, render: (value: string) => formatBeijingTime(value) },
                    {
                      title: "状态",
                      dataIndex: "status",
                      key: "status",
                      width: 104,
                      render: (value: string) => <Tag color={submissionStatusColor(value)}>{submissionStatusLabel(value)}</Tag>,
                    },
                    {
                      title: "操作",
                      key: "action",
                      width: 82,
                      fixed: "right",
                      render: () => <Button type="link" size="small" onClick={() => navigate(`${roleBasePath}/submissions`)}>查看</Button>,
                    },
                  ]}
                />
              </Card>
            </div>
          </div>
        </div>
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
