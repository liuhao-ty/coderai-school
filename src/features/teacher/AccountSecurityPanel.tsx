import { App as AntApp, Button, Card, Form, Input, Space, Tabs } from "antd";
import { History, Laptop, LockKeyhole } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api, loadTeacherProfile, storeTeacherAuth } from "../../lib/api";
import { explainError } from "../../lib/errors";
import type { TeacherAuth } from "../../types";
import { TeacherAuditPanel } from "./TeacherAuditPanel";
import { TeacherSessionPanel } from "./TeacherSessionPanel";


export function PasswordChangePanel() {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const isAdmin = loadTeacherProfile()?.role === "admin";

  const updatePassword = async (values: { current_password: string; next_password: string }) => {
    setSaving(true);
    try {
      const response = await api.post("/api/auth/change-teacher-password", values);
      storeTeacherAuth(response.data as TeacherAuth);
      form.resetFields();
      message.success("登录密码已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="passwordChangeCard" title={<IconTitle icon={<LockKeyhole size={18} />} text="修改登录密码" />}>
      <Form name="current-account-password-change" form={form} layout="vertical" onFinish={updatePassword}>
        <Form.Item name="current_password" label="当前密码" rules={[{ required: true, message: "请输入当前密码" }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item
          name="next_password"
          label="新密码"
          extra={isAdmin ? "至少 10 位，并同时包含字母、数字和特殊字符" : undefined}
          rules={[
            { required: true, message: "请输入新密码" },
            { min: isAdmin ? 10 : 8, message: `新密码至少 ${isAdmin ? 10 : 8} 位` },
            ...(isAdmin ? [{ pattern: /^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).+$/, message: "必须包含字母、数字和特殊字符" }] : []),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={saving} block>
          更新密码
        </Button>
      </Form>
    </Card>
  );
}

export function SecurityActivityTabs() {
  const handleTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const target = event.target as HTMLElement;
    if (target.getAttribute("role") !== "tab") return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'));
    const currentIndex = tabs.indexOf(target);
    if (currentIndex < 0 || tabs.length === 0) return;
    const nextIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? tabs.length - 1
        : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[nextIndex].click();
    tabs[nextIndex].focus();
  };

  return (
    <Tabs
      className="securityActivityTabs"
      defaultActiveKey="sessions"
      onKeyDown={handleTabKeyDown}
      items={[
        {
          key: "sessions",
          label: <Space size={6}><Laptop size={16} />登录设备</Space>,
          children: <TeacherSessionPanel />,
        },
        {
          key: "audit",
          label: <Space size={6}><History size={16} />操作审计</Space>,
          children: <TeacherAuditPanel />,
        },
      ]}
    />
  );
}

export function AccountSecurityPanel({ showPassword = true }: { showPassword?: boolean }) {
  return (
    <Space direction="vertical" size={16} className="fullWidth">
      {showPassword && <PasswordChangePanel />}
      <SecurityActivityTabs />
    </Space>
  );
}
