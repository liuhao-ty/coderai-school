import { Alert, App, Button, Checkbox, Collapse, Drawer, Empty, Input, List, Pagination, Radio, Segmented, Select, Space, Tag, Typography, Upload } from "antd";
import { BookOpen, CheckCircle2, Clock3, Download, Edit3, Eye, FileText, FileUp, Save, Send } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useNavigate } from "react-router-dom";

import { LiveMarkdownEditor } from "../../components/LiveMarkdownEditor";
import type {
  CoursePackageItem,
  CourseScheduleItem,
  Project,
  SubmissionVersion,
  TaskSubmission,
} from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { schoolStageLabel } from "../../lib/schoolStages";
import {
  defaultSubmissionExtensions,
  formatMegabytes,
  maximumSubmissionFileBytes,
} from "../../lib/submissionFiles";


const { Text, Title, Paragraph } = Typography;
type WorkspaceMode = "fill" | "source" | "preview";
type SubmissionSource = "library" | "local";
type CourseWorkspaceField = {
  id: string;
  type: "text" | "textarea" | "radio" | "checkbox";
  label: string;
  required: boolean;
  options: string[];
};
type CourseWorkspaceAnswers = Record<string, string | string[]>;
type CourseWorkspace = {
  course_id: number;
  material_id: number;
  title: string;
  original_name: string;
  content_markdown: string;
  fields: CourseWorkspaceField[];
  answers: CourseWorkspaceAnswers;
  validation_warnings: string[];
  saved: boolean;
  project_id?: number | null;
  project_status: string;
  updated_at?: string | null;
};

function workspaceMarkdownWithFields(markdown: string, fields: CourseWorkspaceField[]) {
  const byId = new Map(fields.map((field) => [field.id, field]));
  return markdown.replace(/\{\{field\b[^{}]*\}\}/g, (marker) => {
    const id = marker.match(/\bid\s*=\s*"([^"]+)"/)?.[1] || "";
    const field = byId.get(id);
    if (!field) return marker;
    const label = field.label.replace(/([\\[\]])/g, "\\$1");
    return `[${label}](coderai-field:${encodeURIComponent(field.id)})`;
  });
}

function answersFingerprint(answers: CourseWorkspaceAnswers) {
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(answers)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, value]) => [key, Array.isArray(value) ? [...value].sort() : value]),
    ),
  );
}

function scheduleState(status: CourseScheduleItem["status"]) {
  if (status === "active") return { color: "green", label: "学习中" };
  if (status === "overdue") return { color: "red", label: "已逾期" };
  if (status === "canceled") return { color: "default", label: "已取消" };
  return { color: "blue", label: "未开始" };
}

