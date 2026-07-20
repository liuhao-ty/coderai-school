import { Alert, App, Button, Drawer, Empty, List, Select, Space, Tag, Typography } from "antd";
import { BookOpen, CheckCircle2, Clock3, Download, Eye, FileText, Presentation, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  CourseMaterialKind,
  CoursePackageItem,
  CourseScheduleItem,
  Project,
  TaskSubmission,
} from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { schoolStageLabel } from "../../lib/schoolStages";


const { Text, Title, Paragraph } = Typography;

function scheduleState(status: CourseScheduleItem["status"]) {
  if (status === "active") return { color: "green", label: "学习中" };
  if (status === "overdue") return { color: "red", label: "已逾期" };
  if (status === "canceled") return { color: "default", label: "已取消" };
  return { color: "blue", label: "未开始" };
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
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
  const { message } = App.useApp();
  const orderedSchedules = useMemo(
    () => [...schedules].sort((left, right) => left.starts_at.localeCompare(right.starts_at)),
    [schedules],
  );
  const [selectedScheduleId, setSelectedScheduleId] = useState<number | null>(orderedSchedules[0]?.id ?? null);
  const [selectedProjectId, setSelectedProjectId] = useState<number | undefined>();
  const [preview, setPreview] = useState<{ kind: CourseMaterialKind; title: string } | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState("");

  const selectedSchedule = orderedSchedules.find((item) => item.id === selectedScheduleId) || orderedSchedules[0] || null;
  const selectedPackage = packages.find((item) => item.id === selectedSchedule?.package_id);
  const selectedCourse = selectedPackage?.courses.find((item) => item.id === selectedSchedule?.course_id);
  const submission = submissions.find((item) => item.task_id === selectedSchedule?.task_id);
  const projectOptions = projects
    .filter((item) => item.lifecycle_status === "active" && item.moderation_status === "approved")
    .map((item) => ({ value: item.id, label: `${item.title} · ${item.project_type}` }));

  useEffect(() => {
    if (selectedScheduleId && !orderedSchedules.some((item) => item.id === selectedScheduleId)) {
      setSelectedScheduleId(orderedSchedules[0]?.id ?? null);
    } else if (!selectedScheduleId && orderedSchedules[0]) {
      setSelectedScheduleId(orderedSchedules[0].id);
    }
  }, [orderedSchedules, selectedScheduleId]);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const closePreview = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreview(null);
    setPreviewText("");
    setPreviewUrl("");
  };

  const openPreview = async (kind: CourseMaterialKind) => {
    if (!selectedCourse) return;
    const material = selectedCourse.materials[kind];
    setPreview({ kind, title: `${selectedCourse.title} · ${material.label}` });
    setPreviewLoading(true);
    setPreviewText("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl("");
    try {
      if (kind === "slides") {
        const response = await api.get(`/api/curriculum-courses/${selectedCourse.id}/materials/${kind}/preview`, { responseType: "blob" });
        const pdf = new Blob([response.data], { type: "application/pdf" });
        setPreviewUrl(URL.createObjectURL(pdf));
      } else {
        const response = await api.get(`/api/curriculum-courses/${selectedCourse.id}/materials/${kind}/preview`, { responseType: "text" });
        setPreviewText(String(response.data || ""));
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setPreviewLoading(false);
    }
  };

  const downloadMaterial = async (kind: CourseMaterialKind) => {
    if (!selectedCourse) return;
    const material = selectedCourse.materials[kind];
    setBusy(`download-${kind}`);
    try {
      const response = await api.get(`/api/curriculum-courses/${selectedCourse.id}/materials/${kind}/download`, { responseType: "blob" });
      saveBlob(response.data, material.original_name || `${material.label}.md`);
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
            <List
              dataSource={orderedSchedules}
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
                      <Space wrap><Tag color={state.color}>{state.label}</Tag>{item.has_submitted && <Tag color="green" icon={<CheckCircle2 size={12} />}>已提交</Tag>}</Space>
                    </Space>
                  </List.Item>
                );
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
                    <Paragraph type="secondary">{selectedCourse.description || "管理员暂未填写课程简介。"}</Paragraph>
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
                    {(Object.keys(selectedCourse.materials) as CourseMaterialKind[]).map((kind) => {
                      const material = selectedCourse.materials[kind];
                      const isLocked = !material.missing && !material.can_preview;
                      return (
                        <article className="studentMaterial" key={kind}>
                          <Space>{kind === "slides" ? <Presentation size={18} /> : <FileText size={18} />}<Text strong>{material.label}</Text></Space>
                          <Text type="secondary">{material.missing ? "管理员暂未补充" : material.original_name}</Text>
                          {isLocked && <Tag color="gold">{kind === "result_markdown" ? "首次提交后解锁" : "课程开始后开放"}</Tag>}
                          {kind === "slides" && !material.missing && material.conversion_status !== "ready" && <Tag color="default">PPT 预览暂不可用</Tag>}
                          <Space wrap>
                            {material.can_preview && <Button size="small" icon={<Eye size={14} />} onClick={() => void openPreview(kind)}>预览</Button>}
                            {material.can_download && <Button size="small" icon={<Download size={14} />} loading={busy === `download-${kind}`} onClick={() => void downloadMaterial(kind)}>下载</Button>}
                          </Space>
                        </article>
                      );
                    })}
                  </div>
                </section>

                <section className="studentAssignmentSection">
                  <div className="studentCourseSectionTitle"><Send size={18} /><Title level={4}>作品提交</Title></div>
                  <div className="assignmentInstructions">
                    <Text type="secondary">提交要求</Text>
                    <Paragraph>{selectedCourse.assignment_instructions || "老师暂未补充特别要求，请按课堂说明完成作品。"}</Paragraph>
                    <Space wrap><Text type="secondary">允许工具</Text>{selectedCourse.tool_scope ? selectedCourse.tool_scope.split(",").map((item) => <Tag key={item}>{item}</Tag>) : <Tag>无 AI 工具</Tag>}</Space>
                  </div>

                  {submission && (
                    <Alert
                      className="mb16"
                      type={submission.status === "reviewed" ? "success" : submission.status === "returned" ? "warning" : "info"}
                      showIcon
                      message={submission.status === "reviewed" ? `老师已批改${submission.score != null ? `：${submission.score}/${submission.max_score} 分` : ""}` : submission.status === "returned" ? "老师已退回修改" : "作品已提交，等待老师批改"}
                      description={submission.feedback || `当前提交版本：${submission.version_count}`}
                    />
                  )}

                  {selectedSchedule.status !== "scheduled" && selectedSchedule.status !== "canceled" && (
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
                  )}
                  {!projectOptions.length && selectedSchedule.status !== "scheduled" && <Text type="secondary">作品库暂无可提交作品，请先在学习工作台完成并保存作品。</Text>}
                </section>
              </Space>
            ) : <Alert type="warning" showIcon message="课程内容暂时不可用" />}
          </main>
        </div>
      )}

      <Drawer title={preview?.title || "课程资料预览"} open={Boolean(preview)} onClose={closePreview} width={860}>
        {previewLoading && <Alert type="info" showIcon message="正在加载课程资料" />}
        {!previewLoading && preview?.kind === "slides" && previewUrl && <iframe className="coursePdfPreview" src={previewUrl} title="课堂 PPT PDF 预览" />}
        {!previewLoading && preview && preview.kind !== "slides" && <article className="markdownPreview courseMarkdownPreview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{previewText || "资料内容为空。"}</ReactMarkdown></article>}
      </Drawer>
    </div>
  );
}
