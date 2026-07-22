import {
  Alert,
  App as AntApp,
  Button,
  Col,
  DatePicker,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography
} from "antd";
import type { Dayjs } from "dayjs";
import { CheckCircle2, Database, Eye, PlayCircle, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";

const { Paragraph, Text } = Typography;
const EXECUTION_CONFIRMATION = "确认执行到期数据删除";

type RetentionCounts = {
  projects: number;
  workflow_runs: number;
  video_tasks: number;
  usage_logs: number;
  moderation_logs: number;
  teacher_audit_logs_anonymize: number;
};

type RetentionPreview = {
  retention_days: number;
  cutoff_at: string;
  generated_at: string;
  counts: RetentionCounts;
  exception_count: number;
  preview_hash: string;
  automatic_deletion: boolean;
};

type RetentionRequest = {
  id: number;
  status: "pending" | "approved" | "executed" | "canceled";
  cutoff_at: string;
  preview: RetentionPreview;
  requested_at: string;
  approved_at?: string | null;
  executed_at?: string | null;
  approval_note: string;
};

type RetentionException = {
  id: number;
  target_type: "student" | "record";
  target_id: string;
  reason: string;
  expires_at?: string | null;
  created_at: string;
};

type ExceptionForm = {
  target_type: "student" | "record";
  target_id: string;
  reason: string;
  expires_at?: Dayjs;
};

const countLabels: Array<[keyof RetentionCounts, string]> = [
  ["projects", "未提交作品"],
  ["workflow_runs", "工作流运行"],
  ["video_tasks", "视频任务"],
  ["usage_logs", "用量记录"],
  ["moderation_logs", "审核记录"],
  ["teacher_audit_logs_anonymize", "需去标识审计"]
];

const statusPresentation: Record<RetentionRequest["status"], { color: string; label: string }> = {
  pending: { color: "gold", label: "待审批" },
  approved: { color: "blue", label: "已批准" },
  executed: { color: "green", label: "已执行" },
  canceled: { color: "default", label: "已取消" }
};

export function RetentionGovernancePanel({
  policyRetentionDays,
  onExecuted
}: {
  policyRetentionDays: number;
  onExecuted: () => Promise<void>;
}) {
  const { message } = AntApp.useApp();
  const [exceptionForm] = Form.useForm<ExceptionForm>();
  const [retentionDays, setRetentionDays] = useState(policyRetentionDays);
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const [requests, setRequests] = useState<RetentionRequest[]>([]);
  const [exceptions, setExceptions] = useState<RetentionException[]>([]);
  const [loading, setLoading] = useState("");
  const [approving, setApproving] = useState<RetentionRequest | null>(null);
  const [approvalNote, setApprovalNote] = useState("");
  const [executing, setExecuting] = useState<RetentionRequest | null>(null);
  const [executionConfirmation, setExecutionConfirmation] = useState("");

  const loadGovernance = async () => {
    setLoading("load");
    try {
      const [requestsResponse, exceptionsResponse] = await Promise.all([
        api.get("/api/privacy/retention/requests"),
        api.get("/api/privacy/retention/exceptions")
      ]);
      setRequests(requestsResponse.data.requests || []);
      setExceptions(exceptionsResponse.data.exceptions || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    setRetentionDays(policyRetentionDays);
  }, [policyRetentionDays]);

  useEffect(() => {
    void loadGovernance();
  }, []);

  const totalCandidates = useMemo(
    () => preview ? Object.values(preview.counts).reduce((sum, count) => sum + Number(count || 0), 0) : 0,
    [preview]
  );

  const generatePreview = async () => {
    setLoading("preview");
    try {
      const response = await api.post("/api/privacy/retention/preview", { retention_days: retentionDays });
      setPreview(response.data.preview as RetentionPreview);
      message.success("到期数据预览已生成，尚未删除任何数据");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const createRequest = async () => {
    setLoading("request");
    try {
      const response = await api.post("/api/privacy/retention/requests", { retention_days: retentionDays });
      const next = response.data.request as RetentionRequest;
      setPreview(next.preview);
      await loadGovernance();
      message.success("已提交到期数据处理审批");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const approveRequest = async () => {
    if (!approving) return;
    setLoading(`approve-${approving.id}`);
    try {
      await api.post(`/api/privacy/retention/requests/${approving.id}/approve`, { note: approvalNote.trim() });
      setApproving(null);
      setApprovalNote("");
      await loadGovernance();
      message.success("到期数据处理已批准");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const executeRequest = async () => {
    if (!executing || executionConfirmation !== EXECUTION_CONFIRMATION) return;
    setLoading(`execute-${executing.id}`);
    try {
      await api.post(`/api/privacy/retention/requests/${executing.id}/execute`, {
        confirmation: executionConfirmation
      });
      setExecuting(null);
      setExecutionConfirmation("");
      setPreview(null);
      await Promise.all([loadGovernance(), onExecuted()]);
      message.success("已执行批准的到期数据处理");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const createException = async (values: ExceptionForm) => {
    setLoading("exception");
    try {
      await api.post("/api/privacy/retention/exceptions", {
        target_type: values.target_type,
        target_id: values.target_id.trim(),
        reason: values.reason.trim(),
        expires_at: values.expires_at?.toISOString() || null
      });
      exceptionForm.resetFields();
      await loadGovernance();
      if (preview) await generatePreview();
      message.success("数据保留例外已添加");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const removeException = async (id: number) => {
    setLoading(`exception-${id}`);
    try {
      await api.delete(`/api/privacy/retention/exceptions/${id}`);
      await loadGovernance();
      if (preview) await generatePreview();
      message.success("数据保留例外已移除");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  return (
    <div className="retentionGovernancePanel">
      <div className="sectionTitleRow">
        <IconTitle icon={<Database size={18} />} text="数据保留与到期处理" />
        <Button icon={<RefreshCw size={16} />} loading={loading === "load"} onClick={() => void loadGovernance()}>
          刷新
        </Button>
      </div>
      <Alert
        type="info"
        showIcon
        message="系统只扫描和生成审批，不会自动删除"
        description="执行前会再次校验预览快照；内容已变化时必须重新生成并审批。作业提交关联的作品不会进入自动到期候选。"
      />

      <Divider orientation="left">扫描与审批</Divider>
      <Row gutter={[12, 12]} align="bottom">
        <Col xs={24} sm={8} lg={6}>
          <Text strong>保留天数</Text>
          <InputNumber
            aria-label="到期数据保留天数"
            className="fullWidth mt8"
            min={30}
            max={3650}
            value={retentionDays}
            onChange={(value) => setRetentionDays(value || policyRetentionDays)}
          />
        </Col>
        <Col xs={24} sm={16} lg={18}>
          <Space wrap>
            <Button icon={<Eye size={16} />} loading={loading === "preview"} onClick={() => void generatePreview()}>
              生成删除预览
            </Button>
            <Button type="primary" icon={<CheckCircle2 size={16} />} loading={loading === "request"} onClick={() => void createRequest()}>
              提交审批
            </Button>
          </Space>
        </Col>
      </Row>

      {preview && (
        <div className="retentionPreview mt16">
          <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 4 }}>
            <Descriptions.Item label="候选记录">{totalCandidates}</Descriptions.Item>
            <Descriptions.Item label="截止时间">{formatBeijingTime(preview.cutoff_at)}</Descriptions.Item>
            <Descriptions.Item label="保留例外">{preview.exception_count}</Descriptions.Item>
            <Descriptions.Item label="预览指纹"><Text code>{preview.preview_hash.slice(0, 12)}</Text></Descriptions.Item>
            {countLabels.map(([key, label]) => (
              <Descriptions.Item key={key} label={label}>{preview.counts[key] || 0}</Descriptions.Item>
            ))}
          </Descriptions>
        </div>
      )}

      <Table<RetentionRequest>
        className="mt16"
        size="small"
        rowKey="id"
        loading={loading === "load"}
        pagination={{ pageSize: 6, hideOnSinglePage: true }}
        dataSource={requests}
        scroll={{ x: 860 }}
        locale={{ emptyText: <EmptyState title="暂无到期处理审批" description="先生成预览，再提交审批" /> }}
        columns={[
          { title: "编号", dataIndex: "id", width: 78, render: (value: number) => `#${value}` },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (value: RetentionRequest["status"]) => <Tag color={statusPresentation[value].color}>{statusPresentation[value].label}</Tag>
          },
          { title: "截止时间", dataIndex: "cutoff_at", width: 180, render: formatBeijingTime },
          { title: "申请时间", dataIndex: "requested_at", width: 180, render: formatBeijingTime },
          {
            title: "候选记录",
            width: 100,
            render: (_, item) => Object.values(item.preview.counts || {}).reduce((sum, count) => sum + Number(count || 0), 0)
          },
          { title: "审批说明", dataIndex: "approval_note", ellipsis: true, render: (value: string) => value || "-" },
          {
            title: "操作",
            key: "actions",
            fixed: "right",
            width: 170,
            render: (_, item) => (
              <Space>
                {item.status === "pending" && (
                  <Button size="small" onClick={() => setApproving(item)}>审批</Button>
                )}
                {item.status === "approved" && (
                  <Button danger size="small" icon={<PlayCircle size={14} />} onClick={() => setExecuting(item)}>执行</Button>
                )}
                {item.status === "executed" && <Text type="secondary">已完成</Text>}
              </Space>
            )
          }
        ]}
      />

      <Divider orientation="left">保留例外</Divider>
      <Form<ExceptionForm>
        form={exceptionForm}
        layout="vertical"
        initialValues={{ target_type: "student" }}
        onFinish={createException}
      >
        <Row gutter={12}>
          <Col xs={24} sm={6}>
            <Form.Item name="target_type" label="例外类型">
              <Select options={[{ value: "student", label: "学生全部数据" }, { value: "record", label: "指定记录" }]} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={8}>
            <Form.Item
              name="target_id"
              label="目标标识"
              rules={[{ required: true, message: "请输入学生 ID 或 table:id" }]}
            >
              <Input placeholder="学生 ID，或 projects:123" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={10}>
            <Form.Item name="expires_at" label="例外到期时间">
              <DatePicker showTime className="fullWidth" placeholder="留空表示长期有效" />
            </Form.Item>
          </Col>
          <Col xs={24} lg={18}>
            <Form.Item name="reason" label="保留原因" rules={[{ required: true, min: 3, message: "至少填写 3 个字符" }]}>
              <Input placeholder="例如监管留存、争议处理或监护人申请" maxLength={300} />
            </Form.Item>
          </Col>
          <Col xs={24} lg={6}>
            <Form.Item label="操作">
              <Button block type="primary" htmlType="submit" icon={<Plus size={16} />} loading={loading === "exception"}>
                添加例外
              </Button>
            </Form.Item>
          </Col>
        </Row>
      </Form>

      <Table<RetentionException>
        size="small"
        rowKey="id"
        pagination={{ pageSize: 6, hideOnSinglePage: true }}
        dataSource={exceptions}
        scroll={{ x: 760 }}
        locale={{ emptyText: <EmptyState title="暂无保留例外" description="到期扫描会按当前隐私政策执行" /> }}
        columns={[
          { title: "范围", dataIndex: "target_type", width: 120, render: (value: string) => value === "student" ? "学生全部数据" : "指定记录" },
          { title: "目标标识", dataIndex: "target_id", width: 160 },
          { title: "保留原因", dataIndex: "reason" },
          { title: "到期时间", dataIndex: "expires_at", width: 180, render: (value?: string | null) => value ? formatBeijingTime(value) : "长期有效" },
          {
            title: "操作",
            width: 90,
            fixed: "right",
            render: (_, item) => (
              <Popconfirm title="移除这条保留例外？" description="移除后，该目标会重新参与下一次到期扫描。" onConfirm={() => void removeException(item.id)}>
                <Button danger size="small" aria-label="移除保留例外" icon={<Trash2 size={14} />} loading={loading === `exception-${item.id}`} />
              </Popconfirm>
            )
          }
        ]}
      />

      <Modal
        title={`审批到期数据处理 #${approving?.id || ""}`}
        open={Boolean(approving)}
        okText="批准"
        cancelText="取消"
        confirmLoading={Boolean(approving && loading === `approve-${approving.id}`)}
        onCancel={() => { setApproving(null); setApprovalNote(""); }}
        onOk={() => void approveRequest()}
      >
        <Paragraph>批准前系统会重新计算候选数据；预览发生变化时，本次审批会被拒绝。</Paragraph>
        <Input.TextArea aria-label="到期数据审批说明" rows={3} maxLength={300} value={approvalNote} onChange={(event) => setApprovalNote(event.target.value)} placeholder="审批说明（可选）" />
      </Modal>

      <Modal
        title={`执行到期数据处理 #${executing?.id || ""}`}
        open={Boolean(executing)}
        okText="确认执行"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled: executionConfirmation !== EXECUTION_CONFIRMATION }}
        confirmLoading={Boolean(executing && loading === `execute-${executing.id}`)}
        onCancel={() => { setExecuting(null); setExecutionConfirmation(""); }}
        onOk={() => void executeRequest()}
      >
        <Space direction="vertical" size={12} className="fullWidth">
          <Alert type="error" showIcon message="此操作会删除经批准的到期数据，并对旧审计记录去标识" />
          <Text>输入“{EXECUTION_CONFIRMATION}”继续：</Text>
          <Input aria-label="确认执行到期数据删除" value={executionConfirmation} onChange={(event) => setExecutionConfirmation(event.target.value)} />
        </Space>
      </Modal>
    </div>
  );
}
