import { Alert, App, Button, Descriptions, Image, Space, Spin, Tag, Typography } from "antd";
import { ArrowLeft, Download, FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import type { CourseScheduleItem, SubmissionVersion, TaskSubmission } from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Title, Text } = Typography;

export function StudentSubmissionVersionPage() {
  const { submissionId, versionId } = useParams();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [version, setVersion] = useState<SubmissionVersion | null>(null);
  const [submission, setSubmission] = useState<TaskSubmission | null>(null);
  const [schedule, setSchedule] = useState<CourseScheduleItem | null>(null);
  const [fileUrl, setFileUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const versionFile = version?.file || version?.attachment || null;
  const mimeType = versionFile?.mime_type || "";
  const canInlinePreview = useMemo(
    () => Boolean(version?.file_available && (mimeType.startsWith("image/") || mimeType.startsWith("video/") || mimeType === "application/pdf" || mimeType.startsWith("text/"))),
    [mimeType, version?.file_available],
  );

  useEffect(() => {
    let objectUrl = "";
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const response = await api.get(`/api/submission-versions/${versionId}`);
        const nextVersion = response.data.version as SubmissionVersion;
        setVersion(nextVersion);
        setSubmission(response.data.submission || null);
        setSchedule(response.data.schedule || null);
        const nextMime = nextVersion.attachment?.mime_type || "";
        const inline = nextVersion.file_available && (
          nextMime.startsWith("image/") || nextMime.startsWith("video/") || nextMime === "application/pdf" || nextMime.startsWith("text/")
        );
        if (inline) {
          const fileResponse = await api.get(`/api/submission-versions/${versionId}/file`, { responseType: "blob" });
          objectUrl = URL.createObjectURL(fileResponse.data);
          setFileUrl(objectUrl);
        }
      } catch (unknownError) {
        setError(explainError(unknownError));
      } finally {
        setLoading(false);
      }
    };
    void load();
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [versionId]);

  const downloadFile = async () => {
    if (!version) return;
    try {
      const response = await api.get(`/api/submission-versions/${version.id}/file`, {
        params: { download: true },
        responseType: "blob",
      });
      await saveBlobFile(response.data, version.file?.original_file_name || version.attachment?.original_file_name || `${version.project_title}.bin`);
    } catch (unknownError) {
      message.error(explainError(unknownError));
    }
  };

  return (
    <div className="page submissionVersionPage">
      <div className="pageTitle submissionVersionTitle">
        <Button icon={<ArrowLeft size={16} />} onClick={() => navigate("/student/courses")}>返回课程</Button>
        <div>
          <Title level={2}>{version ? `${version.project_title} · 第 ${version.version_number} 版` : "提交版本"}</Title>
          <Text type="secondary">历史版本为提交时快照，不会随作品当前内容变化。</Text>
        </div>
      </div>
      {loading && <Spin size="large" />}
      {!loading && error && <Alert type="error" showIcon message="版本加载失败" description={error} />}
      {!loading && version && submission && (
        <Space direction="vertical" size={18} className="fullWidth">
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="课程">{schedule?.course_title || submission.task_title}</Descriptions.Item>
            <Descriptions.Item label="提交时间">{formatBeijingTime(version.created_at)}</Descriptions.Item>
            <Descriptions.Item label="提交状态"><Tag>{version.review_status}</Tag>{version.is_late && <Tag color="orange">逾期</Tag>}</Descriptions.Item>
            <Descriptions.Item label="评分">{version.score == null ? "尚未评分" : `${version.score}/${version.max_score}`}</Descriptions.Item>
            <Descriptions.Item label="教师反馈" span={2}>{version.feedback || "暂无反馈"}</Descriptions.Item>
          </Descriptions>
          <section className="submissionVersionContent">
            <Space className="mb16"><FileText size={18} /><Text strong>提交内容快照</Text></Space>
            <article className="markdownPreview">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{version.project_summary || "该版本没有 Markdown 内容。"}</ReactMarkdown>
            </article>
          </section>
          {versionFile && (
            <section className="submissionVersionContent">
              <Space wrap className="mb16">
                <Text strong>{versionFile.original_file_name}</Text>
                <Tag>{versionFile.mime_type || "文件"}</Tag>
                {versionFile.safety_status === "pending" && <Tag color="gold">等待安全复核</Tag>}
                {versionFile.safety_status === "rejected" && <Tag color="red">审核未通过</Tag>}
                <Button icon={<Download size={15} />} disabled={!version.file_available} onClick={() => void downloadFile()}>下载文件</Button>
              </Space>
              {canInlinePreview && fileUrl && mimeType.startsWith("image/") && <Image src={fileUrl} alt={version.project_title} />}
              {canInlinePreview && fileUrl && mimeType.startsWith("video/") && <video className="generatedVideo" src={fileUrl} controls />}
              {canInlinePreview && fileUrl && mimeType === "application/pdf" && <iframe className="submissionPdfPreview" src={fileUrl} title={version.project_title} />}
              {canInlinePreview && fileUrl && mimeType.startsWith("text/") && <iframe className="submissionTextPreview" src={fileUrl} title={version.project_title} />}
              {!canInlinePreview && <Alert type="info" showIcon message="该文件类型不支持在线预览，可通过鉴权下载查看。" />}
            </section>
          )}
        </Space>
      )}
    </div>
  );
}
