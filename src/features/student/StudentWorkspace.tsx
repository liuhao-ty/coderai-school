import {
  Alert, App as AntApp, Button, Card, Col, Drawer, Form, Input, List, Popconfirm, Radio, Row, Segmented, Select, Space, Statistic, Tabs, Tag, Typography
} from "antd";
import {
  BookOpen, ClipboardList, Eye, Image, Library, Play, RotateCcw, Video
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { IconTitle } from "../../components/IconTitle";
import { EmptyState } from "../../components/PageState";
import type { AIGenerationJob, AssetItem, ClassTask, Course, Lesson, Project, ProviderState, SubmissionVersion, TaskSubmission, VideoTask } from "../../domain-types";
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
export type StudentWorkspaceView = "notifications" | "materials" | "text" | "image" | "video";

export function StudentWorkspace({
  view,
  onRefresh,
  provider,
  classTasks,
  projects,
  assets,
  submissions,
  videoTasks,
  studentProfile
}: {
  view: StudentWorkspaceView;
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
  const [imageResultUrl, setImageResultUrl] = useState("");
  const [generationJob, setGenerationJob] = useState<AIGenerationJob | null>(null);
  const [videoNotice, setVideoNotice] = useState<{ type: "success" | "warning" | "error"; message: string } | null>(null);
  const [refreshingVideoId, setRefreshingVideoId] = useState<number | null>(null);
  const [loading, setLoading] = useState("");
  const [studentPreviewAsset, setStudentPreviewAsset] = useState<AssetItem | null>(null);
  const [studentPreviewAssetUrl, setStudentPreviewAssetUrl] = useState("");
  const [studentPreviewLoading, setStudentPreviewLoading] = useState(false);
  const [studentVersionSubmission, setStudentVersionSubmission] = useState<TaskSubmission | null>(null);
  const [studentVersions, setStudentVersions] = useState<SubmissionVersion[]>([]);
  const [studentVersionsLoading, setStudentVersionsLoading] = useState(false);
  const [imageInput, setImageInput] = useState<{ file_name: string; file_path: string; size: number } | null>(null);
  const [videoInput, setVideoInput] = useState<{ file_name: string; file_path: string; size: number } | null>(null);
  const [uploadingInput, setUploadingInput] = useState<"image" | "video" | "">("");
  const [generationError, setGenerationError] = useState<{ tool: "text" | "image" | "video"; message: string; code: string; values: any } | null>(null);
  const completedJobRef = useRef("");
  const imageResultObjectUrlRef = useRef("");
  const [videoDurations, setVideoDurations] = useState<number[]>([5, 10]);
  const allowedTools = useMemo(() => allowedToolsFromTasks(classTasks), [classTasks]);
  const legacyTasks = useMemo(
    () => classTasks.filter((task) => !task.course_schedule_id && task.task_kind !== "schedule"),
    [classTasks],
  );
  const studentSchoolStage = normalizeSchoolStage(studentProfile?.age_level);
  const isPrimaryLowerStudent = studentSchoolStage === "primary_lower";
  const canUseText = allowedTools.has("text");
  const canUseImage = allowedTools.has("image");
  const canUseVideo = allowedTools.has("video");
  const supportsText = provider.configured && (provider.capabilities || []).includes("text");
  const supportsImage = provider.configured && (provider.capabilities || []).includes("image");
  const supportsVideo = provider.configured && (provider.capabilities || []).includes("video");

  const loadJobImage = async (jobId: number) => {
    if (imageResultObjectUrlRef.current) URL.revokeObjectURL(imageResultObjectUrlRef.current);
    const response = await api.get(`/api/ai/jobs/${jobId}/file`, { responseType: "blob" });
    const objectUrl = URL.createObjectURL(response.data);
    imageResultObjectUrlRef.current = objectUrl;
    setImageResultUrl(objectUrl);
  };

  useEffect(() => () => {
    if (imageResultObjectUrlRef.current) URL.revokeObjectURL(imageResultObjectUrlRef.current);
  }, []);

  useEffect(() => {
    if (view !== "text" && view !== "image") return;
    void api.get("/api/ai/jobs").then(async (response) => {
      const job = (response.data.jobs || []).find((item: AIGenerationJob) => item.operation === "generate" && item.capability === view);
      if (!job) return;
      if (job.status === "succeeded") {
        if (view === "text") setTextResult(String(job.result.text || ""));
        if (view === "image") {
          setGenerationJob(job);
          completedJobRef.current = `${job.id}:${job.result.moderation_status || "approved"}:${Boolean(job.result.file_available)}`;
          if (job.result.file_available) await loadJobImage(job.id);
        }
      }
      if (["queued", "running"].includes(job.status)) {
        setGenerationJob(job);
        setLoading(view);
      }
    }).catch(() => undefined);
  }, [view]);

  useEffect(() => {
    if (view !== "video") return;
    void api.get("/api/models/catalog").then((response) => {
      const available = (response.data.models || []).find((item: { capability: string; available: boolean; parameters?: { durations?: number[] } }) => item.capability === "video" && item.available);
      const durations = available?.parameters?.durations?.filter((item: number) => Number.isInteger(item) && item > 0);
      if (durations?.length) setVideoDurations(durations);
    }).catch(() => undefined);
  }, [view]);

  useEffect(() => {
    if (!generationJob) return;
    const waitingForImageReview = generationJob.status === "succeeded"
      && generationJob.capability === "image"
      && generationJob.result.moderation_status === "pending";
    if (["queued", "running"].includes(generationJob.status) || waitingForImageReview) {
      if (waitingForImageReview) setLoading("");
      const timer = window.setInterval(() => {
        void api.get(`/api/ai/jobs/${generationJob.id}`).then((response) => {
          setGenerationJob(response.data.job);
        }).catch(() => undefined);
      }, waitingForImageReview ? 4_000 : 1_800);
      return () => window.clearInterval(timer);
    }
    const completionKey = `${generationJob.id}:${generationJob.status}:${generationJob.result.moderation_status || "approved"}:${Boolean(generationJob.result.file_available)}`;
    if (completedJobRef.current === completionKey) return;
    completedJobRef.current = completionKey;
    setLoading("");
    if (generationJob.status === "succeeded") {
      setGenerationError(null);
      if (generationJob.capability === "text") setTextResult(String(generationJob.result.text || ""));
      if (generationJob.capability === "image") {
        if (generationJob.result.file_available) {
          void loadJobImage(generationJob.id).catch((error) => message.error(explainError(error)));
          message.success("图片已通过教师审批，可以预览");
        } else if (generationJob.result.moderation_status === "rejected") {
          message.warning("图片未通过教师审批，不能预览");
        }
      } else {
        message.success("文字作品已保存到作品库");
      }
      void onRefresh();
    } else {
      const tool = generationJob.capability === "image" ? "image" : "text";
      setGenerationError({
        tool,
        message: generationJob.error_message || "生成任务失败",
        code: generationJob.error_code || generationJob.status,
        values: {},
      });
    }
  }, [generationJob, message, onRefresh]);

  const runText = async (values: { prompt: string; mode: string; age_level: string }) => {
    setLoading("text");
    setGenerationError(null);
    setTextResult("");
    try {
      const res = await api.post("/api/ai/jobs", {
        client_request_id: crypto.randomUUID(),
        capability: "text",
        prompt: values.prompt,
        mode: values.mode || "general",
        save_project: true,
      });
      completedJobRef.current = "";
      setGenerationJob(res.data.job);
      message.info("文字任务已提交，离开页面后仍会继续执行");
    } catch (error) {
      setGenerationError({ tool: "text", message: explainError(error), code: errorCode(error), values });
      message.error(explainError(error));
      setLoading("");
    }
  };

  const runImage = async (values: { prompt: string; style: string; size: string }) => {
    setLoading("image");
    setGenerationError(null);
    if (imageResultObjectUrlRef.current) URL.revokeObjectURL(imageResultObjectUrlRef.current);
    imageResultObjectUrlRef.current = "";
    setImageResultUrl("");
    try {
      const res = await api.post("/api/ai/jobs", {
        client_request_id: crypto.randomUUID(),
        capability: "image",
        prompt: values.prompt,
        style: values.style,
        size: values.size,
        source_image_path: imageInput?.file_path || null,
        save_project: true,
      });
      completedJobRef.current = "";
      setGenerationJob(res.data.job);
      message.info("图片任务已提交，离开页面后仍会继续执行");
    } catch (error) {
      setGenerationError({ tool: "image", message: explainError(error), code: errorCode(error), values });
      message.error(explainError(error));
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
    if (generationJob && ["failed", "timed_out", "canceled"].includes(generationJob.status)) {
      setLoading(generationJob.capability);
      setGenerationError(null);
      void api.post(`/api/ai/jobs/${generationJob.id}/retry`).then((response) => {
        completedJobRef.current = "";
        setGenerationJob(response.data.job);
      }).catch((error) => {
        setLoading("");
        message.error(explainError(error));
      });
      return;
    }
    if (!generationError) return;
    const { tool, values } = generationError;
    if (tool === "text") void runText(values);
    if (tool === "image") void runImage(values);
    if (tool === "video") void runVideo(values);
  };

  const cancelGeneration = async () => {
    if (!generationJob) return;
    try {
      const response = await api.post(`/api/ai/jobs/${generationJob.id}/cancel`);
      setGenerationJob(response.data.job);
      message.info("已请求取消生成任务");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const refreshGenerationJob = async () => {
    if (!generationJob) return;
    try {
      const response = await api.get(`/api/ai/jobs/${generationJob.id}`);
      setGenerationJob(response.data.job);
    } catch (error) {
      message.error(explainError(error));
    }
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

  const viewMeta: Record<StudentWorkspaceView, { title: string; description: string }> = {
    notifications: { title: "课堂通知", description: "查看课堂安排、截止时间和已有作业反馈。" },
    materials: { title: "课堂素材", description: "查看教师发布的课堂图片、视频和学习资料。" },
    text: { title: "文字生成", description: "使用课堂开放的文字模型完成创作并保存到作品库。" },
    image: { title: "图片生成", description: "使用文字或参考图片生成作品并保存到作品库。" },
    video: { title: "视频生成", description: "创建视频任务并查看生成、重试和保存状态。" },
  };
  const isGenerationView = view === "text" || view === "image" || view === "video";
  const currentToolAllowed = view === "text"
    ? canUseText
    : view === "image"
      ? canUseImage
      : view === "video"
        ? canUseVideo
        : true;

  return (
    <div className="page">
      <div className="pageTitle">
        <Title level={2}>{viewMeta[view].title}</Title>
        <Text>
          {studentProfile
            ? `${viewMeta[view].description} ${studentProfile.name} · ${studentProfile.classroom_name || "未分配班级"} · ${studentProfile.school_stage_label || schoolStageLabel(studentProfile.age_level)}`
            : viewMeta[view].description}
        </Text>
      </div>
      {isGenerationView && !provider.configured && (
        <Alert
          type="warning"
          showIcon
          message="教师还没有配置云端AI API密钥"
          description="当前界面可浏览，但生成任务会提示先完成教师设置。"
        />
      )}
      {view === "notifications" && (
        <Card className="mb16" title={<IconTitle icon={<ClipboardList size={18} />} text="课堂通知" />}>
          <List
            dataSource={legacyTasks}
            pagination={legacyTasks.length > 5 ? { pageSize: 5, showSizeChanger: false } : false}
            locale={{ emptyText: <EmptyState title="暂无课堂通知" description="教师发布新的课堂安排后会显示在这里" /> }}
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
      {view === "materials" && (
        <Card className="mb16" title={<IconTitle icon={<Library size={18} />} text="课堂素材" />}>
          <List
            size="small"
            dataSource={assets}
            pagination={assets.length > 6 ? { pageSize: 6, showSizeChanger: false } : false}
            locale={{ emptyText: <EmptyState title="暂无课堂素材" description="教师发布素材后会显示在这里" /> }}
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
      {isGenerationView && <Alert
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
              : "初中高中：已开放完整创作参数，生成结果会使用更准确的技术术语和验证方法。"
        }
      />}
      {isGenerationView && generationError && (
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
      {(view === "text" || view === "image") && generationJob && ["queued", "running"].includes(generationJob.status) && (
        <Alert
          className="mb16"
          type="info"
          showIcon
          message={generationJob.status === "queued" ? "生成任务正在排队" : "模型正在生成"}
          description="任务已由云端接管，刷新页面或暂时离开不会中断。"
          action={<Button onClick={() => void cancelGeneration()}>取消任务</Button>}
        />
      )}
      {view === "text" && canUseText && <PluginToolsPanel supportsText={supportsText} onRefresh={onRefresh} />}
      <Row gutter={[16, 16]}>
        {view === "text" && canUseText && (
        <Col span={24}>
          <Card title={<IconTitle icon={<BookOpen size={18} />} text="AI文字生成" />}>
            {!supportsText && <Alert className="mb16" type="warning" showIcon message="当前 AI 服务未提供文字生成能力" />}
            <Form layout="vertical" onFinish={runText} initialValues={{ mode: "general", age_level: studentSchoolStage }}>
              {!isPrimaryLowerStudent && (
                <Form.Item name="mode" label="任务类型">
                  <Select
                    options={[
                      { value: "general", label: "常规生成" },
                      { value: "story", label: "故事创作" },
                      { value: "polish", label: "作文润色" },
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
            {textResult && (
              <article className="markdownPreview resultMarkdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{textResult}</ReactMarkdown>
              </article>
            )}
          </Card>
        </Col>
        )}
        {view === "image" && canUseImage && (
        <Col span={24}>
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
            {generationJob?.capability === "image" && generationJob.status === "succeeded" && generationJob.result.moderation_status === "pending" && (
              <Alert
                className="mt16"
                type="info"
                showIcon
                message="等待教师审批"
                description="图片已经生成，教师审批通过后才会在这里显示预览。"
                action={<Button size="small" icon={<RotateCcw size={14} />} onClick={() => void refreshGenerationJob()}>刷新状态</Button>}
              />
            )}
            {generationJob?.capability === "image" && generationJob.status === "succeeded" && generationJob.result.moderation_status === "rejected" && (
              <Alert className="mt16" type="error" showIcon message="图片未通过教师审批" description={generationJob.result.moderation_reason || "请调整描述后重新生成。"} />
            )}
            {imageResultUrl && <img className="generatedImage" src={imageResultUrl} alt="AI生成结果" />}
          </Card>
        </Col>
        )}
        {view === "video" && canUseVideo && (
        <Col span={24}>
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
                      options={videoDurations.map((duration) => ({ value: duration, label: `${duration} 秒` }))}
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
        {isGenerationView && !currentToolAllowed && (
          <Col span={24}>
            <Alert type="warning" showIcon message="当前课堂没有开放这个生成工具，请查看课堂通知或联系教师。" />
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
