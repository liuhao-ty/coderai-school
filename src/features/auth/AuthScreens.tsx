import { Alert, App as AntApp, Button, Card, Col, Form, Input, Row, Space, Typography } from "antd";
import { GraduationCap, Lock, ShieldCheck, UserRound } from "lucide-react";
import { useState } from "react";

import { api, loadTeacherProfile } from "../../lib/api";
import { explainError } from "../../lib/errors";
import type { StudentAuth, TeacherAuth } from "../../types";


const { Title, Text } = Typography;

export function LaunchScreen({
  onStudent,
  onTeacher,
  onPrivacy
}: {
  onStudent: () => void;
  onTeacher: () => void;
  onPrivacy: () => void;
}) {
  return (
    <div className="launchScreen">
      <div className="launchPanel">
        <div className="launchBrand">
          <div className="brandMark large">AI</div>
          <div>
            <Title level={1}>CoderAI 学堂</Title>
            <Text>面向少儿编程课堂的 AI 创作与教学桌面工具</Text>
          </div>
        </div>
        <Row gutter={[20, 20]} className="identityGrid">
          <Col xs={24} md={12}>
            <button className="identityCard studentIdentity" onClick={onStudent}>
              <GraduationCap size={40} />
              <span className="identityTitle">学生端</span>
              <span className="identityText">进入学习工作台、课堂任务、AI创作和我的作品。</span>
            </button>
          </Col>
          <Col xs={24} md={12}>
            <button className="identityCard teacherIdentity" onClick={onTeacher}>
              <Lock size={40} />
              <span className="identityTitle">教师端 / 管理员端</span>
              <span className="identityText">教师管理本人教学数据；管理员维护账号、模型和系统设置。</span>
            </button>
          </Col>
        </Row>
        <Button type="link" icon={<ShieldCheck size={16} />} onClick={onPrivacy}>
          隐私与未成年人数据保护政策
        </Button>
      </div>
    </div>
  );
}

export function StudentLoginScreen({
  onBack,
  onSuccess
}: {
  onBack: () => void;
  onSuccess: (auth: StudentAuth) => void;
}) {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  const login = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api.post("/api/auth/student-login", values);
      onSuccess(res.data as StudentAuth);
      message.success(`欢迎，${res.data.student.name}`);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="launchScreen">
      <Card className="loginCard">
        <Space direction="vertical" size={18} className="fullWidth">
          <div>
            <Title level={2}>学生登录</Title>
            <Text>使用管理员分配的用户名和密码进入学习空间。</Text>
          </div>
          <Alert type="info" showIcon message="学生账号由机构管理员统一开通，不提供自行注册入口。" />
          <Form layout="vertical" onFinish={login}>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
              <Input prefix={<UserRound size={16} />} placeholder="请输入用户名" autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password prefix={<Lock size={16} />} placeholder="请输入密码" autoComplete="current-password" />
            </Form.Item>
            <Space wrap>
              <Button type="primary" htmlType="submit" loading={loading} aria-label="登录">登录</Button>
              <Button onClick={onBack} aria-label="返回">返回</Button>
            </Space>
          </Form>
        </Space>
      </Card>
    </div>
  );
}

export function StudentPasswordChangeScreen({
  onBack,
  onSuccess
}: {
  onBack: () => void;
  onSuccess: (auth: StudentAuth) => void;
}) {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  const changePassword = async (values: { current_password: string; next_password: string; confirm_password: string }) => {
    setLoading(true);
    try {
      const res = await api.post("/api/auth/change-student-password", {
        current_password: values.current_password,
        next_password: values.next_password
      });
      message.success("密码已更新");
      onSuccess(res.data as StudentAuth);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="launchScreen">
      <Card className="loginCard">
        <Space direction="vertical" size={18} className="fullWidth">
          <div>
            <Title level={2}>设置新密码</Title>
            <Text>管理员重置密码后，需要先设置只有你知道的新密码。</Text>
          </div>
          <Alert type="warning" showIcon message="完成修改后，其他旧设备上的学生会话会失效。" />
          <Form layout="vertical" onFinish={changePassword}>
            <Form.Item name="current_password" label="临时密码" rules={[{ required: true, message: "请输入临时密码" }]}>
              <Input.Password prefix={<Lock size={16} />} autoComplete="current-password" />
            </Form.Item>
            <Form.Item
              name="next_password"
              label="新密码"
              rules={[
                { required: true, message: "请输入新密码" },
                { min: 8, message: "密码至少 8 位" },
                { pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/, message: "必须同时包含字母和数字" }
              ]}
            >
              <Input.Password prefix={<Lock size={16} />} autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              name="confirm_password"
              label="确认新密码"
              dependencies={["next_password"]}
              rules={[
                { required: true, message: "请再次输入新密码" },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    return !value || getFieldValue("next_password") === value
                      ? Promise.resolve()
                      : Promise.reject(new Error("两次输入的新密码不一致"));
                  }
                })
              ]}
            >
              <Input.Password prefix={<Lock size={16} />} autoComplete="new-password" />
            </Form.Item>
            <Space wrap>
              <Button type="primary" htmlType="submit" loading={loading}>保存新密码</Button>
              <Button onClick={onBack}>返回身份选择</Button>
            </Space>
          </Form>
        </Space>
      </Card>
    </div>
  );
}

