import { Alert, App as AntApp, Button, Card, Skeleton, Space, Tag, Typography } from "antd";
import { ArrowLeft, FileDown, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { PrivacyPolicy, StudentPrivacyState } from "../../domain-types";
import { api, getAuthValue, STUDENT_TOKEN_KEY } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";


const { Title, Text } = Typography;

export function PrivacyPolicyPage({ onBack }: { onBack: () => void }) {
  const { message } = AntApp.useApp();
  const [policy, setPolicy] = useState<PrivacyPolicy | null>(null);
  const [studentPrivacy, setStudentPrivacy] = useState<StudentPrivacyState | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const policyResponse = await api.get("/api/privacy/policy");
        setPolicy(policyResponse.data.policy as PrivacyPolicy);
        if (getAuthValue(STUDENT_TOKEN_KEY)) {
          try {
            const statusResponse = await api.get("/api/privacy/me");
            setStudentPrivacy(statusResponse.data as StudentPrivacyState);
          } catch {
            setStudentPrivacy(null);
          }
        }
      } catch (error) {
        message.error(explainError(error));
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [message]);

  const exportMyData = async () => {
    setExporting(true);
    try {
      const response = await api.get("/api/privacy/me/export", { responseType: "blob" });
      if (await saveBlobFile(response.data, `coderai-my-data-${new Date().toISOString().slice(0, 10)}.zip`)) {
        message.success("个人数据已导出");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="privacyPolicyPage">
      <main className="privacyPolicySurface">
        <div className="privacyPolicyHeader">
          <Space direction="vertical" size={4}>
            <Space align="center">
              <ShieldCheck size={26} />
              <Title level={2}>隐私与未成年人数据保护</Title>
            </Space>
            <Text type="secondary">当前生效政策及学生个人数据权利</Text>
          </Space>
          <Space wrap>
            {studentPrivacy && (
              <Button icon={<FileDown size={16} />} loading={exporting} onClick={() => void exportMyData()}>
                导出我的数据
              </Button>
            )}
            <Button icon={<ArrowLeft size={16} />} onClick={onBack}>返回</Button>
          </Space>
        </div>

        {studentPrivacy && (
          <Alert
            className="mb16"
            type={studentPrivacy.ai_access_allowed ? "success" : "warning"}
            showIcon
            message={studentPrivacy.ai_access_allowed ? "当前可以使用 AI 生成功能" : "当前尚不能使用 AI 生成功能"}
            description={
              studentPrivacy.consent_required
                ? studentPrivacy.active_consent
                  ? `监护人授权已按政策 ${studentPrivacy.policy.version} 记录。`
                  : "当前政策要求监护人授权，请联系教师完成授权记录。"
                : "当前政策未强制要求监护人授权，机构仍应按当地适用规则完成告知。"
            }
          />
        )}

        <Card className="privacyDocument">
          {loading || !policy ? (
            <Skeleton active paragraph={{ rows: 12 }} />
          ) : (
            <>
              <Space wrap className="mb16">
                <Tag color="blue">版本 {policy.version}</Tag>
                <Tag color={policy.require_guardian_consent ? "green" : "orange"}>
                  {policy.require_guardian_consent ? "AI 使用需监护人授权" : "监护人授权未强制"}
                </Tag>
                <Tag>数据保留 {policy.retention_days} 天</Tag>
                <Tag color={policy.allow_external_ai_processing ? "purple" : "red"}>
                  {policy.allow_external_ai_processing ? "允许受控云端 AI 处理" : "禁止云端 AI 处理"}
                </Tag>
              </Space>
              <div className="markdownPreview privacyMarkdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{policy.content_markdown}</ReactMarkdown>
              </div>
            </>
          )}
        </Card>
      </main>
    </div>
  );
}
