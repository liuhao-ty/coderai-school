import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography
} from "antd";
import { Ban, FileDown, Save, ShieldCheck, Trash2, UserCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type { GuardianConsent, PrivacyPolicy, StudentDeletionPreflight, StudentPrivacyState } from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";
import { formatBeijingTime, formatBytes } from "../../lib/format";
import { RetentionGovernancePanel } from "./RetentionGovernancePanel";


const { Paragraph, Text } = Typography;

type PrivacySettings = {
  current_policy: PrivacyPolicy;
  policies: PrivacyPolicy[];
  student_count: number;
  active_consent_count: number;
};

type PolicyForm = {
  version: string;
  title: string;
  content_markdown: string;
  require_guardian_consent: boolean;
  allow_external_ai_processing: boolean;
  retention_days: number;
};

type ConsentForm = {
  guardian_name: string;
  relationship: string;
  guardian_contact: string;
  consent_method: "written" | "digital" | "in_person" | "phone";
  evidence_reference: string;
  scopes: string[];
};

const scopeOptions = [
  { label: "课程学习", value: "course_learning" },
  { label: "AI 生成", value: "ai_generation" },
  { label: "云端服务商处理", value: "cloud_provider_transfer" },
  { label: "作品存储", value: "portfolio_storage" }
];

const consentMethodLabels: Record<string, string> = {
  written: "纸质授权",
  digital: "电子授权",
  in_person: "当面确认",
  phone: "电话确认"
};

export function PrivacyDataPanel({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { message } = AntApp.useApp();
  const [policyForm] = Form.useForm<PolicyForm>();
  const [consentForm] = Form.useForm<ConsentForm>();
  const [settings, setSettings] = useState<PrivacySettings | null>(null);
  const [studentStates, setStudentStates] = useState<StudentPrivacyState[]>([]);
  const [selectedStudentId, setSelectedStudentId] = useState<number | null>(null);
  const [selectedPrivacy, setSelectedPrivacy] = useState<StudentPrivacyState | null>(null);
  const [loading, setLoading] = useState("");
  const [consentModalOpen, setConsentModalOpen] = useState(false);
  const [revokeConsent, setRevokeConsent] = useState<GuardianConsent | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [deletionPreflight, setDeletionPreflight] = useState<StudentDeletionPreflight | null>(null);
  const [preflightToken, setPreflightToken] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleteReason, setDeleteReason] = useState("");

  const loadOverview = async () => {
    setLoading("overview");
    try {
      const [settingsResponse, studentsResponse] = await Promise.all([
        api.get("/api/privacy/settings"),
        api.get("/api/privacy/students")
      ]);
      const nextSettings = settingsResponse.data as PrivacySettings;
      setSettings(nextSettings);
      setStudentStates(studentsResponse.data.students as StudentPrivacyState[]);
      policyForm.setFieldsValue({
        version: "",
        title: nextSettings.current_policy.title,
        content_markdown: nextSettings.current_policy.content_markdown,
        require_guardian_consent: nextSettings.current_policy.require_guardian_consent,
        allow_external_ai_processing: nextSettings.current_policy.allow_external_ai_processing,
        retention_days: nextSettings.current_policy.retention_days
      });
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const loadStudentPrivacy = async (studentId: number) => {
    setSelectedStudentId(studentId);
    setLoading("student");
    try {
      const response = await api.get(`/api/privacy/students/${studentId}`);
      setSelectedPrivacy(response.data as StudentPrivacyState);
    } catch (error) {
      setSelectedPrivacy(null);
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  const publishPolicy = async (values: PolicyForm) => {
    setLoading("policy");
    try {
      await api.post("/api/privacy/policies", values);
      await loadOverview();
      if (selectedStudentId) await loadStudentPrivacy(selectedStudentId);
      message.success("新隐私政策已发布");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const grantConsent = async (values: ConsentForm) => {
    if (!selectedStudentId) return;
    setLoading("consent");
    try {
      await api.post(`/api/privacy/students/${selectedStudentId}/consents`, values);
      setConsentModalOpen(false);
      consentForm.resetFields();
      await Promise.all([loadOverview(), loadStudentPrivacy(selectedStudentId)]);
      message.success("监护人授权已记录");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const revoke = async () => {
    if (!selectedStudentId || !revokeConsent || !revokeReason.trim()) return;
    setLoading("revoke");
    try {
      await api.post(`/api/privacy/students/${selectedStudentId}/consents/${revokeConsent.id}/revoke`, { reason: revokeReason.trim() });
      setRevokeConsent(null);
      setRevokeReason("");
      await Promise.all([loadOverview(), loadStudentPrivacy(selectedStudentId)]);
      message.success("监护人授权已撤回");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const exportStudent = async () => {
    if (!selectedStudentId) return;
    setLoading("export");
    try {
      const response = await api.get(`/api/privacy/students/${selectedStudentId}/export`, { responseType: "blob" });
      if (await saveBlobFile(response.data, `coderai-student-${selectedStudentId}-data.zip`)) {
        message.success("学生个人数据已导出");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const preflightDelete = async () => {
    if (!selectedStudentId) return;
    setLoading("preflight");
    try {
      const response = await api.get(`/api/privacy/students/${selectedStudentId}/deletion-preflight`);
      setDeletionPreflight(response.data.preflight as StudentDeletionPreflight);
      setPreflightToken(response.data.preflight_token as string);
      setDeleteConfirmation("");
      setDeleteReason("");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const deleteStudent = async () => {
    if (!selectedStudentId || !deletionPreflight || deleteConfirmation !== deletionPreflight.student_name) return;
    setLoading("delete");
    try {
      await api.post(`/api/privacy/students/${selectedStudentId}/delete`, {
        preflight_token: preflightToken,
        confirm_student_name: deleteConfirmation,
        reason: deleteReason
      });
      setDeletionPreflight(null);
      setPreflightToken("");
      setSelectedStudentId(null);
      setSelectedPrivacy(null);
      await Promise.all([loadOverview(), onRefresh()]);
      message.success("学生个人数据已永久删除");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const selectedSummary = useMemo(
    () => studentStates.find((item) => item.student.id === selectedStudentId),
    [selectedStudentId, studentStates]
  );
  const policyUsesLegacyAccessCode = Boolean(
    settings?.current_policy.content_markdown.includes("学生访问码只用于本机身份隔离")
  );

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      {policyUsesLegacyAccessCode && (
        <Alert
          type="warning"
          showIcon
          message="当前已发布政策仍描述旧版访问码登录"
          description="学生账号已改为管理员统一创建的用户名密码和随机登录会话。已发布政策不会被系统自动改写，请由管理员审阅正文并以新版本号发布。"
        />
      )}
      <Alert
        type={settings?.current_policy.require_guardian_consent ? "success" : "warning"}
        showIcon
        message={settings?.current_policy.require_guardian_consent ? "监护人授权规则已强制执行" : "当前政策尚未强制监护人授权"}
        description="发布政策后，后端会按当前生效版本控制学生 AI 访问；关闭云端处理会立即阻止学生将数据发送给 AI 服务商。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card title={<IconTitle icon={<ShieldCheck size={18} />} text="隐私政策版本" />} loading={loading === "overview" && !settings}>
            {settings && (
              <>
                <Descriptions size="small" column={1} className="mb16">
                  <Descriptions.Item label="当前版本">{settings.current_policy.version}</Descriptions.Item>
                  <Descriptions.Item label="生效时间">{formatBeijingTime(settings.current_policy.effective_at)}</Descriptions.Item>
                  <Descriptions.Item label="学生数">{settings.student_count}</Descriptions.Item>
                  <Descriptions.Item label="当前有效授权">{settings.active_consent_count}</Descriptions.Item>
                </Descriptions>
                <Form form={policyForm} layout="vertical" onFinish={publishPolicy}>
                  <Row gutter={12}>
                    <Col xs={24} md={8}>
                      <Form.Item name="version" label="新版本号" rules={[{ required: true, message: "请输入未使用的版本号" }, { pattern: /^[0-9A-Za-z][0-9A-Za-z._-]*$/, message: "仅支持字母、数字、点、下划线和连字符" }]}>
                        <Input placeholder="例如 1.1" />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={16}>
                      <Form.Item name="title" label="政策标题" rules={[{ required: true, message: "请输入政策标题" }]}>
                        <Input />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item name="content_markdown" label="政策正文（Markdown）" rules={[{ required: true, min: 20, message: "政策正文至少 20 个字符" }]}>
                    <Input.TextArea rows={11} />
                  </Form.Item>
                  <Row gutter={12}>
                    <Col xs={24} md={8}>
                      <Form.Item name="require_guardian_consent" label="AI 使用需监护人授权" valuePropName="checked">
                        <Switch />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={8}>
                      <Form.Item name="allow_external_ai_processing" label="允许云端 AI 处理" valuePropName="checked">
                        <Switch />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={8}>
                      <Form.Item name="retention_days" label="数据保留天数" rules={[{ required: true }]}>
                        <InputNumber min={30} max={3650} className="fullWidth" />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={loading === "policy"}>
                    发布新版本
                  </Button>
                </Form>
              </>
            )}
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card title={<IconTitle icon={<UserCheck size={18} />} text="学生授权与数据权利" />}>
            <Space direction="vertical" size={14} className="fullWidth">
              <Select
                className="fullWidth"
                aria-label="选择学生"
                showSearch
                virtual={false}
                optionFilterProp="label"
                value={selectedStudentId}
                placeholder="选择学生"
                onChange={(value) => void loadStudentPrivacy(value)}
                options={studentStates.map((item) => ({
                  value: item.student.id,
                  label: `${item.student.name} · ${item.student.classroom_name || "未分班"} · ${item.ai_access_allowed ? "可用 AI" : "待授权"}`
                }))}
              />
              {!selectedStudentId ? (
                <EmptyState title="请选择学生" description="选择后可管理监护人授权、导出或删除个人数据" />
              ) : (
                <>
                  <Alert
                    type={selectedPrivacy?.ai_access_allowed ? "success" : "warning"}
                    showIcon
                    message={selectedPrivacy?.ai_access_allowed ? "AI 数据处理权限有效" : "AI 数据处理权限不可用"}
                    description={selectedSummary?.consent_required && !selectedPrivacy?.active_consent ? "当前政策要求为该学生补充监护人授权。" : `账号状态：${selectedPrivacy?.student.account_status || "读取中"}`}
                  />
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<UserCheck size={16} />}
                      disabled={Boolean(selectedPrivacy?.active_consent)}
                      onClick={() => {
                        consentForm.setFieldsValue({
                          relationship: "监护人",
                          consent_method: "written",
                          scopes: scopeOptions.map((item) => item.value)
                        });
                        setConsentModalOpen(true);
                      }}
                    >
                      记录授权
                    </Button>
                    <Button icon={<FileDown size={16} />} loading={loading === "export"} onClick={() => void exportStudent()}>
                      导出个人数据
                    </Button>
                    <Button danger icon={<Trash2 size={16} />} loading={loading === "preflight"} onClick={() => void preflightDelete()}>
                      删除个人数据
                    </Button>
                  </Space>
                  {selectedPrivacy?.active_consent && (
                    <Descriptions size="small" column={1} bordered>
                      <Descriptions.Item label="监护人">{selectedPrivacy.active_consent.guardian_name}</Descriptions.Item>
                      <Descriptions.Item label="关系">{selectedPrivacy.active_consent.relationship}</Descriptions.Item>
                      <Descriptions.Item label="联系方式">{selectedPrivacy.active_consent.guardian_contact_masked || "未记录"}</Descriptions.Item>
                      <Descriptions.Item label="授权方式">{consentMethodLabels[selectedPrivacy.active_consent.consent_method]}</Descriptions.Item>
                      <Descriptions.Item label="授权时间">{formatBeijingTime(selectedPrivacy.active_consent.consented_at)}</Descriptions.Item>
                      <Descriptions.Item label="操作">
                        <Button danger size="small" icon={<Ban size={14} />} onClick={() => setRevokeConsent(selectedPrivacy.active_consent || null)}>
                          撤回授权
                        </Button>
                      </Descriptions.Item>
                    </Descriptions>
                  )}
                </>
              )}
            </Space>
          </Card>
        </Col>
      </Row>

      <Card>
        <RetentionGovernancePanel
          policyRetentionDays={settings?.current_policy.retention_days || 365}
          onExecuted={async () => {
            await Promise.all([loadOverview(), onRefresh()]);
          }}
        />
      </Card>

      <Card title="授权历史">
        <Table<GuardianConsent>
          size="small"
          rowKey="id"
          pagination={{ pageSize: 8 }}
          dataSource={selectedPrivacy?.consent_history || []}
          locale={{ emptyText: <EmptyState title="暂无授权记录" description="监护人授权和撤回历史会保留在这里" /> }}
          columns={[
            { title: "政策版本", dataIndex: "policy_version", width: 110 },
            { title: "监护人", dataIndex: "guardian_name", width: 130 },
            { title: "授权方式", dataIndex: "consent_method", render: (value: string) => consentMethodLabels[value] || value },
            { title: "状态", dataIndex: "status", width: 90, render: (value: string) => <Tag color={value === "granted" ? "green" : "default"}>{value === "granted" ? "有效" : "已撤回"}</Tag> },
            { title: "授权时间", dataIndex: "consented_at", render: formatBeijingTime },
            { title: "撤回时间", dataIndex: "revoked_at", render: (value?: string | null) => value ? formatBeijingTime(value) : "-" }
          ]}
        />
      </Card>

      <Modal
        title="记录监护人授权"
        open={consentModalOpen}
        okText="确认记录"
        cancelText="取消"
        confirmLoading={loading === "consent"}
        onCancel={() => setConsentModalOpen(false)}
        onOk={() => void consentForm.validateFields().then(grantConsent)}
        destroyOnClose
      >
        <Form form={consentForm} layout="vertical">
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="guardian_name" label="监护人姓名" rules={[{ required: true, message: "请输入监护人姓名" }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="relationship" label="与学生关系" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="guardian_contact" label="联系方式（安全加密保存）">
            <Input />
          </Form.Item>
          <Form.Item name="consent_method" label="授权方式" rules={[{ required: true }]}>
            <Select options={Object.entries(consentMethodLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="evidence_reference" label="授权凭证编号或存放位置">
            <Input placeholder="不在此处填写完整证件号码" />
          </Form.Item>
          <Form.Item name="scopes" label="授权范围" rules={[{ required: true, message: "至少选择一项" }]}>
            <Checkbox.Group options={scopeOptions} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="撤回监护人授权"
        open={Boolean(revokeConsent)}
        okText="确认撤回"
        okButtonProps={{ danger: true, disabled: !revokeReason.trim() }}
        confirmLoading={loading === "revoke"}
        onCancel={() => { setRevokeConsent(null); setRevokeReason(""); }}
        onOk={() => void revoke()}
      >
        <Paragraph>撤回后，如当前政策要求监护人授权，该学生将立即不能使用 AI 生成功能。</Paragraph>
        <Input.TextArea aria-label="撤回授权原因" rows={3} value={revokeReason} onChange={(event) => setRevokeReason(event.target.value)} placeholder="填写撤回原因" />
      </Modal>

      <Modal
        title="永久删除学生个人数据"
        open={Boolean(deletionPreflight)}
        width={720}
        okText="永久删除"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: deleteConfirmation !== deletionPreflight?.student_name }}
        confirmLoading={loading === "delete"}
        onCancel={() => setDeletionPreflight(null)}
        onOk={() => void deleteStudent()}
      >
        {deletionPreflight && (
          <Space direction="vertical" size={14} className="fullWidth">
            <Alert
              type="error"
              showIcon
              message={`将永久删除 ${deletionPreflight.student_name} 的个人数据`}
              description="操作使用 10 分钟有效的预检快照。若数据发生变化，后端会拒绝删除并要求重新预检。"
            />
            <Descriptions size="small" bordered column={{ xs: 1, sm: 2 }}>
              <Descriptions.Item label="作品">{deletionPreflight.counts.projects || 0}</Descriptions.Item>
              <Descriptions.Item label="作业提交">{deletionPreflight.counts.submissions || 0}</Descriptions.Item>
              <Descriptions.Item label="工作流运行">{deletionPreflight.counts.workflow_runs || 0}</Descriptions.Item>
              <Descriptions.Item label="视频任务">{deletionPreflight.counts.video_tasks || 0}</Descriptions.Item>
              <Descriptions.Item label="受管文件">{deletionPreflight.managed_file_count}（{formatBytes(deletionPreflight.managed_file_bytes)}）</Descriptions.Item>
              <Descriptions.Item label="旧本地备份">{deletionPreflight.backup_file_count}</Descriptions.Item>
              <Descriptions.Item label="共享文件保留">{deletionPreflight.shared_file_count}</Descriptions.Item>
              <Descriptions.Item label="外部引用仅移除记录">{deletionPreflight.external_reference_count}</Descriptions.Item>
            </Descriptions>
            <List size="small" dataSource={deletionPreflight.consequences} renderItem={(item) => <List.Item>{item}</List.Item>} />
            <Input.TextArea aria-label="删除原因或监护人申请编号" rows={2} value={deleteReason} onChange={(event) => setDeleteReason(event.target.value)} placeholder="删除原因或监护人申请编号（可选）" />
            <div>
              <Text strong>输入学生姓名“{deletionPreflight.student_name}”确认：</Text>
              <Input aria-label="输入学生姓名确认永久删除" className="mt8" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} />
            </div>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
