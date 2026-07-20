import {
  Alert, App as AntApp, Button, Card, Col, Drawer, Image as AntImage, Input, List, Popconfirm, Row, Segmented, Select, Space, Spin, Statistic, Tabs, Tag, Typography
} from "antd";
import {
  Archive, Download, Edit3, Eye, FileText, Library, RotateCcw, Save, Trash2
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Classroom, Project } from "../../domain-types";
import { EmptyState } from "../../components/PageState";
import { api } from "../../lib/api";
import { projectTypeLabel } from "../../lib/domain";
import { explainBlobError, explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import type { StudentProfile } from "../../types";


const { Title, Text, Paragraph } = Typography;

export function ProjectFileStatus({ project, compact = false }: { project: Project; compact?: boolean }) {
  if (project.file_status === "none") {
    return <Tag>无本地文件</Tag>;
  }
  if (project.file_status === "remote") {
    return <Tag color="blue">云端文件</Tag>;
  }
  if (project.file_status === "missing") {
    return (
      <Space wrap>
        <Tag color="red">文件缺失</Tag>
        {!compact && <Text copyable>{project.file_path}</Text>}
      </Space>
    );
  }
  return (
    <Space wrap>
      <Tag color="green">文件正常</Tag>
      {!compact && <Text copyable>{project.file_path}</Text>}
    </Space>
  );
}

export function ProjectLibrary({
  projects,
  onRefresh,
  audience,
  students = [],
  classrooms = []
}: {
  projects: Project[];
  onRefresh: () => Promise<void>;
  audience: "student" | "teacher" | "admin";
  students?: StudentProfile[];
  classrooms?: Classroom[];
}) {
  const { message } = AntApp.useApp();
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftSummary, setDraftSummary] = useState("");
  const [searchText, setSearchText] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [studentFilter, setStudentFilter] = useState<number | "all">("all");
  const [classroomFilter, setClassroomFilter] = useState<number | "all">("all");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [previewFileUrl, setPreviewFileUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewObjectUrlRef = useRef("");
  const [projectScope, setProjectScope] = useState<"active" | "archived" | "trash">("active");
  const [libraryProjects, setLibraryProjects] = useState<Project[]>(projects);
  const isTeacherView = audience !== "student";
  const archivedStudentReadOnly = audience === "teacher" && Boolean(selectedProject?.student_archived);

  const clearPreviewFile = () => {
    if (previewObjectUrlRef.current) {
      URL.revokeObjectURL(previewObjectUrlRef.current);
      previewObjectUrlRef.current = "";
    }
    setPreviewFileUrl("");
    setPreviewLoading(false);
    setPreviewError("");
  };

  const loadProjectScope = async (scope: "active" | "archived" | "trash") => {
    const res = await api.get("/api/projects", { params: { scope } });
    setLibraryProjects(res.data.projects || []);
  };

  const refreshLibrary = async () => {
    await onRefresh();
    if (projectScope !== "active") await loadProjectScope(projectScope);
  };

  useEffect(() => {
    if (projectScope === "active") setLibraryProjects(projects);
  }, [projectScope, projects]);

  useEffect(() => {
    if (projectScope !== "active") void loadProjectScope(projectScope).catch(() => undefined);
    clearPreviewFile();
    setSelectedProject(null);
  }, [projectScope]);

  useEffect(() => () => {
    if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
  }, []);

  const projectTypes = useMemo(() => Array.from(new Set(libraryProjects.map((project) => project.project_type))).filter(Boolean), [libraryProjects]);
  const filteredProjects = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return libraryProjects.filter((project) => {
      const matchesKeyword =
        !keyword ||
        project.title.toLowerCase().includes(keyword) ||
        project.owner_name.toLowerCase().includes(keyword) ||
        project.summary.toLowerCase().includes(keyword);
      const matchesType = typeFilter === "all" || project.project_type === typeFilter;
      const matchesStudent = studentFilter === "all" || project.user_id === studentFilter;
      const matchesClassroom = classroomFilter === "all" || project.classroom_id === classroomFilter;
      return matchesKeyword && matchesType && matchesStudent && matchesClassroom;
    });
  }, [classroomFilter, libraryProjects, searchText, studentFilter, typeFilter]);

  const classroomName = (classroomId?: number | null) =>
    classrooms.find((classroom) => classroom.id === classroomId)?.name || (classroomId ? "未命名班级" : "未分配班级");

  const openProject = async (project: Project) => {
    clearPreviewFile();
    let nextProject: Project;
    try {
      const res = await api.get(`/api/projects/${project.id}`);
      nextProject = res.data.project as Project;
      setSelectedProject(nextProject);
      setDraftTitle(nextProject.title);
      setDraftSummary(nextProject.summary || "");
    } catch (error) {
      message.error(explainError(error));
      return;
    }

    if (nextProject.project_type !== "image") return;
    if (nextProject.file_status === "remote") {
      setPreviewFileUrl(nextProject.file_path);
      return;
    }
    if (nextProject.file_status === "moderation_hidden") {
      setPreviewError("图片正在等待安全审核，审核通过后才能预览。");
      return;
    }
    if (nextProject.file_status === "missing") {
      setPreviewError("作品记录存在，但图片文件已经缺失。");
      return;
    }
    if (nextProject.file_status !== "ok") {
      setPreviewError("这个图片作品暂时没有可预览的文件。");
      return;
    }

    setPreviewLoading(true);
    try {
        const fileRes = await api.get(`/api/projects/${nextProject.id}/file`, { responseType: "blob" });
        const objectUrl = URL.createObjectURL(fileRes.data);
        previewObjectUrlRef.current = objectUrl;
        setPreviewFileUrl(objectUrl);
    } catch (error) {
      setPreviewError(explainError(error));
    } finally {
      setPreviewLoading(false);
    }
  };

  const imageDownloadName = (project: Project) => {
    const path = project.file_path.split(/[?#]/, 1)[0];
    const extension = path.match(/\.(png|jpe?g|webp|gif)$/i)?.[0] || ".png";
    const safeTitle = project.title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || `image-${project.id}`;
    return `${safeTitle}${extension}`;
  };

  const triggerDownload = (url: string, filename: string, openFallback = false) => {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    if (openFallback) {
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
    }
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  const downloadProjectImage = async () => {
    if (!selectedProject || selectedProject.project_type !== "image") return;
    if (!(["ok", "remote"] as string[]).includes(selectedProject.file_status)) {
      message.warning("当前图片没有可下载的文件");
      return;
    }
    setDownloading(true);
    try {
      if (selectedProject.file_status === "remote") {
        triggerDownload(selectedProject.file_path, imageDownloadName(selectedProject), true);
      } else {
        const response = await api.get(`/api/projects/${selectedProject.id}/file`, { responseType: "blob" });
        const downloadUrl = URL.createObjectURL(response.data);
        triggerDownload(downloadUrl, imageDownloadName(selectedProject));
        window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
      }
    } catch (error) {
      message.error(`图片下载失败：${await explainBlobError(error)}`);
    } finally {
      setDownloading(false);
    }
  };

  const saveProject = async () => {
    if (!selectedProject) return;
    setSaving(true);
    try {
      const res = await api.put(`/api/projects/${selectedProject.id}`, {
        title: draftTitle,
        summary: draftSummary
      });
      const nextProject = res.data.project as Project;
      setSelectedProject(nextProject);
      setDraftTitle(nextProject.title);
      setDraftSummary(nextProject.summary || "");
      await onRefresh();
      message.success("作品已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const deleteProject = async () => {
    if (!selectedProject) return;
    setDeleting(true);
    try {
      await api.delete(`/api/projects/${selectedProject.id}`);
      clearPreviewFile();
      setSelectedProject(null);
      await onRefresh();
      await loadProjectScope(projectScope);
      message.success("作品已移入回收站");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setDeleting(false);
    }
  };

  const archiveProject = async () => {
    if (!selectedProject) return;
    setDeleting(true);
    try {
      await api.post(`/api/projects/${selectedProject.id}/archive`);
      clearPreviewFile();
      setSelectedProject(null);
      await onRefresh();
      await loadProjectScope(projectScope);
      message.success("作品已归档");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setDeleting(false);
    }
  };

  const restoreProject = async () => {
    if (!selectedProject) return;
    setDeleting(true);
    try {
      await api.post(`/api/projects/${selectedProject.id}/restore`);
      clearPreviewFile();
      setSelectedProject(null);
      await onRefresh();
      await loadProjectScope(projectScope);
      message.success("作品已恢复到作品库");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setDeleting(false);
    }
  };

  const permanentlyDeleteProject = async () => {
    if (!selectedProject) return;
    setDeleting(true);
    try {
      await api.delete(`/api/projects/${selectedProject.id}/permanent`);
      clearPreviewFile();
      setSelectedProject(null);
      await loadProjectScope("trash");
      await onRefresh();
      message.success("作品已永久删除");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setDeleting(false);
    }
  };

  const lifecycleLabel = (project: Project) => {
    if (project.lifecycle_status === "archived") return <Tag color="gold">已归档</Tag>;
    if (project.lifecycle_status === "trashed") return <Tag color="red">回收站</Tag>;
    return <Tag color="green">正常作品</Tag>;
  };

  const moderationLabel = (project: Project) => {
    if (project.moderation_status === "pending") return <Tag color="orange">待图片复核</Tag>;
    if (project.moderation_status === "rejected") return <Tag color="red">审核未通过</Tag>;
    return null;
  };

  const lifecycleTime = (project: Project) => {
    if (project.lifecycle_status === "archived" && project.archived_at) {
      return `归档时间：${formatBeijingTime(project.archived_at)}`;
    }
    if (project.lifecycle_status === "trashed" && project.trashed_at) {
      return `移入时间：${formatBeijingTime(project.trashed_at)}`;
    }
    return `保存时间：${formatBeijingTime(project.created_at)}`;
  };

  const drawerActions = selectedProject ? (
    <Space wrap>
      {selectedProject.project_type === "image" && (
        <Button
          icon={<Download size={16} />}
          loading={downloading}
          disabled={!(["ok", "remote"] as string[]).includes(selectedProject.file_status)}
          onClick={() => void downloadProjectImage()}
        >下载原图</Button>
      )}
      {selectedProject.student_archived && <Tag color="default">归档学员历史作品{archivedStudentReadOnly ? " · 只读" : ""}</Tag>}
      {!archivedStudentReadOnly && selectedProject.lifecycle_status === "active" && (
        <Button icon={<Archive size={16} />} loading={deleting} onClick={archiveProject}>归档</Button>
      )}
      {!archivedStudentReadOnly && selectedProject.lifecycle_status === "archived" && (
        <Button icon={<RotateCcw size={16} />} loading={deleting} onClick={restoreProject}>恢复</Button>
      )}
      {!archivedStudentReadOnly && selectedProject.lifecycle_status !== "trashed" && (
        <Popconfirm
          title="移入回收站？"
          description="已提交为作业的作品会受到保护，无法移入回收站。"
          okText="移入回收站"
          cancelText="取消"
          onConfirm={deleteProject}
        >
          <Button danger icon={<Trash2 size={16} />} loading={deleting}>移入回收站</Button>
        </Popconfirm>
      )}
      {!archivedStudentReadOnly && selectedProject.lifecycle_status === "trashed" && (
        <>
          <Button icon={<RotateCcw size={16} />} loading={deleting} onClick={restoreProject}>恢复</Button>
          <Popconfirm
            title="永久删除这个作品？"
            description="作品记录和工作区内的作品文件将被删除，此操作不可恢复。"
            okText="永久删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={permanentlyDeleteProject}
          >
            <Button danger type="primary" icon={<Trash2 size={16} />} loading={deleting}>永久删除</Button>
          </Popconfirm>
        </>
      )}
      {!archivedStudentReadOnly && selectedProject.lifecycle_status !== "trashed" && (
        <Button icon={<Save size={16} />} type="primary" loading={saving} onClick={saveProject}>保存</Button>
      )}
    </Space>
  ) : null;

  return (
    <div className="page">
      <div className="pageTitle rowTitle">
        <div>
          <Title level={2}>{isTeacherView ? "作品管理" : "作品库"}</Title>
          <Text>
            {isTeacherView
              ? "查看学生保存和提交过的 AI 作品，支持按班级、学生和作品类型筛选。"
              : "学生生成的文字、图片、视频和工作流结果会保存在这里。"}
          </Text>
        </div>
        <Button icon={<Save size={16} />} onClick={refreshLibrary}>
          刷新
        </Button>
      </div>
      <Segmented
        className="mb16"
        block
        value={projectScope}
        onChange={(value) => setProjectScope(value as "active" | "archived" | "trash")}
        options={[
          { value: "active", label: <Space size={6}><Library size={15} />正常作品</Space> },
          { value: "archived", label: <Space size={6}><Archive size={15} />已归档</Space> },
          { value: "trash", label: <Space size={6}><Trash2 size={15} />回收站</Space> }
        ]}
      />
      {isTeacherView && (
        <Space direction="vertical" size={16} className="fullWidth mb16">
          <Row gutter={[12, 12]}>
            <Col xs={12} md={6}>
              <Card className="teacherMetric">
                <Statistic title="当前分区" value={libraryProjects.length} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card className="teacherMetric">
                <Statistic title="当前筛选" value={filteredProjects.length} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card className="teacherMetric">
                <Statistic title="学生数" value={new Set(libraryProjects.map((project) => project.user_id).filter(Boolean)).size} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card className="teacherMetric">
                <Statistic title="类型数" value={projectTypes.length} />
              </Card>
            </Col>
          </Row>
          <Card className="projectFilterPanel">
            <Row gutter={[12, 12]}>
              <Col xs={24} xl={8}>
                <Input
                  placeholder="搜索标题、学生或内容"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                />
              </Col>
              <Col xs={24} sm={8} xl={5}>
                <Select
                  className="fullWidth"
                  value={typeFilter}
                  onChange={setTypeFilter}
                  options={[
                    { value: "all", label: "全部类型" },
                    ...projectTypes.map((type) => ({ value: type, label: projectTypeLabel(type) }))
                  ]}
                />
              </Col>
              <Col xs={24} sm={8} xl={5}>
                <Select
                  className="fullWidth"
                  value={studentFilter}
                  onChange={setStudentFilter}
                  options={[
                    { value: "all", label: "全部学生" },
                    ...students.map((student) => ({ value: student.id, label: student.name }))
                  ]}
                />
              </Col>
              <Col xs={24} sm={8} xl={6}>
                <Select
                  className="fullWidth"
                  value={classroomFilter}
                  onChange={setClassroomFilter}
                  options={[
                    { value: "all", label: "全部班级" },
                    ...classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }))
                  ]}
                />
              </Col>
            </Row>
          </Card>
        </Space>
      )}
      <List
        grid={{ gutter: 16, xs: 1, sm: 2, lg: 3 }}
        dataSource={filteredProjects}
        locale={{
          emptyText: projectScope === "archived"
            ? <EmptyState title="还没有归档作品" description="归档后的作品会显示在这里" />
            : projectScope === "trash"
              ? <EmptyState title="回收站为空" description="移入回收站的作品会暂存在这里" />
              : <EmptyState title="还没有作品" description="完成一次 AI 创作后，作品会自动保存在这里" />
        }}
        renderItem={(project) => (
          <List.Item>
            <Card
              className="projectCard"
              title={project.title}
              extra={
                <Button type="text" icon={<Eye size={16} />} onClick={() => openProject(project)}>
                  预览
                </Button>
              }
              onClick={() => openProject(project)}
            >
              <Space direction="vertical" size={8}>
                <Space wrap>
                  <Tag>{projectTypeLabel(project.project_type)}</Tag>
                  {lifecycleLabel(project)}
                  {isTeacherView && moderationLabel(project)}
                  {isTeacherView && <Tag color="blue">{project.owner_name || "未归属学生"}</Tag>}
                  {isTeacherView && <Tag>{classroomName(project.classroom_id)}</Tag>}
                </Space>
                <Paragraph ellipsis={{ rows: 4 }}>{project.summary || "暂无摘要"}</Paragraph>
                <Text type="secondary">{lifecycleTime(project)}</Text>
                <ProjectFileStatus project={project} compact />
              </Space>
            </Card>
          </List.Item>
        )}
      />
      <Drawer
        title={selectedProject ? selectedProject.title : "作品预览"}
        open={Boolean(selectedProject)}
        width={760}
        onClose={() => {
          clearPreviewFile();
          setSelectedProject(null);
        }}
        extra={drawerActions}
      >
        {selectedProject && (
          <Space direction="vertical" size={16} className="fullWidth">
            <Row gutter={12}>
              <Col xs={24} md={16}>
                <Text type="secondary">作品类型</Text>
                <div>
                  <Tag>{projectTypeLabel(selectedProject.project_type)}</Tag>
                  {lifecycleLabel(selectedProject)}
                  {isTeacherView && moderationLabel(selectedProject)}
                </div>
              </Col>
              <Col xs={24} md={8}>
                <Text type="secondary">创建时间</Text>
                <div>{formatBeijingTime(selectedProject.created_at)}</div>
              </Col>
            </Row>
            {isTeacherView && (
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Text type="secondary">学生</Text>
                  <div>{selectedProject.owner_name || "未归属学生"}</div>
                </Col>
                <Col xs={24} md={12}>
                  <Text type="secondary">班级</Text>
                  <div>{classroomName(selectedProject.classroom_id)}</div>
                </Col>
              </Row>
            )}
            <Row gutter={12}>
              <Col xs={24} md={12}>
                <Text type="secondary">最近更新</Text>
                <div>{formatBeijingTime(selectedProject.updated_at || selectedProject.created_at)}</div>
              </Col>
              <Col xs={24} md={12}>
                <Text type="secondary">文件状态</Text>
                <div>
                  <ProjectFileStatus project={selectedProject} />
                </div>
              </Col>
            </Row>
            <Tabs
              defaultActiveKey="preview"
              items={[
                {
                  key: "preview",
                  label: (
                    <Space>
                      <Eye size={16} />
                      {selectedProject.project_type === "image" ? "图片预览" : "Markdown 预览"}
                    </Space>
                  ),
                  children: (
                    selectedProject.project_type === "image" ? (
                      <div className="projectImagePreview" aria-live="polite">
                        {previewLoading && <Spin size="large" tip="正在加载图片"><div className="projectImageLoading" /></Spin>}
                        {!previewLoading && previewError && (
                          <Alert
                            type="warning"
                            showIcon
                            message="图片暂时无法预览"
                            description={previewError}
                            action={<Button size="small" onClick={() => void openProject(selectedProject)}>重试</Button>}
                          />
                        )}
                        {!previewLoading && !previewError && previewFileUrl && (
                          <AntImage
                            className="projectPreviewImage"
                            src={previewFileUrl}
                            alt={`图片作品：${selectedProject.title}`}
                            onError={() => setPreviewError("云端图片加载失败，请稍后重试或使用下载按钮打开原图。")}
                            preview={{ mask: "点击放大" }}
                          />
                        )}
                        {!previewLoading && !previewError && !previewFileUrl && (
                          <EmptyState title="暂无可预览图片" description="作品文件可用后会在这里显示" />
                        )}
                      </div>
                    ) : (
                      <Space direction="vertical" size={16} className="fullWidth">
                        {selectedProject.project_type === "video" && selectedProject.file_path.startsWith("http") && (
                          <video className="generatedVideo" src={selectedProject.file_path} controls />
                        )}
                        <article className="markdownPreview">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {draftSummary || "这个作品还没有内容，可以切换到“编辑”开始填写。"}
                          </ReactMarkdown>
                        </article>
                      </Space>
                    )
                  )
                },
                ...(selectedProject.project_type === "image" ? [{
                  key: "description",
                  label: (
                    <Space>
                      <FileText size={16} />
                      作品说明
                    </Space>
                  ),
                  children: (
                    <article className="markdownPreview">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {draftSummary || "这个图片作品还没有说明，可以切换到“编辑”进行补充。"}
                      </ReactMarkdown>
                    </article>
                  )
                }] : []),
                ...(!archivedStudentReadOnly && selectedProject.lifecycle_status !== "trashed" ? [{
                  key: "edit",
                  label: (
                    <Space>
                      <Edit3 size={16} />
                      编辑
                    </Space>
                  ),
                  children: (
                    <Space direction="vertical" size={12} className="fullWidth">
                      <div>
                        <Text strong>作品标题</Text>
                        <Input className="mt8" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} />
                      </div>
                      <div>
                        <Text strong>Markdown 内容</Text>
                        <Input.TextArea
                          className="mt8 markdownEditor"
                          value={draftSummary}
                          onChange={(event) => setDraftSummary(event.target.value)}
                          placeholder={"# 我的AI作品\n\n在这里编辑文字作品内容，支持 Markdown。"}
                        />
                      </div>
                    </Space>
                  )
                }] : [])
              ]}
            />
          </Space>
        )}
      </Drawer>
    </div>
  );
}
