import { Alert, Button, Descriptions, Space, Spin, Tag, Typography } from "antd";
import { ArrowLeft, FileText } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import type { CourseScheduleItem, SubmissionVersion, TaskSubmission } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { SubmissionVersionFilePreview } from "../submissions/SubmissionVersionFilePreview";


const { Title, Text } = Typography;

export function StudentSubmissionVersionPage() {
  const { versionId } = useParams();
  const navigate = useNavigate();
  const [version, setVersion] = useState<SubmissionVersion | null>(null);
  const [submission, setSubmission] = useState<TaskSubmission | null>(null);
  const [schedule, setSchedule] = useState<CourseScheduleItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const response = await api.get(`/api/submission-versions/${versionId}`);
        const nextVersion = response.data.version as SubmissionVersion;
        setVersion(nextVersion);
        setSubmission(response.data.submission || null);
        setSchedule(response.data.schedule || null);
      } catch (unknownError) {
        setError(explainError(unknownError));
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [versionId]);

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
          {version.file && <SubmissionVersionFilePreview version={version} />}
        </Space>
      )}
    </div>
  );
}
