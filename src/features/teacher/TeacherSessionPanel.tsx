import { App as AntApp, Button, Card, List, Popconfirm, Space, Tag, Typography } from "antd";
import { RotateCcw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import type { TeacherSessionState } from "../../types";


const { Text } = Typography;

export function TeacherSessionPanel() {
  const { message } = AntApp.useApp();
  const [sessions, setSessions] = useState<TeacherSessionState[]>([]);
  const [loading, setLoading] = useState(false);
  const [revokingId, setRevokingId] = useState<number | null>(null);

  const loadSessions = async () => {
    setLoading(true);
    try {
      const res = await api.get("/api/auth/teacher-sessions");
      setSessions(res.data.sessions || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSessions();
  }, []);

  const revokeSession = async (sessionId: number) => {
    setRevokingId(sessionId);
    try {
      await api.delete(`/api/auth/teacher-sessions/${sessionId}`);
      await loadSessions();
      message.success("该设备的教师会话已撤销");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setRevokingId(null);
    }
  };

  return (
    <Card
      title={<IconTitle icon={<ShieldCheck size={18} />} text="登录设备" />}
      extra={<Button size="small" icon={<RotateCcw size={14} />} loading={loading} onClick={() => void loadSessions()}>刷新</Button>}
    >
      <List
        size="small"
        loading={loading && sessions.length === 0}
        dataSource={sessions}
        locale={{ emptyText: "暂无有效教师会话" }}
        renderItem={(session) => (
          <List.Item
            actions={[
              session.current ? (
                <Tag key="current" color="green">当前设备</Tag>
              ) : (
                <Popconfirm
                  key="revoke"
                  title="撤销这台设备的登录？"
                  description="该设备下次操作时需要重新输入教师密码。"
                  onConfirm={() => void revokeSession(session.session_id)}
                >
                  <Button danger size="small" loading={revokingId === session.session_id}>撤销</Button>
                </Popconfirm>
              )
            ]}
          >
            <List.Item.Meta
              title={<Space><Text strong>{session.device_name}</Text>{session.current && <Tag color="blue">本机</Tag>}</Space>}
              description={
                <Space direction="vertical" size={2}>
                  <Text type="secondary">登录：{formatBeijingTime(session.created_at)}</Text>
                  <Text type="secondary">会话到期：{formatBeijingTime(session.expires_at)}</Text>
                </Space>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}
