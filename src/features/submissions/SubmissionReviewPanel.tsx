import {
  Alert, App as AntApp, Button, Card, Checkbox, Col, Collapse, Drawer, Form, Input, InputNumber, List, Popconfirm, Row, Select, Space, Spin, Statistic, Tag, Typography
} from "antd";
import dayjs from "dayjs";
import { ClipboardList, Eye, FileDown, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { IconTitle } from "../../components/IconTitle";
import type { FeedbackTemplateItem, Project, SubmissionStatistics, SubmissionVersion, TaskSubmission } from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { projectTypeLabel, submissionStatusColor, submissionStatusLabel } from "../../lib/domain";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { ProjectFileStatus } from "../projects/ProjectLibrary";
import { SubmissionVersionFilePreview } from "./SubmissionVersionFilePreview";


const { Text, Paragraph } = Typography;

export function SubmissionReviewPanel({
  submissions,
  onRefresh,
  audience = "teacher",
}: {
  submissions: TaskSubmission[];
  onRefresh: () => Promise<void>;
  audience?: "teacher" | "admin";
}) {
  const { message } = AntApp.useApp();
  const [reviewingId, setReviewingId] = useState<number | null>(null);
  const [previewProject, setPreviewProject] = useState<Project | null>(null);
  const [previewSubmissionVersion, setPreviewSubmissionVersion] = useState<SubmissionVersion | null>(null);
  const [loadingSubmissionId, setLoadingSubmissionId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [submissionKeyword, setSubmissionKeyword] = useState("");
  const [bulkReviewing, setBulkReviewing] = useState(false);
  const [templateForm] = Form.useForm<{ name: string; content: string }>();
  const [feedbackTemplates, setFeedbackTemplates] = useState<FeedbackTemplateItem[]>([]);
  const [statistics, setStatistics] = useState<SubmissionStatistics | null>(null);
  const [supportLoading, setSupportLoading] = useState(false);

  const loadReviewSupport = async () => {
    const [templateRes, statisticsRes] = await Promise.all([api.get("/api/feedback-templates"), api.get("/api/submissions/statistics")]);
    setFeedbackTemplates(templateRes.data.templates || []);
    setStatistics(statisticsRes.data);
  };

  useEffect(() => {
    void loadReviewSupport().catch(() => undefined);
  }, [submissions.length]);

  const createFeedbackTemplate = async (values: { name: string; content: string }) => {
    setSupportLoading(true);
    try {
      await api.post("/api/feedback-templates", values);
      templateForm.resetFields();
      await loadReviewSupport();
      message.success("评语模板已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSupportLoading(false);
    }
  };

  const deleteFeedbackTemplate = async (templateId: number) => {
    try {
      await api.delete(`/api/feedback-templates/${templateId}`);
      await loadReviewSupport();
      message.success("评语模板已删除");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const exportReviewCsv = async () => {
    setSupportLoading(true);
    try {
      const res = await api.get("/api/submissions/export", { responseType: "blob" });
      if (await saveBlobFile(res.data, `CoderAI-批改记录-${dayjs().format("YYYYMMDD-HHmmss")}.csv`)) {
        message.success("批改记录已导出");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSupportLoading(false);
    }
  };

  const filteredSubmissions = useMemo(() => {
    const keyword = submissionKeyword.trim().toLowerCase();
    return submissions.filter((submission) => {
      const matchesStatus = statusFilter === "all" || submission.status === statusFilter;
      const matchesKeyword =
        !keyword ||
        submission.task_title.toLowerCase().includes(keyword) ||
        submission.student_name.toLowerCase().includes(keyword) ||
        submission.project_title.toLowerCase().includes(keyword) ||
        submission.classroom_name.toLowerCase().includes(keyword);
      return matchesStatus && matchesKeyword;
    });
  }, [statusFilter, submissionKeyword, submissions]);

  const reviewSubmission = async (submission: TaskSubmission, values: { status: string; score?: number; feedback: string; feedback_template?: number; is_featured?: boolean }) => {
    if (submission.can_review === false) {
      message.warning("这份提交当前仅供查看");
      return;
    }
    if (submission.student_archived && audience !== "admin") {
      message.warning("归档学员的历史提交仅供查看");
      return;
    }
    setReviewingId(submission.id);
    try {
      const template = feedbackTemplates.find((item) => item.id === values.feedback_template);
      await api.put(`/api/submissions/${submission.id}/review`, {
        status: values.status || "reviewed",
        score: values.score ?? null,
        feedback: values.feedback || template?.content || "",
        is_featured: Boolean(values.is_featured)
      });
      await onRefresh();
      await loadReviewSupport();
      message.success("批改已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setReviewingId(null);
    }
  };

  const bulkReview = async (status: "reviewed" | "returned") => {
    const targets = filteredSubmissions.filter(
      (submission) => submission.status === "submitted" && submission.can_review !== false && (audience === "admin" || !submission.student_archived),
    );
    if (!targets.length) {
      message.info("当前筛选结果中没有待批改提交");
      return;
    }
    setBulkReviewing(true);
    try {
      for (const submission of targets) {
        await api.put(`/api/submissions/${submission.id}/review`, {
          status,
          score: submission.score ?? null,
          feedback: status === "reviewed" ? submission.feedback || "已批改。" : submission.feedback || "请根据课堂要求修改后重新提交。"
        });
      }
      await onRefresh();
      message.success(`已批量处理 ${targets.length} 份提交`);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBulkReviewing(false);
    }
  };

  const openSubmittedProject = async (submission: TaskSubmission) => {
    setLoadingSubmissionId(submission.id);
    try {
      if (submission.project_id) {
        const res = await api.get(`/api/projects/${submission.project_id}`);
        setPreviewProject(res.data.project);
      } else {
        const res = await api.get(`/api/submissions/${submission.id}/versions`);
        const latestVersion = (res.data.versions || [])[0] as SubmissionVersion | undefined;
        if (!latestVersion) {
          message.info("这份提交暂无可预览版本");
          return;
        }
        setPreviewSubmissionVersion(latestVersion);
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoadingSubmissionId(null);
    }
  };

  return (
    <Card className="mt16" title={<IconTitle icon={<ClipboardList size={18} />} text="作业提交与批改" />}>
      <Row gutter={[12, 12]} className="mb16">
        <Col xs={12} md={4}><Statistic title="提交总数" value={statistics?.overall.total || 0} /></Col>
        <Col xs={12} md={4}><Statistic title="已批改" value={statistics?.overall.reviewed || 0} /></Col>
        <Col xs={12} md={4}><Statistic title="平均分" value={statistics?.overall.average_score ?? "-"} /></Col>
        <Col xs={12} md={4}><Statistic title="优秀作品" value={statistics?.overall.featured || 0} /></Col>
        <Col xs={12} md={4}><Statistic title="逾期" value={statistics?.overall.late || 0} /></Col>
        <Col xs={12} md={4}><Button icon={<FileDown size={16} />} loading={supportLoading} onClick={exportReviewCsv}>导出 CSV</Button></Col>
      </Row>
      {statistics?.by_classroom?.length ? (
        <Space wrap className="mb16">
          {statistics.by_classroom.map((item) => <Tag color="blue" key={String(item.classroom_id)}>班级：{item.classroom_name} · 提交 {item.total} · 平均 {item.average_score ?? "-"}</Tag>)}
        </Space>
      ) : null}
      <Form form={templateForm} layout="inline" onFinish={createFeedbackTemplate} className="mb16">
        <Form.Item name="name" label="模板名称" rules={[{ required: true }]}><Input placeholder="例如：创意优秀" /></Form.Item>
        <Form.Item name="content" label="评语内容" rules={[{ required: true }]} className="reviewFeedbackItem"><Input placeholder="输入可复用的评语" /></Form.Item>
        <Button type="primary" htmlType="submit" loading={supportLoading}>保存评语模板</Button>
      </Form>
      {feedbackTemplates.length > 0 && (
        <Space wrap className="mb16">
          {feedbackTemplates.map((template) => (
            <Popconfirm key={template.id} title="删除这个评语模板？" onConfirm={() => deleteFeedbackTemplate(template.id)}>
              <Tag closable onClose={(event) => event.preventDefault()}>{template.name}</Tag>
            </Popconfirm>
          ))}
        </Space>
      )}
      <Row gutter={[12, 12]} className="mb16">
        <Col xs={24} md={10}>
          <Input
            placeholder="搜索任务、学生、作品或班级"
            value={submissionKeyword}
            onChange={(event) => setSubmissionKeyword(event.target.value)}
          />
        </Col>
        <Col xs={24} md={6}>
          <Select
            className="fullWidth"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: "all", label: "全部状态" },
              { value: "submitted", label: "待批改" },
              { value: "reviewed", label: "已批改" },
              { value: "returned", label: "需修改" }
            ]}
          />
        </Col>
        <Col xs={24} md={8}>
          <Space wrap>
            <Tag color="orange">待批改 {submissions.filter((item) => item.status === "submitted").length}</Tag>
            <Tag color="green">已批改 {submissions.filter((item) => item.status === "reviewed").length}</Tag>
            <Tag color="red">需修改 {submissions.filter((item) => item.status === "returned").length}</Tag>
          </Space>
        </Col>
        <Col span={24}>
          <Space wrap>
            <Popconfirm
              title="批量标记已批改"
              description="将当前筛选结果中的待批改提交全部标记为已批改。"
              onConfirm={() => bulkReview("reviewed")}
            >
              <Button loading={bulkReviewing}>筛选结果批量已批改</Button>
            </Popconfirm>
            <Popconfirm
              title="批量退回修改"
              description="将当前筛选结果中的待批改提交全部标记为需修改。"
              onConfirm={() => bulkReview("returned")}
            >
              <Button danger loading={bulkReviewing}>筛选结果批量需修改</Button>
            </Popconfirm>
          </Space>
        </Col>
      </Row>
      <List
        dataSource={filteredSubmissions}
        locale={{ emptyText: "暂无提交记录" }}
        renderItem={(submission) => (
          <List.Item>
            <Space direction="vertical" size={10} className="fullWidth">
              <Space wrap>
                <Text strong>{submission.task_title}</Text>
                {submission.package_title && <Tag color="geekblue">{submission.package_title}</Tag>}
                <Tag color="blue">{submission.student_name}</Tag>
                {submission.student_archived && <Tag color="default">归档学员 · 历史只读</Tag>}
                {submission.course_access_status === "revoked" && <Tag color="orange">课程权限已撤销 · 历史只读</Tag>}
                {submission.classroom_name && <Tag>{submission.classroom_name}</Tag>}
                <Tag color={submissionStatusColor(submission.status)}>{submissionStatusLabel(submission.status)}</Tag>
                <Tag color="purple">版本 {submission.version_count || 1}</Tag>
                {submission.is_late && <Tag color="red">逾期</Tag>}
                {submission.is_featured && <Tag color="gold">优秀作品</Tag>}
                <Text type="secondary">{formatBeijingTime(submission.updated_at)}</Text>
              </Space>
              <Space wrap>
                <Text>提交作品：{submission.project_title}</Text>
                <Button
                  size="small"
                  icon={<Eye size={15} />}
                  loading={loadingSubmissionId === submission.id}
                  onClick={() => openSubmittedProject(submission)}
                >
                  查看作品
                </Button>
              </Space>
              <SubmissionHistoryCollapse submission={submission} />
              {submission.can_review === false || (submission.student_archived && audience !== "admin") ? (
                <Alert
                  type="info"
                  showIcon
                  message={submission.course_access_status === "revoked" ? "课程权限已撤销 · 历史只读" : "历史记录只读"}
                  description={submission.read_only_reason === "archived_student"
                    ? "学员已归档，评分、反馈和优秀作品状态不可修改。"
                    : submission.read_only_reason === "course_access_revoked"
                      ? "已有排课和提交继续保留，当前教师不能继续批改。"
                      : "当前教师没有这项排课的批改权限。"}
                />
              ) : <Form
                layout="vertical"
                className="reviewFormGrid"
                onFinish={(values) => reviewSubmission(submission, values)}
                initialValues={{
                  status: submission.status === "returned" ? "returned" : "reviewed",
                  score: submission.score ?? undefined,
                  feedback: submission.feedback,
                  is_featured: submission.is_featured
                }}
              >
                <Form.Item name="status" label="处理">
                  <Select
                    className="fullWidth"
                    options={[
                      { value: "reviewed", label: "已批改" },
                      { value: "returned", label: "需修改" }
                    ]}
                  />
                </Form.Item>
                <Form.Item label="分数">
                  <div className="reviewScoreControl">
                    <Form.Item
                      name="score"
                      noStyle
                      rules={[{ type: "number", min: 0, max: submission.max_score, message: `请输入 0 到 ${submission.max_score} 的整数` }]}
                    >
                      <InputNumber min={0} max={submission.max_score} precision={0} className="scoreInputNumber" />
                    </Form.Item>
                    <Text type="secondary" className="reviewScoreMaximum">/ {submission.max_score}</Text>
                  </div>
                </Form.Item>
                <Form.Item name="feedback_template" label="评语模板">
                  <Select className="fullWidth" allowClear placeholder="选择模板" options={feedbackTemplates.map((item) => ({ value: item.id, label: item.name }))} />
                </Form.Item>
                <Form.Item name="feedback" label="反馈">
                  <Input placeholder="给学生的反馈" />
                </Form.Item>
                <Form.Item name="is_featured" valuePropName="checked" className="reviewFeaturedItem"><Checkbox>优秀作品</Checkbox></Form.Item>
                <Button className="reviewSubmitButton" type="primary" htmlType="submit" loading={reviewingId === submission.id}>
                  保存批改
                </Button>
              </Form>}
            </Space>
          </List.Item>
        )}
      />
      <Drawer
        title={previewProject ? previewProject.title : "作品详情"}
        open={Boolean(previewProject)}
        width={760}
        onClose={() => setPreviewProject(null)}
      >
        {previewProject && (
          <Space direction="vertical" size={16} className="fullWidth">
            <Row gutter={12}>
              <Col xs={24} md={12}>
                <Text type="secondary">学生</Text>
                <div>{previewProject.owner_name}</div>
              </Col>
              <Col xs={24} md={12}>
                <Text type="secondary">保存时间</Text>
                <div>{formatBeijingTime(previewProject.created_at)}</div>
              </Col>
            </Row>
            <Space wrap>
              <Tag>{previewProject.project_type}</Tag>
              <ProjectFileStatus project={previewProject} />
            </Space>
            <article className="markdownPreview">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {previewProject.summary || "这个作品暂无可预览内容。"}
              </ReactMarkdown>
            </article>
          </Space>
        )}
      </Drawer>
      <Drawer
        title={previewSubmissionVersion ? `${previewSubmissionVersion.project_title} · 第 ${previewSubmissionVersion.version_number} 版` : "提交文件"}
        open={Boolean(previewSubmissionVersion)}
        width={820}
        onClose={() => setPreviewSubmissionVersion(null)}
      >
        {previewSubmissionVersion && (
          <Space direction="vertical" size={16} className="fullWidth">
            <article className="markdownPreview">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {previewSubmissionVersion.project_summary || "该版本没有 Markdown 内容。"}
              </ReactMarkdown>
            </article>
            <SubmissionVersionFilePreview version={previewSubmissionVersion} />
          </Space>
        )}
      </Drawer>
    </Card>
  );
}

function SubmissionVersionHistoryList({ versions }: { versions: SubmissionVersion[] }) {
  const [previewVersionId, setPreviewVersionId] = useState<number | null>(null);

  return (
    <List
      dataSource={versions}
      locale={{ emptyText: "暂无版本记录" }}
      renderItem={(version) => {
        const previewing = previewVersionId === version.id;
        return (
          <List.Item>
            <Space direction="vertical" size={10} className="fullWidth">
              <Space wrap>
                <Tag color="purple">版本 {version.version_number}</Tag>
                {version.is_late && <Tag color="red">逾期</Tag>}
                <Text strong>{version.project_title}</Text>
                <Text type="secondary">{formatBeijingTime(version.created_at)}</Text>
                <Button
                  size="small"
                  icon={<Eye size={15} />}
                  onClick={() => setPreviewVersionId(previewing ? null : version.id)}
                >
                  {previewing ? "收起预览" : "预览版本"}
                </Button>
              </Space>
              {previewing && (
                <div className="submissionHistoryPreview">
                  <article className="markdownPreview">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {version.project_summary || "这个版本暂无文字内容。"}
                    </ReactMarkdown>
                  </article>
                  <SubmissionVersionFilePreview version={version} />
                </div>
              )}
            </Space>
          </List.Item>
        );
      }}
    />
  );
}

function SubmissionHistoryCollapse({ submission }: { submission: TaskSubmission }) {
  const { message } = AntApp.useApp();
  const [versions, setVersions] = useState<SubmissionVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const loadVersions = async () => {
    if (loaded || loading) return;
    setLoading(true);
    try {
      const response = await api.get(`/api/submissions/${submission.id}/versions`);
      setVersions(response.data.versions || []);
      setLoaded(true);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Collapse
      size="small"
      className="submissionHistoryCollapse"
      onChange={(keys) => {
        if (keys.length) void loadVersions();
      }}
      items={[{
        key: "history",
        label: `提交历史（${submission.version_count || 0}）`,
        children: loading ? <Spin size="small" /> : <SubmissionVersionHistoryList versions={versions} />,
      }]}
    />
  );
}

export function SubmissionVersionsDrawer({
  submission,
  versions,
  loading,
  onClose
}: {
  submission: TaskSubmission | null;
  versions: SubmissionVersion[];
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer title={submission ? `${submission.task_title} · 提交历史` : "提交历史"} open={Boolean(submission)} width={760} onClose={onClose}>
      {loading ? <Alert type="info" showIcon message="正在读取提交历史" /> : (
        <SubmissionVersionHistoryList versions={versions} />
      )}
    </Drawer>
  );
}
