import { Alert, App, Button, Image, Space, Spin, Tag, Typography } from "antd";
import { Download, FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { SubmissionVersion } from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";


const { Text } = Typography;

export function SubmissionVersionFilePreview({ version }: { version: SubmissionVersion }) {
  const { message } = App.useApp();
  const [objectUrl, setObjectUrl] = useState("");
  const [textContent, setTextContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const file = version.file || null;
  const mimeType = (file?.mime_type || "").toLowerCase();
  const fileName = file?.original_file_name || `${version.project_title}.bin`;
  const isMarkdown = mimeType === "text/markdown" || /\.md(?:own)?$/i.test(fileName);
  const isText = isMarkdown || mimeType.startsWith("text/");
  const canInlinePreview = useMemo(
    () => Boolean(
      version.file_available
      && file
      && (isText || mimeType.startsWith("image/") || mimeType.startsWith("video/") || mimeType === "application/pdf")
    ),
    [file, isText, mimeType, version.file_available],
  );

  useEffect(() => {
    let active = true;
    let nextObjectUrl = "";
    setObjectUrl("");
    setTextContent("");
    setError("");
    if (!canInlinePreview) return () => undefined;

    setLoading(true);
    void api.get(`/api/submission-versions/${version.id}/file`, { responseType: "blob" })
      .then(async (response) => {
        if (!active) return;
        if (isText) {
          setTextContent(await response.data.text());
        } else {
          nextObjectUrl = URL.createObjectURL(response.data);
          setObjectUrl(nextObjectUrl);
        }
      })
      .catch((unknownError) => {
        if (active) setError(explainError(unknownError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
      if (nextObjectUrl) URL.revokeObjectURL(nextObjectUrl);
    };
  }, [canInlinePreview, isText, version.id]);

  const downloadFile = async () => {
    try {
      const response = await api.get(`/api/submission-versions/${version.id}/file`, {
        params: { download: true },
        responseType: "blob",
      });
      await saveBlobFile(response.data, fileName);
    } catch (unknownError) {
      message.error(explainError(unknownError));
    }
  };

  if (!file) {
    return <Alert type="info" showIcon message="该版本没有附加文件" />;
  }

  return (
    <section className="submissionVersionContent submissionVersionFilePreview">
      <Space wrap className="mb16">
        <FileText size={17} />
        <Text strong>{fileName}</Text>
        <Tag>{file.mime_type || "文件"}</Tag>
        {file.safety_status === "pending" && <Tag color="gold">等待安全复核</Tag>}
        {file.safety_status === "rejected" && <Tag color="red">审核未通过</Tag>}
        <Button
          size="small"
          icon={<Download size={15} />}
          disabled={!version.file_available}
          onClick={() => void downloadFile()}
        >
          下载文件
        </Button>
      </Space>
      {loading && <Spin size="small" />}
      {!loading && error && <Alert type="error" showIcon message="文件预览失败" description={error} />}
      {!loading && !error && canInlinePreview && mimeType.startsWith("image/") && objectUrl && (
        <Image src={objectUrl} alt={version.project_title} />
      )}
      {!loading && !error && canInlinePreview && mimeType.startsWith("video/") && objectUrl && (
        <video className="generatedVideo" src={objectUrl} controls />
      )}
      {!loading && !error && canInlinePreview && mimeType === "application/pdf" && objectUrl && (
        <iframe className="submissionPdfPreview" src={objectUrl} title={version.project_title} />
      )}
      {!loading && !error && canInlinePreview && isMarkdown && (
        <article className="markdownPreview submissionMarkdownFilePreview">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{textContent}</ReactMarkdown>
        </article>
      )}
      {!loading && !error && canInlinePreview && isText && !isMarkdown && (
        <pre className="submissionPlainTextPreview">{textContent}</pre>
      )}
      {!loading && !error && !canInlinePreview && (
        <Alert
          type="info"
          showIcon
          message={version.file_available ? "该文件类型不支持在线预览，可下载后查看。" : "文件当前不可用。"}
        />
      )}
    </section>
  );
}
