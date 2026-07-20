import { App as AntApp, Button, Card, List, Select, Space, Tag, Typography } from "antd";
import { History, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type { TeacherAuditLogItem } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Text } = Typography;

const actionLabels: Record<string, string> = {
  "provider.settings.updated": "AI 服务配置",
  "classroom.deleted": "删除班级",
  "backup.package_restored": "恢复完整备份",
  "backup.legacy_restored": "恢复兼容备份",
  "submission.reviewed": "作业批改",
  "moderation.settings.updated": "审核规则",
  "moderation.image.reviewed": "图片复核",
  "teacher.password.changed": "修改密码",
  "teacher_account.created": "创建教师账号",
  "teacher_account.updated": "更新教师账号",
  "teacher_account.password_reset": "重置教师密码",
  "student_account.created": "创建学生账号",
  "student_account.password_reset": "重置学生密码",
  "curriculum.package.teachers_updated": "课程包教师权限"
};

export function TeacherAuditPanel() {
  const { message } = AntApp.useApp();
  const [logs, setLogs] = useState<TeacherAuditLogItem[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(false);

  const loadLogs = async (nextAction = action) => {
    setLoading(true);
    try {
      const res = await api.get("/api/audit-logs", { params: nextAction ? { action: nextAction } : undefined });
      setLogs(res.data.logs || []);
      setCurrentSessionId(res.data.current_session_id || null);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadLogs("");
  }, []);

  return (
    <Card
      title={<IconTitle icon={<History size={18} />} text="操作审计" />}
      extra={<Button size="small" icon={<RotateCcw size={14} />} loading={loading} onClick={() => void loadLogs()}>刷新</Button>}
    >
      <Select
        className="fullWidth mb16"
        value={action}
        onChange={(value) => {
          setAction(value);
          void loadLogs(value);
        }}
        options={[
          { value: "", label: "全部高风险操作" },
          ...Object.entries(actionLabels).map(([value, label]) => ({ value, label }))
        ]}
      />
      <List
        size="small"
        loading={loading && logs.length === 0}
        dataSource={logs}
        locale={{ emptyText: <EmptyState title="暂无审计记录" description="高风险教师操作会自动记录在这里" /> }}
        renderItem={(log) => (
          <List.Item>
            <List.Item.Meta
              title={
                <Space wrap>
                  <Text strong>{actionLabels[log.action] || log.action}</Text>
                  {log.session_id === currentSessionId && <Tag color="blue">当前设备</Tag>}
                </Space>
              }
              description={
                <Space direction="vertical" size={2}>
                  <Text>{log.summary}</Text>
                  <Text type="secondary">
                    操作者：{log.actor_name || "系统"}{log.actor_username ? `（${log.actor_username}）` : ""} · {" "}
                    {log.target_type ? `${log.target_type}${log.target_id ? ` #${log.target_id}` : ""} · ` : ""}
                    {formatBeijingTime(log.created_at)}
                  </Text>
                </Space>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}
