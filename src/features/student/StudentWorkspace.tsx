import {
  Alert, App as AntApp, Button, Card, Col, Drawer, Form, Input, List, Popconfirm, Radio, Row, Segmented, Select, Space, Statistic, Tabs, Tag, Typography
} from "antd";
import {
  BookOpen, Bot, ClipboardList, Eye, FileUp, Image, Library, Play, RotateCcw, Save, Video
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type { AssetItem, ClassTask, Course, Lesson, Project, ProviderState, SubmissionVersion, TaskSubmission, VideoTask } from "../../domain-types";
import { api } from "../../lib/api";
import { allowedToolsFromTasks, assetName, assetTypeLabel, projectTypeLabel, submissionForTask, submissionStatusColor, submissionStatusLabel, toolScopeLabel, videoTaskStatusColor, videoTaskStatusLabel } from "../../lib/domain";
import { errorCode, explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { normalizeSchoolStage, schoolStageLabel } from "../../lib/schoolStages";
import type { StudentProfile } from "../../types";
import { AssetMediaPreview } from "../courses/CourseTaskManager";
import { ProjectFileStatus } from "../projects/ProjectLibrary";
import { SubmissionVersionsDrawer } from "../submissions/SubmissionReviewPanel";
import { PluginToolsPanel } from "./PluginToolsPanel";


const { Title, Text, Paragraph } = Typography;

export function StudentWorkspace({
  onRefresh,
  provider,
  classTasks,
  projects,
  assets,
  submissions,
  videoTasks,
  studentProfile
}: {
  onRefresh: () => Promise<void>;
  provider: ProviderState;
  classTasks: ClassTask[];
  projects: Project[];
  assets: AssetItem[];
  submissions: TaskSubmission[];
  videoTasks: VideoTask[];
  studentProfile: StudentProfile | null;
}) {
  const { message } = AntApp.useApp();
  const [textResult, setTextResult] = useState("");
  const [codeResult, setCodeResult] = useState("");
  const [imageResult, setImageResult] = useState<{
    url?: string;
    file_path?: string;
    moderation_status?: "approved" | "pending" | "rejected";
    moderation_reason?: string;
    moderation_message?: string;
  } | null>(null);
  const [videoNotice, setVideoNotice] = useState<{ type: "success" | "warning" | "error"; message: string } | null>(null);
  const [refreshingVideoId, setRefreshingVideoId] = useState<number | null>(null);
  const [loading, setLoading] = useState("");
  const [submittingTaskId, setSubmittingTaskId] = useState<number | null>(null);
  const [taskProjectMap, setTaskProjectMap] = useState<Record<number, number>>({});
  const [studentPreviewAsset, setStudentPreviewAsset] = useState<AssetItem | null>(null);
  const [studentPreviewAssetUrl, setStudentPreviewAssetUrl] = useState("");
  const [studentPreviewLoading, setStudentPreviewLoading] = useState(false);
  const [studentVersionSubmission, setStudentVersionSubmission] = useState<TaskSubmission | null>(null);
  const [studentVersions, setStudentVersions] = useState<SubmissionVersion[]>([]);
  const [studentVersionsLoading, setStudentVersionsLoading] = useState(false);
  const [imageInput, setImageInput] = useState<{ file_name: string; file_path: string; size: number } | null>(null);
  const [videoInput, setVideoInput] = useState<{ file_name: string; file_path: string; size: number } | null>(null);
  const [uploadingInput, setUploadingInput] = useState<"image" | "video" | "">("");
  const [generationError, setGenerationError] = useState<{ tool: "text" | "image" | "video" | "code"; message: string; code: string; values: any } | null>(null);
  const allowedTools = useMemo(() => allowedToolsFromTasks(classTasks), [classTasks]);
  const studentSchoolStage = normalizeSchoolStage(studentProfile?.age_level);
  const isPrimaryLowerStudent = studentSchoolStage === "primary_lower";
  const canUseText = allowedTools.has("text");
  const canUseImage = allowedTools.has("image");
  const canUseVideo = allowedTools.has("video");
  const supportsText = provider.configured && (provider.capabilities || []).includes("text");
  const supportsImage = provider.configured && (provider.capabilities || []).includes("image");
  const supportsVideo = provider.configured && (provider.capabilities || []).includes("video");

  const runText = async (values: { prompt: string; mode: string; age_level: string }) => {
    setLoading("text");
    setGenerationError(null);
    setTextResult("");
    try {
      const res = await api.post("/api/text/generate", {
        ...values,
        age_level: studentProfile?.age_level || values.age_level || "primary_lower"
      });
      setTextResult(res.data.text);
      await onRefresh();
      message.success("文字作品已保存到作品库");
    } catch (error) {
      setGenerationError({ tool: "text", message: explainError(error), code: errorCode(error), values });
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const runImage = async (values: { prompt: string; style: string; size: string }) => {
    setLoading("image");
    setGenerationError(null);
    setImageResult(null);
    try {
      const request = { ...values, source_image_path: imageInput?.file_path || null };
      const res = await api.post("/api/image/generate", request);
      setImageResult(res.data);
      await onRefresh();
      if (res.data.moderation_status === "approved") message.success("图片审核通过，已保存到作品库");
      else message.warning(res.data.moderation_message || "图片已进入教师复核队列");
    } catch (error) {
      setGenerationError({ tool: "image", message: explainError(error), code: errorCode(error), values });
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const runCodeAssistant = async (values: { prompt: string }) => {
    setLoading("code");
    setGenerationError(null);
    setCodeResult("");
    try {
      const res = await api.post("/api/text/generate", {
        prompt: values.prompt,
        mode: "code_explain",
        age_level: studentSchoolStage
      });
      setCodeResult(res.data.text);
      await onRefresh();
      message.success("编程助手结果已保存到作品库");
    } catch (error) {
      setGenerationError({ tool: "code", message: explainError(error), code: errorCode(error), values });
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const runVideo = async (values: { prompt: string; source_image_path?: string; duration_seconds: number }) => {
    setLoading("video");
    setGenerationError(null);
    setVideoNotice(null);
    try {
      const request = { ...values, source_image_path: videoInput?.file_path || values.source_image_path || null };
      const res = await api.post("/api/video/generate", request);
      setVideoNotice({ type: "success", message: res.data?.message || "视频生成任务已提交，可稍后刷新任务状态。" });
      await onRefresh();
    } catch (error) {
      const detail = explainError(error);
      setGenerationError({ tool: "video", message: detail, code: errorCode(error), values });
      setVideoNotice({ type: "warning", message: detail });
      message.warning(detail);
    } finally {
      setLoading("");
    }
  };

  const uploadReferenceImage = async (file: File, target: "image" | "video") => {
    setUploadingInput(target);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api.post("/api/ai-inputs/upload", formData, { headers: { "Content-Type": "multipart/form-data" } });
      if (target === "image") setImageInput(res.data.input);
      else setVideoInput(res.data.input);
      message.success("参考图片已上传");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUploadingInput("");
    }
  };

  const retryGeneration = () => {
    if (!generationError) return;
    const { tool, values } = generationError;
    if (tool === "text") void runText(values);
    if (tool === "image") void runImage(values);
    if (tool === "video") void runVideo(values);
    if (tool === "code") void runCodeAssistant(values);
  };

  const refreshVideoTask = async (taskId: number) => {
    setRefreshingVideoId(taskId);
    try {
      await api.post(`/api/video/tasks/${taskId}/refresh`);
      await onRefresh();
      message.success("视频任务状态已刷新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setRefreshingVideoId(null);
    }
  };

  const retryVideoTask = async (taskId: number) => {
    setRefreshingVideoId(taskId);
    try {
      const response = await api.post(`/api/video/tasks/${taskId}/retry`);
      await onRefresh();
      message.success(response.data?.task?.status === "success" ? "视频结果已恢复并保存到作品库" : "视频任务已重新提交");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setRefreshingVideoId(null);
    }
  };

  const cancelVideoTask = async (taskId: number) => {
    setRefreshingVideoId(taskId);
    try {
      await api.post(`/api/video/tasks/${taskId}/cancel`);
      await onRefresh();
      message.success("视频任务已在本地取消");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setRefreshingVideoId(null);
    }
  };

  const submitTask = async (taskId: number) => {
    const projectId = taskProjectMap[taskId];
    if (!projectId) {
      message.warning("请先选择要提交的作品");
      return;
    }
    setSubmittingTaskId(taskId);
    try {
      await api.post(`/api/classes/tasks/${taskId}/submissions`, { project_id: projectId });
      await onRefresh();
      message.success("作业已提交");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSubmittingTaskId(null);
    }
  };

  const openStudentVersions = async (submission: TaskSubmission) => {
    setStudentVersionSubmission(submission);
    setStudentVersionsLoading(true);
    try {
      const res = await api.get(`/api/submissions/${submission.id}/versions`);
      setStudentVersions(res.data.versions || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setStudentVersionsLoading(false);
    }
  };

  const openStudentAsset = async (asset: AssetItem) => {
    if (studentPreviewAssetUrl && !studentPreviewAssetUrl.startsWith("http")) URL.revokeObjectURL(studentPreviewAssetUrl);
    setStudentPreviewAsset(asset);
    setStudentPreviewAssetUrl("");
    if (asset.file_path.startsWith("http")) {
      setStudentPreviewAssetUrl(asset.file_path);
      return;
    }
    setStudentPreviewLoading(true);
    try {
      const res = await api.get(`/api/assets/${asset.id}/file`, { responseType: "blob" });
      setStudentPreviewAssetUrl(URL.createObjectURL(res.data));
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setStudentPreviewLoading(false);
    }
  };

  const closeStudentAsset = () => {
    if (studentPreviewAssetUrl && !studentPreviewAssetUrl.startsWith("http")) URL.revokeObjectURL(studentPreviewAssetUrl);
    setStudentPreviewAssetUrl("");
    setStudentPreviewAsset(null);
  };

  return (
    <div className="page">
      <div className="pageTitle">
        <Title level={2}>学生AI创作工作台</Title>
        <Text>
          {studentProfile
            ? `${studentProfile.name} · ${studentProfile.classroom_name || "未分配班级"} · ${studentProfile.school_stage_label || schoolStageLabel(studentProfile.age_level)}`
            : "文字、图片和编程创意都从这里开始。"}
        </Text>
      </div>
      {!provider.configured && (
        <Alert
          type="warning"
          showIcon
          message="教师还没有配置云端AI API密钥"
          description="当前界面可浏览，但生成任务会提示先完成教师设置。"
        />
      )}
      {classTasks.length > 0 && (
        <Card className="mb16" title={<IconTitle icon={<ClipboardList size={18} />} text="课堂任务" />}>
          <List
            dataSource={classTasks}
            pagination={classTasks.length > 5 ? { pageSize: 5, showSizeChanger: false } : false}
            renderItem={(task) => (
              <List.Item>
                <List.Item.Meta
                  title={task.title}
                  description={
                    <Space direction="vertical" size={6}>
                      <Paragraph className="taskDescription" ellipsis={{ rows: 2 }}>
                        {task.instructions || "教师暂未填写任务说明。"}
                      </Paragraph>
                      <Space wrap>
                        {task.lesson_title && <Tag color="cyan">{task.course_title ? `${task.course_title} · ` : ""}{task.lesson_title}</Tag>}
                        {task.tool_scope.split(",").map((scope) => (
                          <Tag key={scope}>{toolScopeLabel(scope)}</Tag>
                        ))}
                        {submissionForTask(submissions, task.id) && (
                          <Tag color={submissionStatusColor(submissionForTask(submissions, task.id)?.status || "")}>
                            {submissionStatusLabel(submissionForTask(submissions, task.id)?.status || "")}
                          </Tag>
                        )}
                        {submissionForTask(submissions, task.id)?.is_late && <Tag color="red">逾期提交</Tag>}
                        {submissionForTask(submissions, task.id)?.is_featured && <Tag color="gold">优秀作品</Tag>}
                        {task.due_at && <Tag color="gold">截止 {formatBeijingTime(task.due_at)}</Tag>}
                        <Text type="secondary">{formatBeijingTime(task.created_at)}</Text>
                      </Space>
                      <Space wrap>
                        <Select
                          className="taskSubmitSelect"
                          placeholder={projects.length ? "选择作品提交" : "先在作品库保存作品"}
                          value={taskProjectMap[task.id]}
                          disabled={!projects.length}
                          onChange={(value) => setTaskProjectMap((current) => ({ ...current, [task.id]: value }))}
                          options={projects.map((project) => ({ value: project.id, label: project.title }))}
                        />
                        <Button
                          size="small"
                          type="primary"
                          onClick={() => submitTask(task.id)}
                          loading={submittingTaskId === task.id}
                          disabled={!projects.length}
                        >
                          提交作品
                        </Button>
                      </Space>
                      {submissionForTask(submissions, task.id)?.feedback && (
                        <Text type="secondary">教师反馈：{submissionForTask(submissions, task.id)?.feedback}</Text>
                      )}
                      {submissionForTask(submissions, task.id) && (
                        <Button size="small" icon={<Eye size={15} />} onClick={() => openStudentVersions(submissionForTask(submissions, task.id)!)}>
                          提交历史（{submissionForTask(submissions, task.id)?.version_count || 1}）
                        </Button>
                      )}
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}
      {assets.length > 0 && (
        <Card className="mb16" title={<IconTitle icon={<Library size={18} />} text="课堂素材" />}>
          <List
            size="small"
            dataSource={assets}
            pagination={assets.length > 6 ? { pageSize: 6, showSizeChanger: false } : false}
            renderItem={(asset) => (
              <List.Item actions={[<Button key="preview" size="small" icon={<Eye size={15} />} onClick={() => openStudentAsset(asset)}>预览</Button>]}> 
                <Space direction="vertical" size={4} className="fullWidth">
                  <Space wrap>
                    <Tag>{assetTypeLabel(asset.asset_type)}</Tag>
                    {asset.classroom_name && <Tag color="blue">{asset.classroom_name}</Tag>}
                    {asset.lesson_title && <Tag color="cyan">{asset.course_title ? `${asset.course_title} · ` : ""}{asset.lesson_title}</Tag>}
                    <Text>{assetName(asset)}</Text>
                    <ProjectFileStatus project={{ file_status: asset.file_status, file_path: asset.file_path } as Project} compact />
                  </Space>
                  {asset.file_path.startsWith("http") ? (
                    <a href={asset.file_path} target="_blank" rel="noreferrer">
                      打开素材
                    </a>
                  ) : (
                    <Text copyable={{ text: asset.file_path }} type="secondary">
                      {asset.file_path}
                    </Text>
                  )}
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
      <Alert
        className="mb16"
        type="info"
        showIcon
        message={
          classTasks.length
            ? `当前课堂开放工具：${Array.from(allowedTools).map(toolScopeLabel).join("、")}`
            : "当前没有指定课堂工具限制，学生可使用基础创作入口。"
        }
        description={
          isPrimaryLowerStudent
            ? "小学低龄：界面保留基础创作项，并隐藏任务类型、图片尺寸和参考图等高级参数。"
            : studentSchoolStage === "primary_upper"
              ? "小学高龄：已开放任务类型、参考图和更多创作参数。"
              : "初中高中：已开放完整创作参数，文字助手会使用更准确的技术术语和验证方法。"
        }
      />
      {generationError && (
        <Alert
          className="mb16"
          type="error"
          showIcon
          message={generationError.message}
          description={`错误代码：${generationError.code}`}
          action={<Button danger onClick={retryGeneration} loading={Boolean(loading)}>重试</Button>}
          closable
          onClose={() => setGenerationError(null)}
        />
      )}
      <PluginToolsPanel supportsText={supportsText} onRefresh={onRefresh} />
      <Row gutter={[16, 16]}>
        {canUseText && (
        <Col xs={24} xl={12}>
          <Card title={<IconTitle icon={<BookOpen size={18} />} text="AI文字生成" />}>
            {!supportsText && <Alert className="mb16" type="warning" showIcon message="当前 AI 服务未提供文字生成能力" />}
            <Form layout="vertical" onFinish={runText} initialValues={{ mode: "story", age_level: studentSchoolStage }}>
              {!isPrimaryLowerStudent && (
                <Form.Item name="mode" label="任务类型">
                  <Select
                    options={[
                      { value: "story", label: "故事创作" },
                      { value: "polish", label: "作文润色" },
                      { value: "code_explain", label: "代码解释" },
                      { value: "prompt_refine", label: "提示词改写" }
                    ]}
                  />
                </Form.Item>
              )}
              <Form.Item name="prompt" label="输入内容" rules={[{ required: true, message: "请输入创作内容" }]}>
                <Input.TextArea rows={5} placeholder="例如：帮我写一个关于太空机器人的Scratch项目故事" />
              </Form.Item>
              <Button icon={<Play size={16} />} type="primary" htmlType="submit" loading={loading === "text"} disabled={!supportsText}>
                生成并保存
              </Button>
            </Form>
            {textResult && <pre className="resultText">{textResult}</pre>}
          </Card>
        </Col>
        )}
        {canUseText && (
        <Col xs={24} xl={12}>
          <Card title={<IconTitle icon={<Bot size={18} />} text="编程助手" />}>
            {!supportsText && <Alert className="mb16" type="warning" showIcon message="编程助手需要文字模型，请联系教师调整 AI 服务" />}
            <Form layout="vertical" onFinish={runCodeAssistant}>
              <Form.Item name="prompt" label="代码或问题" rules={[{ required: true, message: "请输入代码或编程问题" }]}>
                <Input.TextArea
                  rows={7}
                  placeholder={"粘贴 Scratch 思路、Python/JavaScript 代码，或描述遇到的报错。\n例如：帮我解释这段循环为什么只运行了一次。"}
                />
              </Form.Item>
              <Button icon={<Bot size={16} />} type="primary" htmlType="submit" loading={loading === "code"} disabled={!supportsText}>
                获取帮助
              </Button>
            </Form>
            {codeResult && <pre className="resultText">{codeResult}</pre>}
          </Card>
        </Col>
        )}
        {canUseImage && (
        <Col xs={24} xl={12}>
          <Card title={<IconTitle icon={<Image size={18} />} text="AI图片生成" />}>
            {!supportsImage && <Alert className="mb16" type="warning" showIcon message="当前 AI 服务未提供图片生成能力" />}
            <Form layout="vertical" onFinish={runImage} initialValues={{ style: "明亮卡通", size: "1024x1024" }}>
              {!isPrimaryLowerStudent && (
                <>
                  <Form.Item name="style" label="图片风格">
                    <Select
                      options={[
                        { value: "明亮卡通", label: "明亮卡通" },
                        { value: "科技课堂", label: "科技课堂" },
                        { value: "项目封面", label: "项目封面" }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="size" label="尺寸">
                    <Select options={[{ value: "1024x1024", label: "1024 x 1024" }]} />
                  </Form.Item>
                </>
              )}
              <Form.Item name="prompt" label="图片描述" rules={[{ required: true, message: "请输入图片描述" }]}>
                <Input.TextArea rows={5} placeholder="例如：一个小学生正在设计火星探测机器人" />
              </Form.Item>
              {!isPrimaryLowerStudent && (
                <Form.Item label="参考图片（图生图）">
                  <Space direction="vertical" size={8} className="fullWidth">
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      disabled={uploadingInput === "image"}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) void uploadReferenceImage(file, "image");
                        event.currentTarget.value = "";
                      }}
                    />
                    {imageInput && <Space wrap><Tag color="blue">{imageInput.file_name} · {(imageInput.size / 1024).toFixed(0)} KB</Tag><Button size="small" onClick={() => setImageInput(null)}>移除</Button></Space>}
                  </Space>
                </Form.Item>
              )}
              <Button icon={<Image size={16} />} type="primary" htmlType="submit" loading={loading === "image"} disabled={!supportsImage}>
                生成并保存
              </Button>
            </Form>
            {imageResult?.url && <img className="generatedImage" src={imageResult.url} alt="AI生成结果" />}
            {imageResult?.file_path && <Alert className="mt16" type="success" message={`已保存：${imageResult.file_path}`} />}
            {imageResult?.moderation_status && imageResult.moderation_status !== "approved" && (
              <Alert
                className="mt16"
                type="warning"
                showIcon
                message={imageResult.moderation_status === "pending" ? "图片等待教师复核" : "图片被自动审核拦截"}
                description={imageResult.moderation_message || imageResult.moderation_reason}
              />
            )}
          </Card>
        </Col>
        )}
        {canUseVideo && (
        <Col xs={24} xl={12}>
          <Card title={<IconTitle icon={<Video size={18} />} text="AI视频生成" />}>
            {!supportsVideo && <Alert className="mb16" type="warning" showIcon message="当前 AI 服务未提供视频生成能力" />}
            <Form layout="vertical" onFinish={runVideo} initialValues={{ duration_seconds: 5 }}>
              <Form.Item name="prompt" label="视频描述 / 分镜" rules={[{ required: true, message: "请输入视频描述" }]}>
                <Input.TextArea rows={5} placeholder="例如：一个机器人从草图变成会移动的课堂助手，镜头明亮清晰" />
              </Form.Item>
              {!isPrimaryLowerStudent && (
                <>
                  <Form.Item label="上传参考图片（图生视频）">
                    <Space direction="vertical" size={8} className="fullWidth">
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        disabled={uploadingInput === "video"}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) void uploadReferenceImage(file, "video");
                          event.currentTarget.value = "";
                        }}
                      />
                      {videoInput && <Space wrap><Tag color="blue">{videoInput.file_name} · {(videoInput.size / 1024).toFixed(0)} KB</Tag><Button size="small" onClick={() => setVideoInput(null)}>移除</Button></Space>}
                    </Space>
                  </Form.Item>
                  <Form.Item name="source_image_path" label="或选择云端图片作品">
                    <Select
                      allowClear
                      placeholder="可选：选择带云端链接的图片作品"
                      options={projects
                        .filter((project) => project.project_type === "image" && project.file_path.startsWith("http"))
                        .map((project) => ({ value: project.file_path, label: project.title }))}
                    />
                  </Form.Item>
                  <Form.Item name="duration_seconds" label="时长">
                    <Select
                      options={[
                        { value: 5, label: "5 秒" },
                        { value: 8, label: "8 秒" },
                        { value: 10, label: "10 秒" }
                      ]}
                    />
                  </Form.Item>
                </>
              )}
              <Button icon={<Video size={16} />} type="primary" htmlType="submit" loading={loading === "video"} disabled={!supportsVideo}>
                生成视频
              </Button>
            </Form>
            {videoNotice && <Alert className="mt16" type={videoNotice.type} showIcon message={videoNotice.message} />}
            <List
              className="mt16"
              size="small"
              dataSource={videoTasks.slice(0, 5)}
              locale={{ emptyText: <EmptyState title="暂无视频任务" description="提交视频生成后可在这里查看进度" /> }}
              renderItem={(task) => {
                const active = ["submitted", "processing"].includes(task.status);
                const shouldRefresh = active || task.status === "download_failed" || (task.status === "success" && !task.file_available);
                const actions = [];
                if (shouldRefresh) {
                  actions.push(
                    <Button key="refresh" size="small" loading={refreshingVideoId === task.id} onClick={() => refreshVideoTask(task.id)}>
                      刷新状态
                    </Button>
                  );
                }
                if (task.can_retry) {
                  actions.push(
                    <Popconfirm
                      key="retry"
                      title="确认重试视频任务？"
                      description="重试可能再次调用云端服务并产生费用。"
                      okText="确认重试"
                      cancelText="取消"
                      onConfirm={() => retryVideoTask(task.id)}
                    >
                      <Button icon={<RotateCcw size={14} />} size="small" loading={refreshingVideoId === task.id}>重试</Button>
                    </Popconfirm>
                  );
                }
                if (active) {
                  actions.push(
                    <Button key="cancel" danger size="small" loading={refreshingVideoId === task.id} onClick={() => cancelVideoTask(task.id)}>
                      取消
                    </Button>
                  );
                }
                return (
                  <List.Item actions={actions}>
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <Text strong>{task.prompt}</Text>
                          <Tag color={videoTaskStatusColor(task.status)}>{videoTaskStatusLabel(task.status)}</Tag>
                          {task.file_available && <Tag color="green">已保存到作品库</Tag>}
                        </Space>
                      }
                      description={
                        <Space direction="vertical" size={6}>
                          <Space wrap>
                            <Text type="secondary">提交时间：{formatBeijingTime(task.created_at)}</Text>
                            <Text type="secondary">重试：{task.retry_count}/{task.max_retries}</Text>
                            {active && task.timeout_at && <Text type="secondary">超时：{formatBeijingTime(task.timeout_at)}</Text>}
                            {task.last_checked_at && <Text type="secondary">最近检查：{formatBeijingTime(task.last_checked_at)}</Text>}
                          </Space>
                          {task.download_url && task.status !== "expired" && (
                            <a href={task.download_url} target="_blank" rel="noreferrer">
                              打开生成视频
                            </a>
                          )}
                          {task.error_message && <Text type="danger">{task.error_message}</Text>}
                        </Space>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          </Card>
        </Col>
        )}
        {!canUseText && !canUseImage && !canUseVideo && (
          <Col span={24}>
            <Alert type="warning" showIcon message="当前课堂任务没有开放生成工具，请查看任务说明或联系教师。" />
          </Col>
        )}
      </Row>
      <SubmissionVersionsDrawer
        submission={studentVersionSubmission}
        versions={studentVersions}
        loading={studentVersionsLoading}
        onClose={() => setStudentVersionSubmission(null)}
      />
      <Drawer title={studentPreviewAsset ? assetName(studentPreviewAsset) : "课堂素材预览"} open={Boolean(studentPreviewAsset)} width={760} onClose={closeStudentAsset}>
        {studentPreviewAsset && (
          <Space direction="vertical" size={16} className="fullWidth">
            <Space wrap>
              <Tag>{assetTypeLabel(studentPreviewAsset.asset_type)}</Tag>
              <ProjectFileStatus project={{ file_status: studentPreviewAsset.file_status, file_path: studentPreviewAsset.file_path } as Project} compact />
            </Space>
            {studentPreviewLoading && <Alert type="info" showIcon message="正在加载素材" />}
            {!studentPreviewLoading && studentPreviewAssetUrl && <AssetMediaPreview asset={studentPreviewAsset} url={studentPreviewAssetUrl} />}
            {!studentPreviewLoading && !studentPreviewAssetUrl && <Alert type="warning" showIcon message="这个素材暂时无法预览" />}
          </Space>
        )}
      </Drawer>
    </div>
  );
}

export function StudentCourses({ courses, lessons }: { courses: Course[]; lessons: Lesson[] }) {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(courses[0]?.id ?? null);
  const [selectedLessonId, setSelectedLessonId] = useState<number | null>(null);
  const courseLessons = lessons.filter((lesson) => lesson.course_id === selectedCourseId);
  const selectedLesson = courseLessons.find((lesson) => lesson.id === selectedLessonId) || courseLessons[0];

  useEffect(() => {
    if (!selectedCourseId && courses[0]) setSelectedCourseId(courses[0].id);
  }, [courses, selectedCourseId]);

  useEffect(() => {
    setSelectedLessonId(courseLessons[0]?.id ?? null);
  }, [selectedCourseId]);

  return (
    <div className="page">
      <div className="pageTitle">
        <Title level={2}>课程学习</Title>
        <Text>按课程和课时阅读老师发布的学习内容。</Text>
      </div>
      {!courses.length ? (
        <EmptyState title="老师还没有发布课程" description="课程发布后会显示在这里" />
      ) : (
        <div className="courseReader">
          <Card className="courseCatalog" title="课程目录">
            <Select
              className="fullWidth mb16"
              value={selectedCourseId}
              onChange={setSelectedCourseId}
              options={courses.map((course) => ({ value: course.id, label: course.title }))}
            />
            <List
              dataSource={courseLessons}
              locale={{ emptyText: <EmptyState title="这门课程还没有课时" description="老师添加课时后可开始学习" /> }}
              renderItem={(lesson, index) => (
                <List.Item
                  className={selectedLesson?.id === lesson.id ? "activeLesson" : ""}
                  onClick={() => setSelectedLessonId(lesson.id)}
                >
                  <Space><Tag>{index + 1}</Tag><Text strong={selectedLesson?.id === lesson.id}>{lesson.title}</Text></Space>
                </List.Item>
              )}
            />
          </Card>
          <Card className="lessonReader">
            {selectedLesson ? (
              <>
                <Space wrap className="mb16">
                  <Tag color="blue">第 {courseLessons.findIndex((item) => item.id === selectedLesson.id) + 1} 课</Tag>
                  <Text type="secondary">{selectedLesson.course_title}</Text>
                </Space>
                <Title level={3}>{selectedLesson.title}</Title>
                <article className="markdownPreview lessonContent">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedLesson.content || "老师暂未填写本课内容。"}</ReactMarkdown>
                </article>
              </>
            ) : <Alert type="info" showIcon message="请选择一个课时" />}
          </Card>
        </div>
      )}
    </div>
  );
}