export function TeacherLoginScreen({ onBack, onSuccess }: { onBack: () => void; onSuccess: (auth: TeacherAuth) => void }) {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);

  const login = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const res = await api.post("/api/auth/teacher-login", {
        ...values,
        device_name: navigator.platform || navigator.userAgent.slice(0, 80) || "Windows 设备"
      });
      const auth = res.data as TeacherAuth;
      onSuccess(auth);
      message.success(auth.user.role === "admin" ? "管理员端已解锁" : "教师端已解锁");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="launchScreen">
      <Card className="loginCard">
        <Space direction="vertical" size={18} className="fullWidth">
          <div>
            <Title level={2}>教师 / 管理员登录</Title>
            <Text>系统会根据账号角色进入独立的教学工作台或管理控制台。</Text>
          </div>
          <Alert type="info" showIcon message="管理员负责全机构教学与系统配置；教师管理获授权班级的教学数据。" />
          <Form layout="vertical" onFinish={login}>
            <Form.Item name="username" label="职员用户名" rules={[{ required: true, message: "请输入职员用户名" }]}>
              <Input prefix={<UserRound size={16} />} autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="职员密码" rules={[{ required: true, message: "请输入职员密码" }]}>
              <Input.Password prefix={<Lock size={16} />} placeholder="请输入职员密码" />
            </Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={loading}>进入对应工作台</Button>
              <Button onClick={onBack}>返回</Button>
            </Space>
          </Form>
        </Space>
      </Card>
    </div>
  );
}

export function TeacherPasswordChangeScreen({ onBack, onSuccess }: { onBack: () => void; onSuccess: (auth: TeacherAuth) => void }) {
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);
  const isAdmin = loadTeacherProfile()?.role === "admin";

  const changePassword = async (values: { current_password: string; next_password: string; confirm_password: string }) => {
    setLoading(true);
    try {
      const res = await api.post("/api/auth/change-teacher-password", {
        current_password: values.current_password,
        next_password: values.next_password
      });
      message.success("账号密码已更新，请使用新密码登录。");
      onSuccess(res.data as TeacherAuth);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="launchScreen">
      <Card className="loginCard">
        <Space direction="vertical" size={18} className="fullWidth">
          <div>
            <Title level={2}>修改登录密码</Title>
            <Text>{isAdmin ? "管理员账号继续使用系统强密码规则。" : "设置至少 8 位的新密码。"}</Text>
          </div>
          {isAdmin && <Alert type="warning" showIcon message="新密码至少 10 位，并同时包含字母、数字和特殊字符。" />}
          <Form layout="vertical" onFinish={changePassword}>
            <Form.Item name="current_password" label="当前或临时密码" rules={[{ required: true, message: "请输入当前或临时密码" }]}>
              <Input.Password prefix={<Lock size={16} />} />
            </Form.Item>
            <Form.Item
              name="next_password"
              label="新密码"
              rules={[
                { required: true, message: "请输入新密码" },
                { min: isAdmin ? 10 : 8, message: `新密码至少 ${isAdmin ? 10 : 8} 位` },
                ...(isAdmin ? [{ pattern: /^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).+$/, message: "必须包含字母、数字和特殊字符" }] : [])
              ]}
            >
              <Input.Password prefix={<Lock size={16} />} autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              name="confirm_password"
              label="确认新密码"
              dependencies={["next_password"]}
              rules={[
                { required: true, message: "请再次输入新密码" },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    return !value || getFieldValue("next_password") === value
                      ? Promise.resolve()
                      : Promise.reject(new Error("两次输入的新密码不一致"));
                  }
                })
              ]}
            >
              <Input.Password prefix={<Lock size={16} />} autoComplete="new-password" />
            </Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={loading}>修改密码并进入对应工作台</Button>
              <Button onClick={onBack}>返回身份选择</Button>
            </Space>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
