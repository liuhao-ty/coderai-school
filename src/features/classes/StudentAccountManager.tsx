import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Col,
  Drawer,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { Archive, FileDown, KeyRound, Pencil, RotateCcw, Search, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { Classroom, SchoolStage } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { SCHOOL_STAGE_OPTIONS, schoolStageLabel } from "../../lib/schoolStages";
import type { StudentProfile } from "../../types";


const { Text, Paragraph } = Typography;
const DEFAULT_STUDENT_PASSWORD = "bcm123456";

type StudentMode = "teacher" | "admin";
type StatusFilter = "all" | "active" | "disabled" | "archived";

export function StudentAccountManager({
  mode,
  students,
  classrooms,
  onRefresh,
}: {
  mode: StudentMode;
  students: StudentProfile[];
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const { message, modal } = AntApp.useApp();
  const [editForm] = Form.useForm();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [targetClassroomId, setTargetClassroomId] = useState<number>();
  const [editingStudent, setEditingStudent] = useState<StudentProfile | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  const filteredStudents = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return students.filter((student) => {
      const matchesSearch = !keyword || `${student.name} ${student.username} ${student.classroom_name || ""}`.toLowerCase().includes(keyword);
      const matchesStatus = statusFilter === "all"
        || (statusFilter === "active" && student.account_status === "active")
        || (statusFilter === "disabled" && student.account_status === "disabled")
        || (statusFilter === "archived" && student.account_status === "archived");
      return matchesSearch && matchesStatus;
    });
  }, [search, statusFilter, students]);

  const classroomOptions = classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }));
  const activeCount = students.filter((student) => student.account_status === "active").length;
  const disabledCount = students.filter((student) => student.account_status === "disabled").length;
  const archivedCount = students.filter((student) => student.account_status === "archived").length;

  const showStudentCredential = (title: string, username: string, password = DEFAULT_STUDENT_PASSWORD) => {
    modal.info({
      title,
      width: 520,
      okText: "知道了",
      content: (
        <Space direction="vertical" size={12} className="fullWidth">
          <Alert type="info" showIcon message="学生初始和重置密码由机构统一设置，不强制首次修改。" />
          <div><Text type="secondary">用户名</Text><Paragraph copyable strong>{username}</Paragraph></div>
          <div><Text type="secondary">当前密码</Text><Paragraph copyable code>{password}</Paragraph></div>
        </Space>
      ),
    });
  };

  const resetPassword = async (student: StudentProfile) => {
    setBusyId(student.id);
    try {
      const response = await api.post(`/api/students/${student.id}/reset-password`);
      await onRefresh();
      showStudentCredential("学生密码已重置", student.username, response.data.temporary_password);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusyId(null);
    }
  };

  const updateClassroom = async (student: StudentProfile, classroomId?: number) => {
    setBusyId(student.id);
    try {
      await api.put(`/api/students/${student.id}/classroom`, { classroom_id: classroomId ?? null });
      await onRefresh();
      message.success(classroomId ? "学员班级已更新" : "学员已设为未分班");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusyId(null);
    }
  };

  const batchTransfer = async () => {
    if (!selectedIds.length || !targetClassroomId) {
      message.warning("请先选择学员和目标班级");
      return;
    }
    setSaving(true);
    try {
      await api.post("/api/students/batch-transfer", { student_ids: selectedIds, classroom_id: targetClassroomId });
      setSelectedIds([]);
      await onRefresh();
      message.success("选中学员已转入目标班级");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const runAdminBatch = async (endpoint: string, successMessage: string, needsClassroom = false) => {
    if (!selectedIds.length) {
      message.warning("请先选择学员");
      return;
    }
    if (needsClassroom && !targetClassroomId) {
      message.warning("请选择目标班级");
      return;
    }
    setSaving(true);
    try {
      await api.post(endpoint, {
        student_ids: selectedIds,
        ...(needsClassroom ? { classroom_id: targetClassroomId } : {}),
      });
      setSelectedIds([]);
      await onRefresh();
      message.success(successMessage);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const exportStudents = async () => {
    try {
      const response = await api.get("/api/students/export", { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "coderai-students.csv";
      anchor.click();
      URL.revokeObjectURL(url);
      message.success("学生账号 CSV 已导出");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const openEdit = (student: StudentProfile) => {
    setEditingStudent(student);
    editForm.setFieldsValue({
      name: student.name,
      username: student.username,
      classroom_id: student.classroom_id ?? undefined,
      age_level: student.age_level,
      active: student.active,
    });
  };

  const saveStudent = async (values: { name: string; username: string; classroom_id?: number; age_level: SchoolStage; active: boolean }) => {
    if (!editingStudent) return;
    setSaving(true);
    try {
      await api.put(`/api/students/${editingStudent.id}`, { ...values, classroom_id: values.classroom_id ?? null });
      setEditingStudent(null);
      await onRefresh();
      message.success("学生账号资料已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const toggleVisibleSelection = (checked: boolean) => {
    const visibleIds = filteredStudents.map((student) => student.id);
    setSelectedIds((current) => checked
      ? Array.from(new Set([...current, ...visibleIds]))
      : current.filter((id) => !visibleIds.includes(id)));
  };

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      <Alert
        type="info"
        showIcon
        message={mode === "admin" ? "全机构学员信息维护" : "全机构学员列表"}
        description={mode === "admin"
          ? "在这里维护学生资料、登录状态、班级和密码；新增学生请到“系统管理 → 账号管理 → 学生开户”。"
          : "教师可以查询全部学员、调整班级并重置密码，不维护账号资料和状态。"}
      />

      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}><Card><Statistic title="学生总数" value={students.length} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="正常启用" value={activeCount} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="已停用" value={disabledCount} /></Card></Col>
        <Col xs={12} md={6}><Card><Statistic title="已归档" value={archivedCount} /></Card></Col>
      </Row>

      <Card title={<IconTitle icon={<UsersRound size={18} />} text="学员列表" />}>
        <div className="studentBatchToolbar">
          <Space wrap>
            <Input prefix={<Search size={15} />} allowClear aria-label="搜索学员" placeholder="搜索姓名、用户名或班级" value={search} onChange={(event) => setSearch(event.target.value)} />
            <Segmented value={statusFilter} onChange={(value) => setStatusFilter(value as StatusFilter)} options={[
              { value: "all", label: "全部" },
              { value: "active", label: "启用" },
              { value: "disabled", label: "停用" },
              { value: "archived", label: "归档" },
            ]} />
          </Space>
          <Space wrap>
            <Checkbox
              checked={filteredStudents.length > 0 && filteredStudents.every((student) => selectedIds.includes(student.id))}
              indeterminate={filteredStudents.some((student) => selectedIds.includes(student.id)) && !filteredStudents.every((student) => selectedIds.includes(student.id))}
              onChange={(event) => toggleVisibleSelection(event.target.checked)}
            >选择当前列表</Checkbox>
            <Select className="studentBatchClassroom" value={targetClassroomId} onChange={setTargetClassroomId} placeholder="目标班级" options={classroomOptions} />
            <Button disabled={!selectedIds.length} loading={saving} onClick={() => void batchTransfer()}>批量转班</Button>
            {mode === "admin" && (
              <>
                <Button icon={<FileDown size={14} />} onClick={() => void exportStudents()}>导出全部账号</Button>
                <Button disabled={!selectedIds.length} loading={saving} icon={<RotateCcw size={14} />} onClick={() => void runAdminBatch("/api/students/batch-restore", "学生账号已恢复", true)}>恢复</Button>
                <Popconfirm title="归档选中的学生账号？" description="归档后不能登录，历史作品和提交会保留。" onConfirm={() => runAdminBatch("/api/students/batch-archive", "学生账号已归档")}>
                  <Button danger disabled={!selectedIds.length} loading={saving} icon={<Archive size={14} />}>归档</Button>
                </Popconfirm>
              </>
            )}
            <Text type="secondary">已选 {selectedIds.length} 人</Text>
          </Space>
        </div>

        <List
          dataSource={filteredStudents}
          locale={{ emptyText: "暂无符合条件的学生" }}
          renderItem={(student) => (
            <List.Item
              actions={[
                <Select
                  key="classroom"
                  size="small"
                  allowClear
                  aria-label={`设置 ${student.name} 的班级`}
                  placeholder="未分班"
                  value={student.classroom_id ?? undefined}
                  disabled={student.account_status === "archived" || busyId === student.id}
                  options={classroomOptions}
                  onChange={(value) => void updateClassroom(student, value)}
                />,
                <Popconfirm key="password" title="重置学生密码？" description={`密码将固定重置为 ${DEFAULT_STUDENT_PASSWORD}，旧会话立即失效。`} onConfirm={() => resetPassword(student)}>
                  <Button size="small" icon={<KeyRound size={14} />} loading={busyId === student.id} disabled={!student.username}>重置密码</Button>
                </Popconfirm>,
                ...(mode === "admin" ? [<Button key="edit" size="small" icon={<Pencil size={14} />} onClick={() => openEdit(student)}>编辑账号</Button>] : []),
              ]}
            >
              <List.Item.Meta
                title={(
                  <Space wrap>
                    <Checkbox
                      aria-label={`选择学生 ${student.name}`}
                      checked={selectedIds.includes(student.id)}
                      onChange={(event) => setSelectedIds((current) => event.target.checked ? [...current, student.id] : current.filter((id) => id !== student.id))}
                    />
                    <Text strong>{student.name}</Text>
                    {student.username && <Text code>{student.username}</Text>}
                  </Space>
                )}
                description={(
                  <Space direction="vertical" size={5}>
                    <Space wrap>
                      <Tag color="blue">{student.classroom_name || (student.archived_classroom_name ? `原班级：${student.archived_classroom_name}` : "未分配班级")}</Tag>
                      <Tag>{student.school_stage_label || schoolStageLabel(student.age_level)}</Tag>
                      <Tag color={student.account_status === "active" ? "green" : student.account_status === "disabled" ? "red" : "default"}>
                        {student.account_status === "active" ? "已启用" : student.account_status === "disabled" ? "已停用" : "已归档"}
                      </Tag>
                    </Space>
                    <Text type="secondary">最近登录：{student.last_login_at ? formatBeijingTime(student.last_login_at) : "从未登录"}</Text>
                  </Space>
                )}
              />
            </List.Item>
          )}
        />
      </Card>

      <Drawer title={editingStudent ? `编辑学生账号：${editingStudent.name}` : "编辑学生账号"} open={Boolean(editingStudent)} width={520} onClose={() => setEditingStudent(null)}>
        <Form name="student-account-edit" form={editForm} layout="vertical" onFinish={saveStudent}>
          {editingStudent?.account_status === "archived" && <Alert className="mb16" type="info" showIcon message="归档账号需通过列表中的批量恢复操作重新启用。" />}
          <Form.Item name="name" label="学生姓名" rules={[{ required: true, message: "请输入学生姓名" }]}><Input /></Form.Item>
          <Form.Item name="username" label="唯一用户名" rules={[
            { required: true, message: "请输入用户名" },
            { pattern: /^[a-z0-9][a-z0-9._-]{3,39}$/, message: "请输入 4-40 位有效用户名" },
          ]}><Input disabled={editingStudent?.account_status === "archived"} /></Form.Item>
          <Form.Item name="classroom_id" label="所属班级"><Select allowClear disabled={editingStudent?.account_status === "archived"} options={classroomOptions} /></Form.Item>
          <Form.Item name="age_level" label="学龄分类"><Select options={SCHOOL_STAGE_OPTIONS} /></Form.Item>
          <Form.Item name="active" label="账号状态"><Select disabled={editingStudent?.account_status === "archived"} options={[{ value: true, label: "启用" }, { value: false, label: "停用" }]} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={saving} block>保存账号资料</Button>
        </Form>
      </Drawer>
    </Space>
  );
}