export function StudentCourseReader({
  packages,
  schedules,
  projects,
  submissions,
  onRefresh,
}: {
  packages: CoursePackageItem[];
  schedules: CourseScheduleItem[];
  projects: Project[];
  submissions: TaskSubmission[];
  onRefresh: () => Promise<void>;
}) {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const orderedSchedules = useMemo(
    () => [...schedules].sort((left, right) => {
      const statusOrder: Record<CourseScheduleItem["status"], number> = {
        active: 0,
        scheduled: 1,
        overdue: 2,
        canceled: 3,
      };
      const statusDifference = statusOrder[left.status] - statusOrder[right.status];
      if (statusDifference) return statusDifference;
      if (left.status === "scheduled") return left.starts_at.localeCompare(right.starts_at);
      if (left.status === "overdue") {
        return (right.due_at || right.starts_at).localeCompare(left.due_at || left.starts_at);
      }
      return right.starts_at.localeCompare(left.starts_at);
    }),
    [schedules],
  );
  const [courseSearch, setCourseSearch] = useState("");
  const [coursePage, setCoursePage] = useState(1);
  const [coursePageSize, setCoursePageSize] = useState(10);
  const filteredSchedules = useMemo(() => {
    const keyword = courseSearch.trim().toLowerCase();
    if (!keyword) return orderedSchedules;
    return orderedSchedules.filter((item) =>
      item.course_title.toLowerCase().includes(keyword)
      || item.package_title.toLowerCase().includes(keyword),
    );
  }, [courseSearch, orderedSchedules]);
  const pageSchedules = useMemo(
    () => filteredSchedules.slice((coursePage - 1) * coursePageSize, coursePage * coursePageSize),
    [coursePage, coursePageSize, filteredSchedules],
  );
  const [selectedScheduleId, setSelectedScheduleId] = useState<number | null>(orderedSchedules[0]?.id ?? null);
  const [selectedProjectId, setSelectedProjectId] = useState<number | undefined>();
  const [submissionSource, setSubmissionSource] = useState<SubmissionSource>("library");
  const [localSubmissionFile, setLocalSubmissionFile] = useState<File | null>(null);
  const [localSubmissionTitle, setLocalSubmissionTitle] = useState("");
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("preview");
  const [workspaceText, setWorkspaceText] = useState("");
  const [savedWorkspaceText, setSavedWorkspaceText] = useState("");
  const [workspaceFields, setWorkspaceFields] = useState<CourseWorkspaceField[]>([]);
  const [workspaceAnswers, setWorkspaceAnswers] = useState<CourseWorkspaceAnswers>({});
  const [savedWorkspaceAnswers, setSavedWorkspaceAnswers] = useState<CourseWorkspaceAnswers>({});
  const [workspaceWarnings, setWorkspaceWarnings] = useState<string[]>([]);
  const [workspaceProjectId, setWorkspaceProjectId] = useState<number | null>(null);
  const [workspaceUpdatedAt, setWorkspaceUpdatedAt] = useState<string | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [busy, setBusy] = useState("");
  const [submissionVersions, setSubmissionVersions] = useState<SubmissionVersion[]>([]);
  const [submissionVersionsLoading, setSubmissionVersionsLoading] = useState(false);
  const workspaceDirty = workspaceText !== savedWorkspaceText
    || answersFingerprint(workspaceAnswers) !== answersFingerprint(savedWorkspaceAnswers);
  const workspaceFormMarkdown = useMemo(
    () => workspaceMarkdownWithFields(workspaceText, workspaceFields),
    [workspaceFields, workspaceText],
  );

  const selectedSchedule = pageSchedules.find((item) => item.id === selectedScheduleId) || pageSchedules[0] || null;
  const selectedPackage = packages.find((item) => item.id === selectedSchedule?.package_id);
  const selectedCourse = selectedPackage?.courses.find((item) => item.id === selectedSchedule?.course_id);
  const submission = submissions.find((item) => item.task_id === selectedSchedule?.task_id);
  const projectOptions = projects
    .filter((item) => item.lifecycle_status === "active" && item.moderation_status === "approved")
    .map((item) => ({ value: item.id, label: `${item.title} · ${item.project_type}` }));

  useEffect(() => {
    if (selectedScheduleId && !pageSchedules.some((item) => item.id === selectedScheduleId)) {
      setSelectedScheduleId(pageSchedules[0]?.id ?? null);
    } else if (!selectedScheduleId && pageSchedules[0]) {
      setSelectedScheduleId(pageSchedules[0].id);
    }
  }, [pageSchedules, selectedScheduleId]);

  useEffect(() => {
    setCoursePage(1);
  }, [coursePageSize, courseSearch]);

  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(filteredSchedules.length / coursePageSize));
    if (coursePage > lastPage) setCoursePage(lastPage);
  }, [coursePage, coursePageSize, filteredSchedules.length]);

  useEffect(() => {
    setWorkspaceOpen(false);
    setWorkspaceText("");
    setSavedWorkspaceText("");
    setWorkspaceFields([]);
    setWorkspaceAnswers({});
    setSavedWorkspaceAnswers({});
    setWorkspaceWarnings([]);
    setWorkspaceProjectId(null);
    setWorkspaceUpdatedAt(null);
    setWorkspaceError("");
    setSubmissionSource("library");
    setLocalSubmissionFile(null);
    setLocalSubmissionTitle("");
  }, [selectedCourse?.id]);

  useEffect(() => {
    setSubmissionVersions([]);
  }, [submission?.id]);

  const loadSubmissionVersions = async () => {
    if (!submission || submissionVersionsLoading || submissionVersions.length) return;
    setSubmissionVersionsLoading(true);
    try {
      const response = await api.get(`/api/submissions/${submission.id}/versions`);
      setSubmissionVersions(response.data.versions || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSubmissionVersionsLoading(false);
    }
  };

  const resetWorkspace = () => {
    setWorkspaceOpen(false);
    setWorkspaceText("");
    setSavedWorkspaceText("");
    setWorkspaceFields([]);
    setWorkspaceAnswers({});
    setSavedWorkspaceAnswers({});
    setWorkspaceWarnings([]);
    setWorkspaceProjectId(null);
    setWorkspaceUpdatedAt(null);
    setWorkspaceError("");
  };

  const requestWorkspaceClose = () => {
    if (!workspaceDirty) {
      resetWorkspace();
      return;
    }
    modal.confirm({
      title: "放弃未保存的修改？",
      content: "关闭后，本次尚未保存的工程包内容将丢失。",
      okText: "放弃修改",
      cancelText: "继续编辑",
      okButtonProps: { danger: true },
      onOk: resetWorkspace,
    });
  };

  const openWorkspace = async (mode: WorkspaceMode) => {
    if (!selectedCourse) return;
    setWorkspaceOpen(true);
    setWorkspaceMode(mode);
    setWorkspaceLoading(true);
    setWorkspaceError("");
    try {
      const response = await api.get<{ workspace: CourseWorkspace }>(
        `/api/curriculum-courses/${selectedCourse.id}/workspace`,
      );
      const workspace = response.data.workspace;
      setWorkspaceText(workspace.content_markdown);
      setSavedWorkspaceText(workspace.content_markdown);
      setWorkspaceFields(workspace.fields || []);
      setWorkspaceAnswers(workspace.answers || {});
      setSavedWorkspaceAnswers(workspace.answers || {});
      setWorkspaceWarnings(workspace.validation_warnings || []);
      setWorkspaceMode(workspace.fields?.length ? mode : mode === "fill" ? "source" : mode);
      setWorkspaceProjectId(workspace.project_id ?? null);
      setWorkspaceUpdatedAt(workspace.updated_at ?? null);
      if (workspace.project_id) setSelectedProjectId(workspace.project_id);
    } catch (error) {
      setWorkspaceError(explainError(error));
    } finally {
      setWorkspaceLoading(false);
    }
  };

  const saveWorkspace = async () => {
    if (!selectedCourse) return;
    setBusy("workspace-save");
    try {
      const currentFieldIds = new Set(
        [...workspaceText.matchAll(/\{\{field\b[^{}]*\}\}/g)]
          .map((match) => match[0].match(/\bid\s*=\s*"([^"]+)"/)?.[1])
          .filter((fieldId): fieldId is string => Boolean(fieldId)),
      );
      const currentAnswers = Object.fromEntries(
        Object.entries(workspaceAnswers).filter(([fieldId]) => currentFieldIds.has(fieldId)),
      );
      const response = await api.put<{ workspace: CourseWorkspace }>(
        `/api/curriculum-courses/${selectedCourse.id}/workspace`,
        { content_markdown: workspaceText, answers: currentAnswers },
      );
      const workspace = response.data.workspace;
      setWorkspaceText(workspace.content_markdown);
      setSavedWorkspaceText(workspace.content_markdown);
      setWorkspaceFields(workspace.fields || []);
      setWorkspaceAnswers(workspace.answers || {});
      setSavedWorkspaceAnswers(workspace.answers || {});
      setWorkspaceWarnings(workspace.validation_warnings || []);
      setWorkspaceProjectId(workspace.project_id ?? null);
      setWorkspaceUpdatedAt(workspace.updated_at ?? null);
      if (workspace.project_id) setSelectedProjectId(workspace.project_id);
      message.success("工程包已保存到我的作品");
      await onRefresh();
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const updateWorkspaceAnswer = (fieldId: string, value: string | string[]) => {
    setWorkspaceAnswers((current) => ({ ...current, [fieldId]: value }));
  };

  const renderWorkspaceField = (field: CourseWorkspaceField, readOnly: boolean) => {
    const value = workspaceAnswers[field.id] ?? (field.type === "checkbox" ? [] : "");
    if (readOnly) {
      const displayValue = Array.isArray(value) ? value.join("、") : value;
      return (
        <span className="courseFieldPreview">
          <span className="courseFieldPreviewLabel">{field.label}{field.required ? " *" : ""}</span>
          <span>{displayValue || "未填写"}</span>
        </span>
      );
    }
    return (
      <span className={`courseFieldControl courseFieldControl-${field.type}`}>
        <span className="courseFieldLabel">{field.label}{field.required && <Tag color="red">必填</Tag>}</span>
        {field.type === "text" && (
          <Input
            size="small"
            value={typeof value === "string" ? value : ""}
            onChange={(event) => updateWorkspaceAnswer(field.id, event.target.value)}
            aria-label={field.label}
          />
        )}
        {field.type === "textarea" && (
          <Input.TextArea
            value={typeof value === "string" ? value : ""}
            autoSize={{ minRows: 2, maxRows: 8 }}
            onChange={(event) => updateWorkspaceAnswer(field.id, event.target.value)}
            aria-label={field.label}
          />
        )}
        {field.type === "radio" && (
          <Radio.Group
            value={typeof value === "string" ? value : ""}
            options={field.options.map((option) => ({ label: option, value: option }))}
            onChange={(event) => updateWorkspaceAnswer(field.id, event.target.value)}
            aria-label={field.label}
          />
        )}
        {field.type === "checkbox" && (
          <Checkbox.Group
            value={Array.isArray(value) ? value : []}
            options={field.options}
            onChange={(values) => updateWorkspaceAnswer(field.id, values)}
            aria-label={field.label}
          />
        )}
      </span>
    );
  };

  const workspaceMarkdownComponents = {
    a: ({ href, children }: { href?: string; children?: ReactNode }) => {
      if (href?.startsWith("coderai-field:")) {
        const fieldId = decodeURIComponent(href.slice("coderai-field:".length));
        const field = workspaceFields.find((item) => item.id === fieldId);
        if (field) return renderWorkspaceField(field, workspaceMode === "preview");
      }
      return <a href={href}>{children}</a>;
    },
  };

  const downloadMaterial = async () => {
    if (!selectedCourse) return;
    const material = selectedCourse.materials.starter_markdown;
    setBusy("download-starter");
    try {
      const response = await api.get(
        `/api/curriculum-courses/${selectedCourse.id}/materials/starter_markdown/download`,
        { responseType: "blob" },
      );
      if (await saveBlobFile(response.data, material.original_name || "工程包.md")) {
        message.success("工程包原件已保存");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const submitProject = async () => {
    if (!selectedSchedule || !selectedProjectId) return;
    setBusy("submit");
    try {
      await api.post(`/api/course-schedules/${selectedSchedule.id}/submissions`, { project_id: selectedProjectId });
      message.success(submission ? "已提交新版本" : "作品已提交");
      await onRefresh();
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const chooseLocalSubmissionFile = (file: File) => {
    const allowedExtensions = selectedCourse?.submission_extensions?.length
      ? selectedCourse.submission_extensions
      : defaultSubmissionExtensions;
    const maximumBytes = selectedCourse?.submission_max_bytes || maximumSubmissionFileBytes;
    const extension = file.name.match(/\.[^.]+$/)?.[0]?.toLowerCase() || "";
    if (!allowedExtensions.includes(extension)) {
      message.error(`当前课程不接受 ${extension || "无扩展名"} 文件`);
      return Upload.LIST_IGNORE;
    }
    if (file.size > maximumBytes) {
      message.error(`当前课程的作品文件不能超过 ${formatMegabytes(maximumBytes)}`);
      return Upload.LIST_IGNORE;
    }
    setLocalSubmissionFile(file);
    setLocalSubmissionTitle(file.name.replace(/\.[^.]+$/, "").slice(0, 160));
    return Upload.LIST_IGNORE;
  };

  const uploadAndSubmitLocalFile = async () => {
    if (!selectedSchedule || !localSubmissionFile || !localSubmissionTitle.trim()) return;
    setBusy("upload-submit");
    try {
      const formData = new FormData();
      formData.append("file", localSubmissionFile);
      formData.append("title", localSubmissionTitle.trim());
      const response = await api.post(
        `/api/course-schedules/${selectedSchedule.id}/submissions/file`,
        formData,
      );
      if (response.data.moderation_pending) message.warning(response.data.message || "文件已提交，正在等待安全复核");
      else message.success(response.data.message || (submission ? "本机文件已提交为新版本" : "本机文件已提交"));
      setLocalSubmissionFile(null);
      setLocalSubmissionTitle("");
      await onRefresh();
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="page">
      <div className="pageTitle">
        <Title level={2}>课程学习</Title>
        <Text>查看排给你的课程资料，完成作品并提交给老师。</Text>
      </div>

      {!orderedSchedules.length ? (
        <Empty description="老师还没有为你安排课程" />
      ) : (
        <div className="studentCourseLayout">
          <aside className="studentScheduleList" aria-label="我的课程列表">
            <div className="studentScheduleListHeader"><Text strong>我的课程</Text><Tag>{orderedSchedules.length}</Tag></div>
            <Input.Search
              className="studentCourseSearch"
              allowClear
              value={courseSearch}
              onChange={(event) => setCourseSearch(event.target.value)}
              placeholder="搜索课程或课程包名称"
              aria-label="搜索课程名称"
            />
            <List
              dataSource={pageSchedules}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的课程" /> }}
              renderItem={(item) => {
                const state = scheduleState(item.status);
                return (
                  <List.Item
                    className={item.id === selectedSchedule?.id ? "studentScheduleItem active" : "studentScheduleItem"}
                    onClick={() => setSelectedScheduleId(item.id)}
                  >
                    <Space direction="vertical" size={6} className="fullWidth">
                      <Text strong>{item.course_title}</Text>
                      <Text type="secondary">{item.package_title}</Text>
                      <Text type="secondary">开始：{formatBeijingTime(item.starts_at)}</Text>
                      <Space wrap><Tag color={state.color}>{state.label}</Tag>{item.has_submitted && <Tag color="green" icon={<CheckCircle2 size={12} />}>已提交</Tag>}</Space>
                    </Space>
                  </List.Item>
                );
              }}
            />
            <Pagination
              className="studentCoursePagination"
              current={coursePage}
              pageSize={coursePageSize}
              total={filteredSchedules.length}
              pageSizeOptions={[5, 10, 20]}
              showSizeChanger
              showLessItems
              onChange={(page, pageSize) => {
                setCoursePage(page);
                setCoursePageSize(pageSize);
              }}
            />
          </aside>

          <main className="studentCourseContent">
            {selectedSchedule && selectedCourse ? (
              <Space direction="vertical" size={18} className="fullWidth">
                <header className="studentCourseHeader">
                  <div>
                    <Space wrap>
                      <Tag color={scheduleState(selectedSchedule.status).color}>{scheduleState(selectedSchedule.status).label}</Tag>
                      <Text type="secondary">{selectedSchedule.package_title}</Text>
                      {selectedPackage?.school_stages.map((stage) => <Tag key={stage}>{schoolStageLabel(stage)}</Tag>)}
                    </Space>
                    <Title level={3}>{selectedCourse.title}</Title>
                  </div>
                  <div className="studentCourseDates">
                    <Text><Clock3 size={14} /> 开始：{formatBeijingTime(selectedSchedule.starts_at)}</Text>
                    <Text type="secondary">截止：{selectedSchedule.due_at ? formatBeijingTime(selectedSchedule.due_at) : "不设截止"}</Text>
                  </div>
                </header>

                {selectedSchedule.status === "scheduled" && <Alert type="info" showIcon message="课程尚未开始" description="开始后可查看课堂资料并提交作品。" />}
                {selectedSchedule.status === "overdue" && <Alert type="warning" showIcon message="课程已超过截止时间" description="仍可继续提交，系统会将本次提交标记为逾期。" />}

                <section>
                  <div className="studentCourseSectionTitle"><BookOpen size={18} /><Title level={4}>课程资料</Title></div>
                  <div className="studentMaterialGrid">
                    {(() => {
                      const material = selectedCourse.materials.starter_markdown;
                      const isLocked = !material.missing && !material.can_preview;
                      return (
                        <article className="studentMaterial">
                          <Space><FileText size={18} /><Text strong>{material.label}</Text></Space>
                          <Text type="secondary">{material.missing ? "管理员暂未补充" : material.original_name}</Text>
                          {isLocked && <Tag color="gold">课程开始后开放</Tag>}
                          {workspaceProjectId && <Tag color="green">已保存到作品库</Tag>}
                          <Space wrap>
                            {material.can_preview && <Button size="small" icon={<Eye size={14} />} onClick={() => void openWorkspace("preview")}>查看</Button>}
                            {material.can_preview && <Button size="small" type="primary" icon={<Edit3 size={14} />} onClick={() => void openWorkspace("fill")}>在线填写与编辑</Button>}
                            {material.can_download && <Button size="small" icon={<Download size={14} />} loading={busy === "download-starter"} onClick={() => void downloadMaterial()}>下载原件</Button>}
                          </Space>
                        </article>
                      );
                    })()}
                  </div>
                </section>

                <section className="studentAssignmentSection">
                  <div className="studentCourseSectionTitle"><Send size={18} /><Title level={4}>作品提交</Title></div>
                  <div className="assignmentInstructions">
                    <Text type="secondary">提交要求</Text>
                    <Paragraph className="preWrapText">{selectedCourse.assignment_instructions || "老师暂未补充特别要求，请按课堂说明完成作品。"}</Paragraph>
                    <Space wrap><Text type="secondary">允许工具</Text>{selectedCourse.tool_scope ? selectedCourse.tool_scope.split(",").map((item) => <Tag key={item}>{item}</Tag>) : <Tag>无 AI 工具</Tag>}</Space>
                  </div>

                  {submission && (
                    <>
                      <Alert
                        className="mb16"
                        type={submission.status === "reviewed" ? "success" : submission.status === "returned" ? "warning" : "info"}
                        showIcon
                        message={submission.status === "reviewed" ? `老师已批改${submission.score != null ? `：${submission.score}/${submission.max_score} 分` : ""}` : submission.status === "returned" ? "老师已退回修改" : "作品已提交，等待老师批改"}
                        description={submission.feedback || `当前提交版本：${submission.version_count}`}
                      />
                      <Collapse
                        className="submissionHistoryCollapse mb16"
                        onChange={(keys) => { if (keys.length) void loadSubmissionVersions(); }}
                        items={[{
                          key: "history",
                          label: `提交历史（${submission.version_count}）`,
                          children: (
                            <List
                              loading={submissionVersionsLoading}
                              dataSource={submissionVersions}
                              locale={{ emptyText: "暂无历史版本" }}
                              renderItem={(version) => (
                                <List.Item
                                  actions={[
                                    <Button
                                      key="open"
                                      size="small"
                                      icon={<Eye size={14} />}
                                      onClick={() => navigate(`/student/submissions/${submission.id}/versions/${version.id}`)}
                                    >
                                      查看该版本
                                    </Button>,
                                  ]}
                                >
                                  <Space wrap>
                                    <Text strong>第 {version.version_number} 版</Text>
                                    <Text>{version.project_title}</Text>
                                    <Text type="secondary">{formatBeijingTime(version.created_at)}</Text>
                                    {version.is_late && <Tag color="orange">逾期</Tag>}
                                  </Space>
                                </List.Item>
                              )}
                            />
                          ),
                        }]}
                      />
                    </>
                  )}

                  {selectedSchedule.status !== "scheduled" && selectedSchedule.status !== "canceled" && (
                    <div className="studentSubmitPanel">
                      <Segmented
                        value={submissionSource}
                        onChange={(value) => setSubmissionSource(value as SubmissionSource)}
                        options={[
                          { value: "library", label: "从作品库选择" },
                          { value: "local", label: "选择本机文件" },
                        ]}
                      />
                      {submissionSource === "library" ? (
                        <Space wrap className="studentSubmitBar">
                          <Select
                            showSearch
                            optionFilterProp="label"
                            value={selectedProjectId}
                            onChange={setSelectedProjectId}
                            placeholder="从作品库选择一个作品"
                            options={projectOptions}
                            style={{ minWidth: 300 }}
                          />
                          <Button type="primary" icon={<Send size={15} />} disabled={!selectedProjectId} loading={busy === "submit"} onClick={() => void submitProject()}>{submission ? "提交新版本" : "提交作品"}</Button>
                        </Space>
                      ) : (
                        <div className="localSubmissionPanel">
                          <Upload
                            accept={(selectedCourse.submission_extensions?.length
                              ? selectedCourse.submission_extensions
                              : defaultSubmissionExtensions).join(",")}
                            showUploadList={false}
                            beforeUpload={chooseLocalSubmissionFile}
                          >
                            <Button icon={<FileUp size={15} />}>选择文件</Button>
                          </Upload>
                          <Input
                            value={localSubmissionTitle}
                            disabled={!localSubmissionFile}
                            maxLength={160}
                            aria-label="本机文件作品名称"
                            placeholder="选择文件后填写作品名称"
                            onChange={(event) => setLocalSubmissionTitle(event.target.value)}
                          />
                          <div className="localSubmissionFileState">
                            <Text strong>{localSubmissionFile?.name || "尚未选择文件"}</Text>
                            <Text type="secondary">
                              {(selectedCourse.submission_extensions?.length
                                ? selectedCourse.submission_extensions
                                : defaultSubmissionExtensions).join("、")}
                              {` · 上限 ${formatMegabytes(selectedCourse.submission_max_bytes || maximumSubmissionFileBytes)}`}
                            </Text>
                          </div>
                          <Button
                            type="primary"
                            icon={<Send size={15} />}
                            disabled={!localSubmissionFile || !localSubmissionTitle.trim()}
                            loading={busy === "upload-submit"}
                            onClick={() => void uploadAndSubmitLocalFile()}
                          >
                            {submission ? "提交文件新版本" : "提交本机文件"}
                          </Button>
                        </div>
                      )}
                    </div>
                  )}
                  {!projectOptions.length && submissionSource === "library" && selectedSchedule.status !== "scheduled" && <Text type="secondary">作品库暂无可提交作品，也可以切换到“选择本机文件”。</Text>}
                </section>
              </Space>
            ) : filteredSchedules.length
              ? <Alert type="warning" showIcon message="课程内容暂时不可用" />
              : <Empty description="没有匹配的课程，请调整搜索关键词" />}
          </main>
        </div>
      )}

      <Drawer
        title={selectedCourse ? `${selectedCourse.title} · 工程包` : "工程包"}
        open={workspaceOpen}
        onClose={requestWorkspaceClose}
        width={900}
        extra={(
          <Button
            type="primary"
            icon={<Save size={15} />}
            disabled={workspaceLoading || Boolean(workspaceError) || !workspaceDirty}
            loading={busy === "workspace-save"}
            onClick={() => void saveWorkspace()}
          >
            保存
          </Button>
        )}
      >
        {workspaceLoading && <Alert type="info" showIcon message="正在加载工程包" />}
        {!workspaceLoading && workspaceError && (
          <Alert
            type="error"
            showIcon
            message="工程包加载失败"
            description={workspaceError}
            action={<Button size="small" onClick={() => void openWorkspace(workspaceMode)}>重试</Button>}
          />
        )}
        {!workspaceLoading && !workspaceError && workspaceOpen && (
          <Space direction="vertical" size={16} className="fullWidth">
            <div className="courseWorkspaceToolbar">
              <Segmented
                value={workspaceMode}
                onChange={(value) => setWorkspaceMode(value as WorkspaceMode)}
                options={[
                  { value: "fill", label: "填写", icon: <Edit3 size={14} />, disabled: !workspaceFields.length },
                  { value: "source", label: "源码", icon: <FileText size={14} /> },
                  { value: "preview", label: "预览", icon: <Eye size={14} /> },
                ]}
              />
              <Text type="secondary">
                {workspaceUpdatedAt ? `保存于 ${formatBeijingTime(workspaceUpdatedAt)}` : "尚未保存"}
              </Text>
            </div>
            {workspaceWarnings.map((warning) => (
              <Alert key={warning} type="warning" showIcon message="工程包字段需要检查" description={warning} />
            ))}
            {workspaceMode === "source" ? (
              <LiveMarkdownEditor
                value={workspaceText}
                maxLength={500_000}
                onChange={setWorkspaceText}
                ariaLabel="工程包 Markdown 编辑器"
                placeholder="# 我的课堂工程"
              />
            ) : (
              <article className={`markdownPreview courseMarkdownPreview courseWorkspacePreview ${workspaceMode === "fill" ? "courseWorkspaceForm" : ""}`}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={workspaceMarkdownComponents}
                  urlTransform={(url) => url}
                >
                  {workspaceFormMarkdown || "工程包内容为空。"}
                </ReactMarkdown>
              </article>
            )}
          </Space>
        )}
      </Drawer>
    </div>
  );
}
