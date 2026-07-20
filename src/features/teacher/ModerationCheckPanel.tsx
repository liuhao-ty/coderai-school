import {
  Alert, App as AntApp, Button, Card, Col, Drawer, Form, Input, List, Popconfirm, Row, Space, Tabs, Tag, Typography
} from "antd";
import axios from "axios";
import { Eye, RotateCcw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { ModerationLogItem } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Text, Paragraph } = Typography;

export function ModerationCheckPanel({ canManageSettings = true }: { canManageSettings?: boolean }) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm<{ text: string }>();
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<{ passed: boolean; reason: string } | null>(null);
  const [blockedWordsText, setBlockedWordsText] = useState("");
  const [logs, setLogs] = useState<ModerationLogItem[]>([]);
  const [imageReviews, setImageReviews] = useState<ModerationLogItem[]>([]);
  const [previewReview, setPreviewReview] = useState<ModerationLogItem | null>(null);
  const [reviewPreviewUrl, setReviewPreviewUrl] = useState("");
  const [reviewingImageId, setReviewingImageId] = useState<number | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);

  const loadModerationData = async () => {
    try {
      const [settingsRes, logsRes, imagesRes] = await Promise.all([
        api.get("/api/moderation/settings"),
        api.get("/api/moderation/logs"),
        api.get("/api/moderation/images", { params: { status: "all" } })
      ]);
      setBlockedWordsText((settingsRes.data.blocked_words || []).join("\n"));
      setLogs(logsRes.data.logs || []);
      setImageReviews(imagesRes.data.items || []);
    } catch (error) {
      message.error(explainError(error));
    }
  };

  useEffect(() => {
    loadModerationData();
  }, []);

  const checkText = async (values: { text: string }) => {
    setChecking(true);
    setResult(null);
    try {
      const res = await api.post("/api/moderation/check", values);
      setResult(res.data);
      await loadModerationData();
      message.success("内容检测通过");
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.data?.detail) {
        const detail = error.response.data.detail;
        setResult({ passed: false, reason: detail.message || "内容检测未通过" });
      }
      await loadModerationData();
      message.error(explainError(error));
    } finally {
      setChecking(false);
    }
  };

  const saveSettings = async () => {
    setSettingsLoading(true);
    try {
      const blocked_words = blockedWordsText
        .split(/\r?\n|,/)
        .map((word) => word.trim())
        .filter(Boolean);
      const res = await api.post("/api/moderation/settings", { blocked_words });
      setBlockedWordsText((res.data.blocked_words || []).join("\n"));
      message.success("敏感词库已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSettingsLoading(false);
    }
  };

  const openImageReview = async (item: ModerationLogItem) => {
    if (reviewPreviewUrl && !reviewPreviewUrl.startsWith("http")) URL.revokeObjectURL(reviewPreviewUrl);
    setPreviewReview(item);
    setReviewPreviewUrl("");
    try {
      if (item.resource_path.startsWith("http")) {
        setReviewPreviewUrl(item.resource_path);
      } else {
        const res = await api.get(`/api/moderation/images/${item.id}/file`, { responseType: "blob" });
        setReviewPreviewUrl(URL.createObjectURL(res.data));
      }
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const closeImageReview = () => {
    if (reviewPreviewUrl && !reviewPreviewUrl.startsWith("http")) URL.revokeObjectURL(reviewPreviewUrl);
    setReviewPreviewUrl("");
    setPreviewReview(null);
  };

  const reviewImage = async (item: ModerationLogItem, status: "approved" | "rejected") => {
    setReviewingImageId(item.id);
    try {
      await api.post(`/api/moderation/images/${item.id}/review`, {
        status,
        note: status === "approved" ? "教师人工复核通过。" : "教师人工复核未通过。"
      });
      await loadModerationData();
      closeImageReview();
      message.success(status === "approved" ? "图片已批准并对学生开放" : "图片已拒绝并继续隐藏");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setReviewingImageId(null);
    }
  };

  return (
    <>
    <Card title={<IconTitle icon={<ShieldCheck size={18} />} text="内容安全检测" />}>
      <Tabs
        items={[
          {
            key: "check",
            label: "检测",
            children: (
              <>
                <Form form={form} layout="vertical" onFinish={checkText}>
                  <Form.Item name="text" label="待检测内容" rules={[{ required: true, message: "请输入需要检测的内容" }]}>
                    <Input.TextArea rows={6} placeholder="粘贴学生提示词、作品摘要或课堂任务说明" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={checking}>
                    开始检测
                  </Button>
                </Form>
                {result && (
                  <Alert
                    className="mt16"
                    type={result.passed ? "success" : "error"}
                    showIcon
                    message={result.passed ? "适合课堂使用" : "需要教师复核"}
                    description={result.reason || "未发现当前敏感词库中的风险内容。"}
                  />
                )}
              </>
            )
          },
          ...(canManageSettings ? [{
            key: "words",
            label: "敏感词",
            children: (
              <Space direction="vertical" size={12} className="fullWidth">
                <Input.TextArea
                  rows={8}
                  value={blockedWordsText}
                  onChange={(event) => setBlockedWordsText(event.target.value)}
                  placeholder="每行一个敏感词，也支持逗号分隔"
                />
                <Button type="primary" onClick={saveSettings} loading={settingsLoading}>
                  保存敏感词库
                </Button>
              </Space>
            )
          }] : []),
          {
            key: "images",
            label: `图片复核（${imageReviews.filter((item) => item.status === "pending").length}）`,
            children: (
              <Space direction="vertical" size={12} className="fullWidth">
                <Alert
                  type="info"
                  showIcon
                  message="自动审核优先，人工复核兜底"
                  description="自动审核不可用或命中风险的图片不会向学生展示；教师预览并批准后才会进入学生作品库。"
                />
                <List
                  size="small"
                  dataSource={imageReviews}
                  locale={{ emptyText: "暂无图片审核记录" }}
                  renderItem={(item) => (
                    <List.Item
                      actions={[
                        <Button key="preview" size="small" icon={<Eye size={15} />} onClick={() => openImageReview(item)}>预览</Button>,
                        <Button key="approve" size="small" type="primary" loading={reviewingImageId === item.id} onClick={() => reviewImage(item, "approved")}>批准</Button>,
                        <Popconfirm key="reject" title="拒绝这张图片？" onConfirm={() => reviewImage(item, "rejected")}>
                          <Button size="small" danger loading={reviewingImageId === item.id}>拒绝</Button>
                        </Popconfirm>
                      ]}
                    >
                      <List.Item.Meta
                        title={
                          <Space wrap>
                            <Tag color={item.status === "approved" ? "green" : item.status === "pending" ? "orange" : "red"}>
                              {item.status === "approved" ? "已通过" : item.status === "pending" ? "待复核" : "已拒绝"}
                            </Tag>
                            {item.project_id && <Tag color="blue">作品 #{item.project_id}</Tag>}
                            <Text type="secondary">{formatBeijingTime(item.created_at)}</Text>
                          </Space>
                        }
                        description={<Space direction="vertical" size={4}><Text>{item.input_text}</Text><Text type="secondary">{item.reason}</Text></Space>}
                      />
                    </List.Item>
                  )}
                />
              </Space>
            )
          },
          {
            key: "logs",
            label: "审核日志",
            children: (
              <List
                size="small"
                dataSource={logs}
                locale={{ emptyText: "暂无审核记录" }}
                renderItem={(log) => (
                  <List.Item>
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <Tag color={log.status === "pending" ? "orange" : log.passed ? "green" : "red"}>{log.status === "pending" ? "待复核" : log.passed ? "通过" : "拦截"}</Tag>
                          <Tag>{log.content_stage === "image_output" ? "图片输出" : log.content_stage === "output" ? "文字输出" : "用户输入"}</Tag>
                          <Text type="secondary">{formatBeijingTime(log.created_at)}</Text>
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={4} className="fullWidth">
                          <Paragraph ellipsis={{ rows: 2 }}>{log.input_text}</Paragraph>
                          {log.reason && <Text type="danger">{log.reason}</Text>}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )
          }
        ]}
      />
    </Card>
    <Drawer title="图片输出复核" open={Boolean(previewReview)} width={720} onClose={closeImageReview}>
      {previewReview && (
        <Space direction="vertical" size={16} className="fullWidth">
          <Space wrap>
            <Tag color={previewReview.status === "approved" ? "green" : previewReview.status === "pending" ? "orange" : "red"}>
              {previewReview.status === "approved" ? "已通过" : previewReview.status === "pending" ? "待复核" : "已拒绝"}
            </Tag>
            <Text type="secondary">{formatBeijingTime(previewReview.created_at)}</Text>
          </Space>
          {reviewPreviewUrl ? <img className="generatedImage" src={reviewPreviewUrl} alt="待复核图片" /> : <Alert type="warning" showIcon message="图片暂时无法预览" />}
          <div><Text strong>生成提示词</Text><Paragraph>{previewReview.input_text}</Paragraph></div>
          <Alert type="warning" showIcon message="审核说明" description={previewReview.reason} />
          <Space>
            <Button type="primary" loading={reviewingImageId === previewReview.id} onClick={() => reviewImage(previewReview, "approved")}>批准并对学生开放</Button>
            <Popconfirm title="拒绝这张图片？" onConfirm={() => reviewImage(previewReview, "rejected")}>
              <Button danger loading={reviewingImageId === previewReview.id}>拒绝并保持隐藏</Button>
            </Popconfirm>
          </Space>
        </Space>
      )}
    </Drawer>
    </>
  );
}
