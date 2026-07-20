import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Select,
  Space,
  Typography,
} from "antd";
import { FileDown, FileUp, Plus } from "lucide-react";
import { useRef, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { Classroom, SchoolStage } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { SCHOOL_STAGE_OPTIONS } from "../../lib/schoolStages";


const { Paragraph, Text } = Typography;
const DEFAULT_STUDENT_PASSWORD = "bcm123456";

export function StudentRegistrationPanel({
  classrooms,
  onRefresh,
}: {
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const { message, modal } = AntApp.useApp();
  const [form] = Form.useForm();
  const importInputRef = useRef<HTMLInputElement>(null);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [conflictStrategy, setConflictStrategy] = useState<"skip" | "update">("skip");
  const classroomOptions = classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }));

  const showStudentCredential = (username: string, password = DEFAULT_STUDENT_PASSWORD) => {
    modal.info({
      title: "学生账号已创建",
      width: 520,
      okText: "知道了",
      content: (
        <Space direction="vertical" size={12} className="fullWidth">
          <Alert type="info" showIcon message="学生初始密码由机构统一设置，不强制首次修改。" />
          <div><Text type="secondary">用户名</Text><Paragraph copyable strong>{username}</Paragraph></div>
          <div><Text type="secondary">初始密码</Text><Paragraph copyable code>{password}</Paragraph></div>
        </Space>
      ),
    });
  };

  const createStudent = async (values: { name: string; username: string; classroom_id?: number; age_level: SchoolStage }) => {
    setSaving(true);
    try {
      const response = await api.post("/api/students", values);
      form.resetFields();
      await onRefresh();
      showStudentCredential(response.data.student.username, response.data.initial_password);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const importStudents = async (file?: File) => {
    if (!file) return;
    setImporting(true);
    try {
      const data = new FormData();
      data.append("file", file);
      data.append("conflict_strategy", conflictStrategy);
      const response = await api.post("/api/students/import", data);
      await onRefresh();
      const result = response.data as { imported: number; updated: number; skipped: number; errors: Array<{ row: number; message: string }> };
      const summary = `新增 ${result.imported}，更新 ${result.updated}，跳过 ${result.skipped}`;
      result.errors.length
        ? message.warning(`${summary}；${result.errors.length} 行失败，首条：第 ${result.errors[0].row} 行 ${result.errors[0].message}`)
        : message.success(summary);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setImporting(false);
      if (importInputRef.current) importInputRef.current.value = "";
    }
  };

  const downloadTemplate = () => {
    const content = "\ufeff姓名,用户名,班级,学龄分类,账号状态\n示例学生,student.example,示例班级,小学低龄,active\n";
    const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "学生账号导入模板.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      <Alert
        type="info"
        showIcon
        message="学生账号由管理员统一开户"
        description={`支持单独开户和 CSV 批量开户；初始密码固定为 ${DEFAULT_STUDENT_PASSWORD}。开户后的资料与状态请到“教学管理 → 学员管理”维护。`}
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card title={<IconTitle icon={<Plus size={18} />} text="单独创建学生" />}>
            <Form name="student-account-create" form={form} layout="vertical" onFinish={createStudent} initialValues={{ age_level: "primary_lower" }}>
              <Form.Item name="name" label="学生姓名" rules={[{ required: true, message: "请输入学生姓名" }]}><Input /></Form.Item>
              <Form.Item
                name="username"
                label="唯一用户名"
                normalize={(value) => String(value || "").toLowerCase()}
                rules={[
                  { required: true, message: "请输入用户名" },
                  { pattern: /^[a-z0-9][a-z0-9._-]{3,39}$/, message: "请输入 4-40 位有效用户名" },
                ]}
              ><Input autoComplete="off" /></Form.Item>
              <Form.Item name="classroom_id" label="初始班级"><Select allowClear placeholder="暂不分班" options={classroomOptions} /></Form.Item>
              <Form.Item name="age_level" label="学龄分类"><Select options={SCHOOL_STAGE_OPTIONS} /></Form.Item>
              <Button type="primary" htmlType="submit" loading={saving} icon={<Plus size={16} />} block>创建学生账号</Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card title={<IconTitle icon={<FileUp size={18} />} text="CSV 批量开户" />}>
            <Space direction="vertical" size={14} className="fullWidth">
              <Text>必填列：姓名、用户名；可选列：班级、学龄分类、账号状态。按用户名处理重复记录。</Text>
              <Space wrap>
                <Select value={conflictStrategy} onChange={setConflictStrategy} options={[
                  { value: "skip", label: "重复用户名：跳过" },
                  { value: "update", label: "重复用户名：更新资料" },
                ]} />
                <input ref={importInputRef} type="file" accept=".csv,text/csv" hidden onChange={(event) => void importStudents(event.target.files?.[0])} />
                <Button type="primary" icon={<FileUp size={15} />} loading={importing} onClick={() => importInputRef.current?.click()}>选择 CSV 批量开户</Button>
                <Button icon={<FileDown size={15} />} onClick={downloadTemplate}>下载模板</Button>
              </Space>
              <Alert type="warning" showIcon message="更新模式不会覆盖现有学生密码。" />
            </Space>
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
