import { Card, List, Space, Tag, Typography } from "antd";
import { Bot, CheckCircle2, DatabaseBackup, KeyRound, ServerCog, ShieldCheck, UsersRound } from "lucide-react";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type { AdminSectionKey, Classroom, ProviderState } from "../../domain-types";
import { loadTeacherProfile } from "../../lib/api";
import { AccountManager } from "./AccountManager";
import { AccountSecurityPanel } from "./AccountSecurityPanel";
import { ExtensionLicensePanel } from "./ExtensionLicensePanel";
import { MultiModelManager } from "./MultiModelManager";
import { OperationsPanel } from "./OperationsPanel";
import { PrivacyDataPanel } from "./PrivacyDataPanel";
import { SystemBackupPanel } from "./SystemBackupPanel";


const { Title, Text, Paragraph } = Typography;

const sectionCopy: Partial<Record<AdminSectionKey, { title: string; description: string }>> = {
  overview: { title: "系统总览", description: "关注账号体系、模型服务和系统运行状态，不混入教师的课堂数据。" },
  accounts: { title: "账号与身份管理", description: "统一管理职员账号、学生开户和当前管理员登录密码。" },
  models: { title: "模型服务", description: "集中维护多厂商模型、密钥、能力路由和调用统计。" },
  privacy: { title: "隐私与数据政策", description: "维护全局隐私政策、监护人授权要求和数据处理规则。" },
  security: { title: "管理员账号安全", description: "查看当前管理员的登录设备和高风险操作记录。" },
  operations: { title: "运维与备份", description: "检查机构存储和文件一致性，并导出本机构数据；云端恢复由平台运维执行。" },
  extensions: { title: "扩展与授权", description: "维护许可证、插件和系统扩展能力。" },
};

export function AdminPanel({
  section,
  provider,
  usage,
  classrooms,
  onRefresh,
}: {
  section: AdminSectionKey;
  provider: ProviderState;
  usage: { feature: string; status: string; count: number }[];
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const profile = loadTeacherProfile();
  const copy = sectionCopy[section] || sectionCopy.overview!;
  const totalUsage = usage.reduce((sum, item) => sum + item.count, 0);

  return (
    <div className="page">
      <div className="teacherHero adminHero">
        <div>
          <Title level={2}>{copy.title}</Title>
          <Text>{copy.description}</Text>
        </div>
        <Space wrap>
          <Tag color="purple">系统管理员</Tag>
          {section === "models" && (
            <Tag color={provider.configured ? "green" : "orange"}>{provider.configured ? "模型服务可用" : "模型服务待配置"}</Tag>
          )}
        </Space>
      </div>

      {section === "overview" && (
        <Space direction="vertical" size={16} className="fullWidth">
          <section className="teachingMetricStrip systemMetricStrip" aria-label="系统统计">
            <div className="teachingMetricItem metricBlue">
              <span className="teachingMetricIcon"><ShieldCheck size={22} /></span>
              <span><Text type="secondary">当前角色</Text><strong>管理员</strong></span>
            </div>
            <div className="teachingMetricItem metricGreen">
              <span className="teachingMetricIcon"><CheckCircle2 size={22} /></span>
              <span><Text type="secondary">模型服务</Text><strong>{provider.configured ? "已配置" : "待配置"}</strong></span>
            </div>
            <div className="teachingMetricItem metricAmber">
              <span className="teachingMetricIcon"><ServerCog size={22} /></span>
              <span><Text type="secondary">服务商数量</Text><strong>{provider.provider_count || (provider.configured ? 1 : 0)}</strong></span>
            </div>
            <div className="teachingMetricItem metricCoral">
              <span className="teachingMetricIcon"><Bot size={22} /></span>
              <span><Text type="secondary">累计调用记录</Text><strong>{totalUsage}</strong></span>
            </div>
          </section>
          <div className="systemOverviewGrid">
            <Card className="ledgerPanel" title={<IconTitle icon={<KeyRound size={18} />} text="当前管理员" />}>
              <Space direction="vertical" size={8}>
                <Text strong>{profile?.name || "管理员"}</Text>
                <Text code>{profile?.username || "admin"}</Text>
                <Text type="secondary">系统总览仅展示账号、模型与运维状态；教学数据在教学总览中查看。</Text>
              </Space>
            </Card>
            <Card className="ledgerPanel" title={<IconTitle icon={<ShieldCheck size={18} />} text="管理员职责" />}>
              <List
                size="small"
                dataSource={[
                  { icon: <UsersRound size={16} />, text: "职员账号、学生开户与角色权限" },
                  { icon: <Bot size={16} />, text: "全局模型服务与能力路由" },
                  { icon: <DatabaseBackup size={16} />, text: "系统运维、备份与授权" },
                ]}
                renderItem={(item) => <List.Item><Space>{item.icon}<Text>{item.text}</Text></Space></List.Item>}
              />
            </Card>
          </div>
        </Space>
      )}

      {section === "accounts" && <AccountManager classrooms={classrooms} onRefresh={onRefresh} />}
      {section === "models" && (
        <Space direction="vertical" size={16} className="fullWidth">
          <MultiModelManager onRefresh={onRefresh} />
          <Card title="全局调用统计">
            <List
              size="small"
              dataSource={usage}
              locale={{ emptyText: <EmptyState title="暂无调用记录" description="AI 工具产生调用后会显示统计" /> }}
              renderItem={(item) => (
                <List.Item>
                  <Text>{item.feature}</Text>
                  <Tag color={item.status === "success" ? "green" : "red"}>{item.status}</Tag>
                  <Text>{item.count}</Text>
                </List.Item>
              )}
            />
          </Card>
        </Space>
      )}
      {section === "privacy" && <PrivacyDataPanel onRefresh={onRefresh} />}
      {section === "security" && <AccountSecurityPanel showPassword={false} />}
      {section === "operations" && (
        <Space direction="vertical" size={16} className="fullWidth">
          <OperationsPanel />
          <SystemBackupPanel onRefresh={onRefresh} />
        </Space>
      )}
      {section === "extensions" && <ExtensionLicensePanel />}
      {!copy && <Paragraph>页面不存在。</Paragraph>}
    </div>
  );
}
