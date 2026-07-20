import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { KeyRound, UserCog, UserRoundPlus } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { Classroom } from "../../domain-types";
import { StudentRegistrationPanel } from "../classes/StudentRegistrationPanel";
import { api, loadTeacherProfile } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import type { AccountProfile } from "../../types";
import { PasswordChangePanel } from "./AccountSecurityPanel";


const { Text, Paragraph } = Typography;

export function AccountManager({
  classrooms,
  onRefresh,
}: {
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const { message, modal } = AntApp.useApp();
  const [teacherForm] = Form.useForm();
  const [teachers, setTeachers] = useState<AccountProfile[]>([]);
  const [loadingTeachers, setLoadingTeachers] = useState(false);
  const [savingTeacher, setSavingTeacher] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const currentTeacher = loadTeacherProfile();

  const loadTeachers = async () => {
    setLoadingTeachers(true);
    try {
      const response = await api.get("/api/accounts/teachers");
      setTeachers(response.data.accounts as AccountProfile[]);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoadingTeachers(false);
    }
  };

  useEffect(() => {
    void loadTeachers();
  }, []);

  const showTemporaryCredential = (title: string, username: string, password: string) => {
    modal.info({
      title,
      width: 520,
      okText: "我已妥善记录",
      content: (
        <Space direction="vertical" size={12} className="fullWidth">
          <Alert type="warning" showIcon message="职员临时密码只显示这一次，首次登录后必须修改。" />
          <div><Text type="secondary">用户名</Text><Paragraph copyable strong>{username}</Paragraph></div>
          <div><Text type="secondary">临时密码</Text><Paragraph copyable code>{password}</Paragraph></div>
        </Space>
      ),
    });
  };

  const createTeacher = async (values: { name: string; username: string; role: "teacher" | "admin" }) => {
    setSavingTeacher(true);
    try {
      const response = await api.post("/api/accounts/teachers", values);
      teacherForm.resetFields();
      await loadTeachers();
      showTemporaryCredential("职员账号已创建", response.data.account.username, response.data.temporary_password);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingTeacher(false);
    }
  };

  const updateTeacher = async (account: AccountProfile, patch: Partial<Pick<AccountProfile, "role" | "active">>) => {
    setUpdatingId(account.id);
    try {
      await api.put(`/api/accounts/teachers/${account.id}`, {
        name: account.name,
        role: patch.role ?? account.role,
        active: patch.active ?? account.active,
      });
      await Promise.all([loadTeachers(), onRefresh()]);
      message.success("职员账号已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUpdatingId(null);
    }
  };

  const resetTeacherPassword = async (account: AccountProfile) => {
    setUpdatingId(account.id);
    try {
      const response = await api.post(`/api/accounts/teachers/${account.id}/reset-password`);
      await loadTeachers();
      showTemporaryCredential("职员密码已重置", account.username, response.data.temporary_password);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUpdatingId(null);
    }
  };

  const teacherAccounts = (
    <Space direction="vertical" size={16} className="fullWidth">
      <Alert type="info" showIcon message="教师与管理员账号" description="只有管理员可以创建、停用职员账号或调整系统角色。停用教师会立即移除其班级授权。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card title={<IconTitle icon={<UserRoundPlus size={18} />} text="创建职员账号" />}>
            <Form name="staff-account-create" form={teacherForm} layout="vertical" onFinish={createTeacher} initialValues={{ role: "teacher" }}>
              <Form.Item name="name" label="姓名" rules={[{ required: true, message: "请输入姓名" }]}><Input placeholder="例如：王老师" /></Form.Item>
              <Form.Item
                name="username"
                label="登录用户名"
                normalize={(value) => String(value || "").toLowerCase()}
                rules={[
                  { required: true, message: "请输入用户名" },
                  { pattern: /^[a-z0-9][a-z0-9._-]{3,39}$/, message: "请输入 4-40 位有效用户名" },
                ]}
              ><Input autoComplete="off" /></Form.Item>
              <Form.Item name="role" label="权限角色"><Select options={[{ value: "teacher", label: "教师" }, { value: "admin", label: "管理员" }]} /></Form.Item>
              <Button type="primary" htmlType="submit" loading={savingTeacher} icon={<UserRoundPlus size={16} />} block>创建并生成临时密码</Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card title={<IconTitle icon={<UserCog size={18} />} text="职员账号" />}>
            <List
              loading={loadingTeachers}
              dataSource={teachers}
              locale={{ emptyText: "暂无职员账号" }}
              renderItem={(account) => {
                const isCurrent = account.id === currentTeacher?.id;
                return (
                  <List.Item
                    actions={[
                      <Select
                        key="role"
                        size="small"
                        aria-label={`设置 ${account.name} 的角色`}
                        value={account.role}
                        disabled={isCurrent || updatingId === account.id}
                        onChange={(role) => void updateTeacher(account, { role: role as "teacher" | "admin" })}
                        options={[{ value: "teacher", label: "教师" }, { value: "admin", label: "管理员" }]}
                      />,
                      <Popconfirm key="password" title="重置这个职员账号的密码？" description="所有旧会话会失效，并生成只显示一次的临时密码。" onConfirm={() => resetTeacherPassword(account)}>
                        <Button size="small" disabled={isCurrent} loading={updatingId === account.id} icon={<KeyRound size={14} />}>重置密码</Button>
                      </Popconfirm>,
                      <Popconfirm key="active" title={account.active ? "停用这个职员账号？" : "重新启用这个职员账号？"} description={account.active ? "账号会立即退出；教师的班级授权同时清除。" : "启用后可重新登录，但班级需重新授权。"} onConfirm={() => updateTeacher(account, { active: !account.active })}>
                        <Button size="small" danger={account.active} disabled={isCurrent} loading={updatingId === account.id}>{account.active ? "停用" : "启用"}</Button>
                      </Popconfirm>,
                    ]}
                  >
                    <List.Item.Meta
                      title={<Space wrap><Text strong>{account.name}</Text><Text code>{account.username}</Text>{isCurrent && <Tag color="blue">当前账号</Tag>}</Space>}
                      description={(
                        <Space wrap>
                          <Tag color={account.role === "admin" ? "purple" : "blue"}>{account.role === "admin" ? "管理员" : "教师"}</Tag>
                          <Tag color={account.active ? "green" : "red"}>{account.active ? "启用" : "停用"}</Tag>
                          {account.password_change_required && <Tag color="orange">待修改临时密码</Tag>}
                          <Text type="secondary">最近登录：{account.last_login_at ? formatBeijingTime(account.last_login_at) : "从未登录"}</Text>
                        </Space>
                      )}
                    />
                  </List.Item>
                );
              }}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );

  return (
    <Tabs
      defaultActiveKey="teachers"
      items={[
        { key: "teachers", label: "教师与管理员", children: teacherAccounts },
        { key: "students", label: "学生开户", children: <StudentRegistrationPanel classrooms={classrooms} onRefresh={onRefresh} /> },
        { key: "password", label: "修改登录密码", children: <PasswordChangePanel /> },
      ]}
    />
  );
}
