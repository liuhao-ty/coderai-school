import {
  Alert,
  App,
  Button,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { CalendarClock, Clock3, RefreshCcw, RotateCcw, Send, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";

import type { Classroom, CoursePackageItem, CourseScheduleItem } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { schoolStageLabel } from "../../lib/schoolStages";
import type { StudentProfile } from "../../types";


const { Text, Title } = Typography;

type ScheduleFormValues = {
  curriculum: string;
  target_ids: number[];
  starts_at: Dayjs;
  due_at?: Dayjs;
  interval_days?: number;
};

type UpdateFormValues = { starts_at: Dayjs; due_at?: Dayjs };

function scheduleState(status: CourseScheduleItem["status"]) {
  if (status === "active") return { color: "green", label: "学习中" };
  if (status === "overdue") return { color: "red", label: "已逾期" };
  if (status === "canceled") return { color: "default", label: "已取消" };
  return { color: "blue", label: "未开始" };
}

export function ScheduleManager({
  audience,
  packages,
  schedules,
  students,
  classrooms,
  onRefresh,
}: {
  audience: "admin" | "teacher";
  packages: CoursePackageItem[];
  schedules: CourseScheduleItem[];
  students: StudentProfile[];
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const { message, modal } = App.useApp();
  const [mode, setMode] = useState<"student" | "classroom">("student");
  const [form] = Form.useForm<ScheduleFormValues>();
  const [updateForm] = Form.useForm<UpdateFormValues>();
  const [editing, setEditing] = useState<CourseScheduleItem | null>(null);
  const [busy, setBusy] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("current");
  const [targetFilter, setTargetFilter] = useState<number | undefined>();

  const publishedPackages = useMemo(() => packages.filter((item) => item.status === "published"), [packages]);
  const activeStudents = useMemo(() => students.filter((item) => item.active && item.account_status !== "archived"), [students]);
  const availableClassrooms = useMemo(
    () => audience === "admin" ? classrooms : classrooms.filter((item) => item.can_manage),
    [audience, classrooms],
  );
  const curriculumOptions = useMemo(() => publishedPackages.flatMap((item) => [
    { label: `整包 · ${item.title}（${item.courses.length} 课）· ${item.school_stages.map(schoolStageLabel).join("、")}`, value: `package:${item.id}` },
    ...item.courses.map((course) => ({ label: `${item.title} · ${course.title} · ${item.school_stages.map(schoolStageLabel).join("、")}`, value: `course:${course.id}` })),
  ]), [publishedPackages]);

  const visibleSchedules = useMemo(() => schedules.filter((item) => {
    if (statusFilter === "current" && item.status === "canceled") return false;
    if (statusFilter !== "current" && statusFilter !== "all" && item.status !== statusFilter) return false;
    if (targetFilter && item.target_id !== targetFilter) return false;
    return true;
  }), [schedules, statusFilter, targetFilter]);

  const targetOptions = mode === "student"
    ? activeStudents.map((student) => ({ value: student.id, label: `${student.name} · ${student.username}${student.classroom_name ? ` · ${student.classroom_name}` : ""}` }))
    : availableClassrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }));

  const createSchedules = async (values: ScheduleFormValues) => {
    const [scope, rawId] = values.curriculum.split(":");
    const id = Number(rawId);
    const courses = scope === "package"
      ? publishedPackages.find((item) => item.id === id)?.courses || []
      : publishedPackages.flatMap((item) => item.courses).filter((item) => item.id === id);
    if (!courses.length) {
      message.error("请选择可排课的已发布课程");
      return;
    }
    const intervalDays = Math.max(0, values.interval_days || 0);
    const items = values.target_ids.flatMap((targetId) => courses.map((course, index) => ({
      course_id: course.id,
      target_type: mode,
      target_id: targetId,
      starts_at: values.starts_at.add(index * intervalDays, "day").toISOString(),
      due_at: values.due_at ? values.due_at.add(index * intervalDays, "day").toISOString() : null,
    })));
    if (items.length > 500) {
      message.error("单次排课不能超过 500 条，请缩小目标范围");
      return;
    }
    setBusy("create");
    try {
      const response = await api.post("/api/course-schedules/batch", { items });
      const conflicts = response.data.conflicts || [];
      message.success(`已创建 ${response.data.created} 条排课`);
      form.resetFields();
      await onRefresh();
      if (conflicts.length) {
        modal.warning({
          title: `发现 ${conflicts.length} 处时间重叠`,
          content: (
            <Space direction="vertical" size={6}>
              {conflicts.slice(0, 8).map((item: Record<string, string | number>, index: number) => (
                <Text key={`${item.item_index}-${index}`}>{item.target_name}：{item.course_title} 与 {item.conflicts_with_course_title || "另一课程"} 时间重叠</Text>
              ))}
              {conflicts.length > 8 && <Text type="secondary">还有 {conflicts.length - 8} 处重叠，可在排课列表中逐条调整。</Text>}
            </Space>
          ),
        });
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const saveTime = async (values: UpdateFormValues) => {
    if (!editing) return;
    setBusy(`update-${editing.id}`);
    try {
      const response = await api.put(`/api/course-schedules/${editing.id}`, {
        starts_at: values.starts_at.toISOString(),
        due_at: values.due_at?.toISOString() || null,
      });
      setEditing(null);
      message.success("排课时间已更新");
      await onRefresh();
      if ((response.data.conflicts || []).length) message.warning("调整后的时间与其他排课重叠，请再次检查");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const cancelSchedule = async (schedule: CourseScheduleItem) => {
    setBusy(`cancel-${schedule.id}`);
    try {
      await api.post(`/api/course-schedules/${schedule.id}/cancel`, { reason: "由教学人员在排课管理中取消" });
      message.success("排课已取消，历史提交仍会保留");
      await onRefresh();
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const openTimeEditor = (schedule: CourseScheduleItem) => {
    setEditing(schedule);
    updateForm.setFieldsValue({
      starts_at: dayjs(schedule.starts_at),
      due_at: schedule.due_at ? dayjs(schedule.due_at) : undefined,
    });
  };

  return (
    <Space direction="vertical" size={16} className="fullWidth scheduleWorkspace">
      <section className="scheduleComposer">
        <div className="scheduleComposerHeader">
          <div><Title level={4}>创建排课</Title><Text type="secondary">可选择单门课程或按课程包顺序批量排课</Text></div>
          <Segmented
            value={mode}
            onChange={(value) => { setMode(value as "student" | "classroom"); form.setFieldValue("target_ids", []); }}
            options={[
              { label: "按学员", value: "student", icon: <UsersRound size={15} /> },
              { label: "按班级", value: "classroom", icon: <CalendarClock size={15} /> },
            ]}
          />
        </div>
        {!publishedPackages.length ? (
          <Alert type="warning" showIcon message="暂时没有可排课的已发布课程包" description="管理员发布至少包含一门课程的课程包后，才能创建排课。" />
        ) : mode === "classroom" && !availableClassrooms.length ? (
          <Alert type="warning" showIcon message="当前教师还没有获授权班级" />
        ) : (
          <Form
            form={form}
            layout="vertical"
            onFinish={createSchedules}
            initialValues={{ interval_days: 7 }}
          >
            <div className="scheduleFormGrid">
              <Form.Item name="curriculum" label="课程范围" rules={[{ required: true, message: "请选择课程" }]}>
                <Select showSearch optionFilterProp="label" placeholder="选择整包或单门课程" options={curriculumOptions} />
              </Form.Item>
              <Form.Item name="target_ids" label={mode === "student" ? "学员" : "班级"} rules={[{ required: true, message: "请选择排课目标" }]}>
                <Select mode="multiple" showSearch optionFilterProp="label" maxTagCount="responsive" placeholder={mode === "student" ? "选择启用且未归档学员" : "选择获授权班级"} options={targetOptions} />
              </Form.Item>
              <Form.Item name="starts_at" label="开始时间" rules={[{ required: true, message: "请选择开始时间" }]}>
                <DatePicker showTime format="YYYY-MM-DD HH:mm" className="fullWidth" />
              </Form.Item>
              <Form.Item name="due_at" label="截止时间（可选）">
                <DatePicker showTime format="YYYY-MM-DD HH:mm" className="fullWidth" />
              </Form.Item>
              <Form.Item name="interval_days" label="整包课程间隔">
                <InputNumber min={0} max={90} addonAfter="天" className="fullWidth" />
              </Form.Item>
            </div>
            <Button type="primary" htmlType="submit" icon={<Send size={15} />} loading={busy === "create"}>确认排课</Button>
          </Form>
        )}
      </section>

      <section className="scheduleListSection">
        <div className="scheduleListHeader">
          <div><Title level={4}>排课记录</Title><Text type="secondary">截止后仍允许提交，系统会自动标记逾期</Text></div>
          <Space wrap>
            <Select
              allowClear
              placeholder="筛选目标"
              value={targetFilter}
              onChange={setTargetFilter}
              options={[...activeStudents.map((item) => ({ value: item.id, label: item.name })), ...availableClassrooms.map((item) => ({ value: item.id, label: item.name }))]}
              style={{ width: 160 }}
            />
            <Select
              value={statusFilter}
              onChange={setStatusFilter}
              options={[
                { value: "current", label: "当前排课" },
                { value: "scheduled", label: "未开始" },
                { value: "active", label: "学习中" },
                { value: "overdue", label: "已逾期" },
                { value: "all", label: "全部状态" },
              ]}
              style={{ width: 130 }}
            />
            <Button icon={<RefreshCcw size={15} />} onClick={() => void onRefresh()}>刷新</Button>
          </Space>
        </div>
        <Table<CourseScheduleItem>
          rowKey="id"
          dataSource={visibleSchedules}
          pagination={{ pageSize: 12, showSizeChanger: false }}
          scroll={{ x: 980 }}
          locale={{ emptyText: "暂无排课记录" }}
          columns={[
            {
              title: "课程",
              dataIndex: "course_title",
              width: 260,
              render: (_, item) => (
                <Space direction="vertical" size={2}>
                  <Text strong>{item.course_title}</Text>
                  <Text type="secondary">{item.package_title}</Text>
                  {item.course_access_status === "revoked" && <Tag color="orange">课程权限已撤销 · 历史只读</Tag>}
                </Space>
              ),
            },
            {
              title: "目标",
              width: 150,
              render: (_, item) => <Space direction="vertical" size={2}><Tag color={item.target_type === "student" ? "blue" : "cyan"}>{item.target_type === "student" ? "学员" : "班级"}</Tag><Text>{item.target_name}</Text></Space>,
            },
            {
              title: "时间",
              width: 230,
              render: (_, item) => <Space direction="vertical" size={2}><Text><Clock3 size={13} /> {formatBeijingTime(item.starts_at)}</Text><Text type="secondary">截止：{item.due_at ? formatBeijingTime(item.due_at) : "不设截止"}</Text></Space>,
            },
            {
              title: "状态",
              width: 110,
              render: (_, item) => <Space direction="vertical" size={2}><Tag color={scheduleState(item.status).color}>{scheduleState(item.status).label}</Tag><Text type="secondary">{item.submission_count} 份提交</Text></Space>,
            },
            {
              title: "排课人",
              dataIndex: "created_by_name",
              width: 120,
            },
            {
              title: "操作",
              fixed: "right",
              width: 190,
              render: (_, item) => item.can_manage && item.status !== "canceled" ? (
                <Space>
                  <Button size="small" icon={<RotateCcw size={14} />} onClick={() => openTimeEditor(item)}>调整</Button>
                  <Popconfirm title="取消这条排课？" description="历史提交会保留，学生将不能继续提交。" onConfirm={() => cancelSchedule(item)}>
                    <Button size="small" danger loading={busy === `cancel-${item.id}`}>取消</Button>
                  </Popconfirm>
                </Space>
              ) : <Text type="secondary">{item.course_access_status === "revoked" ? "权限已撤销 · 只读" : "只读"}</Text>,
            },
          ]}
        />
      </section>

      <Modal title={editing ? `调整排课：${editing.course_title}` : "调整排课"} open={Boolean(editing)} onCancel={() => setEditing(null)} footer={null} destroyOnClose>
        <Form form={updateForm} layout="vertical" onFinish={saveTime}>
          <Form.Item name="starts_at" label="开始时间" rules={[{ required: true, message: "请选择开始时间" }]}><DatePicker showTime format="YYYY-MM-DD HH:mm" className="fullWidth" /></Form.Item>
          <Form.Item name="due_at" label="截止时间（可选）"><DatePicker showTime format="YYYY-MM-DD HH:mm" className="fullWidth" /></Form.Item>
          <Form.Item name="note" label="调整说明（本地备注）"><Input placeholder="可选，不会修改课程作业要求" /></Form.Item>
          <Button type="primary" htmlType="submit" loading={Boolean(editing && busy === `update-${editing.id}`)} block>保存时间</Button>
        </Form>
      </Modal>
    </Space>
  );
}
