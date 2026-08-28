import {
  Alert, App as AntApp, Button, Card, Col, DatePicker, Drawer, Image as AntImage, Input, List, Popconfirm, Row, Segmented, Select, Space, Spin, Table, Tabs, Tag, Typography
} from "antd";
import type { TableColumnsType } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import {
  Archive, CircleCheckBig, Download, Edit3, Eye, FileText, Library, RotateCcw, Save, Shapes, Trash2, UsersRound
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { LiveMarkdownEditor } from "../../components/LiveMarkdownEditor";
import type { Classroom, Project, ProjectCategory } from "../../domain-types";
import { EmptyState } from "../../components/PageState";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { formatFileSize, projectTypeLabel } from "../../lib/domain";
import { explainBlobError, explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import type { StudentProfile } from "../../types";


const { Title, Text, Paragraph } = Typography;
type ProjectCategoryFilter = "all" | ProjectCategory;

const projectCategoryOf = (project: Project): ProjectCategory =>
  project.project_category || (project.curriculum_course_id ? "course_workspace" : "ai_generated");

const projectCategoryTag = (project: Project) => projectCategoryOf(project) === "course_workspace"
  ? <Tag color="cyan">{project.workspace_is_primary ? "主工程包" : "工程包副本"}</Tag>
  : <Tag color="purple">AI 生成</Tag>;

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
        {!compact && project.original_file_name && <Text>{project.original_file_name}</Text>}
      </Space>
    );
  }
  return (
    <Space wrap>
      <Tag color="green">文件正常</Tag>
      {!compact && project.original_file_name && <Text>{project.original_file_name}</Text>}
      {!compact && Boolean(project.file_size) && <Text type="secondary">{formatFileSize(project.file_size || 0)}</Text>}
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
  const [categoryFilter, setCategoryFilter] = useState<ProjectCategoryFilter>("all");
  const [studentFilter, setStudentFilter] = useState<number>();
  const [classroomFilter, setClassroomFilter] = useState<number>();
  const [submittedRange, setSubmittedRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [previewFileUrl, setPreviewFileUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewObjectUrlRef = useRef("");
  const projectRequestRef = useRef<AbortController | null>(null);
  const [projectScope, setProjectScope] = useState<"active" | "archived" | "trash">("active");
  const [libraryProjects, setLibraryProjects] = useState<Project[]>(projects);
  const [libraryTotal, setLibraryTotal] = useState(projects.length);
  const [libraryLoading, setLibraryLoading] = useState(false);
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

  const loadProjectScope = async (scope: "active" | "archived" | "trash", signal?: AbortSignal) => {
    const res = await api.get("/api/projects", {
      signal,
      params: {
        scope,
        ...(isTeacherView && searchText.trim() ? { q: searchText.trim() } : {}),
        ...(isTeacherView && typeFilter !== "all" ? { project_type: typeFilter } : {}),
        ...(categoryFilter !== "all" ? { project_category: categoryFilter } : {}),
        ...(isTeacherView && studentFilter ? { student_id: studentFilter } : {}),
        ...(isTeacherView && classroomFilter ? { classroom_id: classroomFilter } : {}),
        ...(isTeacherView && submittedRange?.[0]
          ? { submitted_from: submittedRange[0].startOf("day").format() }
          : {}),
        ...(isTeacherView && submittedRange?.[1]
          ? { submitted_to: submittedRange[1].endOf("day").format() }
          : {}),
      },
    });
    if (signal?.aborted) return;
    setLibraryProjects(res.data.projects || []);
    setLibraryTotal(Number(res.data.total ?? res.data.projects?.length ?? 0));
  };

  const refreshLibrary = async () => {
    await onRefresh();
    if (isTeacherView || projectScope !== "active") await loadProjectScope(projectScope);
  };

  useEffect(() => {
    if (!isTeacherView && projectScope === "active") {
      setLibraryProjects(projects);
      setLibraryTotal(projects.length);
    }
  }, [isTeacherView, projectScope, projects]);

  useEffect(() => {
    clearPreviewFile();
    setSelectedProject(null);
    projectRequestRef.current?.abort();
    const controller = new AbortController();
    projectRequestRef.current = controller;
    if (!isTeacherView && projectScope === "active") return () => controller.abort();
    const timer = window.setTimeout(() => {
      setLibraryLoading(true);
      void loadProjectScope(projectScope, controller.signal)
        .catch((error) => {
          if (!controller.signal.aborted) message.error(`作品筛选失败：${explainError(error)}`);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLibraryLoading(false);
        });
    }, isTeacherView ? 300 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [categoryFilter, classroomFilter, isTeacherView, projectScope, searchText, studentFilter, submittedRange, typeFilter]);

  useEffect(() => () => {
    projectRequestRef.current?.abort();
    if (previewObjectUrlRef.current) URL.revokeObjectURL(previewObjectUrlRef.current);
  }, []);

  const projectTypes = useMemo(
    () => Array.from(new Set([...projects, ...libraryProjects].map((project) => project.project_type))).filter(Boolean),
    [libraryProjects, projects],
  );
  const filteredProjects = useMemo(() => {
    const keyword = searchText.trim().toLowerCase();
    return libraryProjects.filter((project) => {
      const student = students.find((item) => item.id === project.user_id);
      const matchesKeyword =
        !keyword ||
        project.title.toLowerCase().includes(keyword) ||
        project.owner_name.toLowerCase().includes(keyword) ||
        project.summary.toLowerCase().includes(keyword) ||
        student?.username.toLowerCase().includes(keyword);
      const matchesType = typeFilter === "all" || project.project_type === typeFilter;
      const matchesCategory = categoryFilter === "all" || projectCategoryOf(project) === categoryFilter;
      const matchesStudent = !studentFilter || project.user_id === studentFilter;
      const matchesClassroom = !classroomFilter || project.classroom_id === classroomFilter;
      const submittedAt = project.latest_submitted_at ? dayjs(project.latest_submitted_at) : null;
      const matchesSubmittedFrom = !submittedRange?.[0] || Boolean(submittedAt?.isAfter(submittedRange[0].startOf("day")) || submittedAt?.isSame(submittedRange[0].startOf("day")));
      const matchesSubmittedTo = !submittedRange?.[1] || Boolean(submittedAt?.isBefore(submittedRange[1].endOf("day")) || submittedAt?.isSame(submittedRange[1].endOf("day")));
      return matchesKeyword && matchesType && matchesCategory && matchesStudent && matchesClassroom && matchesSubmittedFrom && matchesSubmittedTo;
    });
  }, [categoryFilter, classroomFilter, libraryProjects, searchText, studentFilter, students, submittedRange, typeFilter]);

  const emptyProjectDescription = categoryFilter === "course_workspace"
    ? "在课程学习中保存工程包后，会显示在这里"
    : categoryFilter === "ai_generated"
      ? "使用文字、图片、视频或工作流工具生成内容后，会显示在这里"
      : "保存工程包或完成一次 AI 创作后，作品会显示在这里";

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

  const projectDownloadName = (project: Project) => {
    const sourceName = project.original_file_name || project.file_path.split(/[?#]/, 1)[0];
    const extension = sourceName.match(/\.[A-Za-z0-9]{1,10}$/)?.[0] || (project.project_type === "image" ? ".png" : "");
    const safeTitle = project.title.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || `project-${project.id}`;
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

  const downloadProjectFile = async () => {
    if (!selectedProject) return;
    if (!(["ok", "remote"] as string[]).includes(selectedProject.file_status)) {
      message.warning("当前作品没有可下载的文件");
      return;
    }
    setDownloading(true);
    try {
      if (selectedProject.file_status === "remote") {
        triggerDownload(selectedProject.file_path, projectDownloadName(selectedProject), true);
      } else {
        const response = await api.get(`/api/projects/${selectedProject.id}/file`, { responseType: "blob" });
        if (await saveBlobFile(response.data, projectDownloadName(selectedProject))) {
          message.success(selectedProject.project_type === "image" ? "原图已保存" : "原文件已保存");
        }
      }
    } catch (error) {
      const subject = selectedProject.project_type === "image" ? "图片" : "文件";
      message.error(`${subject}下载失败：${await explainBlobError(error)}`);
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
      {selectedProject.file_status !== "none" && (
        <Button
          icon={<Download size={16} />}
          loading={downloading}
          disabled={!(["ok", "remote"] as string[]).includes(selectedProject.file_status)}
          onClick={() => void downloadProjectFile()}
        >{selectedProject.project_type === "image" ? "下载原图" : "下载原文件"}</Button>
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

  const projectColumns: TableColumnsType<Project> = [
    {
      title: "作品",
      key: "project",
      width: 260,
      render: (_, project) => (
        <div className="projectLedgerIdentity">
          <Text strong ellipsis={{ tooltip: project.title }}>{project.title}</Text>
          <Text type="secondary" ellipsis={{ tooltip: project.summary || "暂无摘要" }}>{project.summary || "暂无摘要"}</Text>
        </div>
      ),
    },
    {
      title: "类型与状态",
      key: "state",
      width: 160,
      render: (_, project) => (
        <Space wrap size={[4, 4]}>
          {projectCategoryTag(project)}
          <Tag>{projectTypeLabel(project.project_type)}</Tag>
          {lifecycleLabel(project)}
          {moderationLabel(project)}
        </Space>
      ),
    },
    {
      title: "学员 / 班级",
      key: "owner",
      width: 150,
      render: (_, project) => (
        <div className="projectLedgerIdentity">
          <Text>{project.owner_name || "未归属学生"}</Text>
          <Text type="secondary">{classroomName(project.classroom_id)}</Text>
        </div>
      ),
    },
    {
      title: "最近提交",
      dataIndex: "latest_submitted_at",
      key: "submitted",
      width: 150,
      render: (value?: string | null) => value ? formatBeijingTime(value) : <Text type="secondary">尚未提交</Text>,
    },
    {
      title: "保存时间",
      key: "saved",
      width: 150,
      render: (_, project) => <Text type="secondary">{formatBeijingTime(project.created_at)}</Text>,
    },
    {
      title: "文件",
      key: "file",
      width: 110,
      render: (_, project) => <ProjectFileStatus project={project} compact />,
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_, project) => (
        <Button
          type="link"
          icon={<Eye size={15} />}
          onClick={(event) => {
            event.stopPropagation();
            void openProject(project);
          }}
        >预览</Button>
      ),
    },
  ];

  return (
    <div className={`page ${isTeacherView ? "projectLedgerPage" : ""}`}>
      <div className="pageTitle rowTitle">
        <div>
          <Title level={2}>{isTeacherView ? "作品管理" : "作品库"}</Title>
          <Text>
            {isTeacherView
              ? "查看学生保存的工程包与 AI 生成内容，支持按分类、班级、学生和作品类型筛选。"
              : "工程包保存内容与 AI 生成内容会分类保存在这里。"}
          </Text>
        </div>
        <Button icon={<Save size={16} />} onClick={refreshLibrary}>
          刷新
        </Button>
      </div>
      <div className="projectLibrarySwitchers">
        <Segmented
          className="projectScopeSwitcher"
          value={projectScope}
          onChange={(value) => setProjectScope(value as "active" | "archived" | "trash")}
          options={[
            { value: "active", label: <Space size={6}><Library size={15} />正常作品</Space> },
            { value: "archived", label: <Space size={6}><Archive size={15} />已归档</Space> },
            { value: "trash", label: <Space size={6}><Trash2 size={15} />回收站</Space> }
          ]}
        />
        <Segmented
          className="projectCategorySwitcher"
          aria-label="作品内容分类"
          value={categoryFilter}
          onChange={(value) => setCategoryFilter(value as ProjectCategoryFilter)}
          options={[
            { value: "all", label: "全部内容" },
            { value: "course_workspace", label: "工程包" },
            { value: "ai_generated", label: "AI 生成" },
          ]}
        />
      </div>
      {isTeacherView && (
        <div className="projectLedgerControls">
          <section className="teachingMetricStrip projectMetricStrip" aria-label="作品统计">
            <div className="teachingMetricItem metricBlue">
              <span className="teachingMetricIcon"><Library size={22} /></span>
              <span><Text type="secondary">当前分区</Text><strong>{libraryTotal}</strong></span>
            </div>
            <div className="teachingMetricItem metricGreen">
              <span className="teachingMetricIcon"><CircleCheckBig size={22} /></span>
              <span><Text type="secondary">当前筛选</Text><strong>{filteredProjects.length}</strong></span>
            </div>
            <div className="teachingMetricItem metricAmber">
              <span className="teachingMetricIcon"><UsersRound size={22} /></span>
              <span><Text type="secondary">学生数</Text><strong>{new Set(libraryProjects.map((project) => project.user_id).filter(Boolean)).size}</strong></span>
            </div>
            <div className="teachingMetricItem metricCoral">
              <span className="teachingMetricIcon"><Shapes size={22} /></span>
              <span><Text type="secondary">类型数</Text><strong>{projectTypes.length}</strong></span>
            </div>
          </section>
          <div className="projectFilterToolbar" aria-label="作品筛选">
            <div className="projectFilterSearch">
                <Input
                  allowClear
                  aria-label="搜索作品"
                  placeholder="搜索标题、学生或内容"
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                />
            </div>
            <div>
                <Select
                  className="fullWidth"
                  value={typeFilter}
                  onChange={setTypeFilter}
                  options={[
                    { value: "all", label: "全部类型" },
                    ...projectTypes.map((type) => ({ value: type, label: projectTypeLabel(type) }))
                  ]}
                />
            </div>
            <div>
                <Select
                  className="fullWidth"
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  aria-label="按学生筛选作品"
                  placeholder="全部学生"
                  value={studentFilter}
                  onChange={setStudentFilter}
                  options={students.map((student) => ({
                    value: student.id,
                    label: `${student.name} · ${student.username}`,
                  }))}
                />
            </div>
            <div>
                <Select
                  className="fullWidth"
                  showSearch
                  allowClear
                  optionFilterProp="label"
                  aria-label="按班级筛选作品"
                  placeholder="全部班级"
                  value={classroomFilter}
                  onChange={setClassroomFilter}
                  options={classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }))}
                />
            </div>
            <div className="projectFilterDate">
                <DatePicker.RangePicker
                  className="fullWidth"
                  value={submittedRange}
                  onChange={(dates) => setSubmittedRange(dates)}
                  placeholder={["提交开始日期", "提交结束日期"]}
                  allowClear
                />
            </div>
          </div>
        </div>
      )}
      {isTeacherView ? (
        <div className="projectLedgerTable">
          <Table
            rowKey="id"
            loading={libraryLoading}
            columns={projectColumns}
            dataSource={filteredProjects}
            scroll={{ x: 1060 }}
            pagination={{
              defaultPageSize: 10,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50],
              showTotal: (total) => `共 ${total} 件作品`,
            }}
            locale={{
              emptyText: projectScope === "archived"
                ? <EmptyState title="还没有归档作品" description="归档后的作品会显示在这里" />
                : projectScope === "trash"
                  ? <EmptyState title="回收站为空" description="移入回收站的作品会暂存在这里" />
                  : <EmptyState title="还没有作品" description={emptyProjectDescription} />
            }}
            onRow={(project) => ({
              className: "projectLedgerRow",
              onClick: () => void openProject(project),
            })}
          />
        </div>
      ) : (
        <List
          loading={libraryLoading}
          grid={{ gutter: 16, xs: 1, sm: 2, lg: 3 }}
          dataSource={filteredProjects}
          locale={{
            emptyText: projectScope === "archived"
              ? <EmptyState title="还没有归档作品" description="归档后的作品会显示在这里" />
              : projectScope === "trash"
                ? <EmptyState title="回收站为空" description="移入回收站的作品会暂存在这里" />
                : <EmptyState title="还没有作品" description={emptyProjectDescription} />
          }}
          renderItem={(project) => (
            <List.Item>
              <Card
                className="projectCard"
                title={<span className="projectCardTitle" title={project.title}>{project.title}</span>}
                extra={<Button type="text" icon={<Eye size={16} />} onClick={() => openProject(project)}>预览</Button>}
                onClick={() => openProject(project)}
              >
                <Space direction="vertical" size={8}>
                  <Space wrap>
                    {projectCategoryTag(project)}
                    <Tag>{projectTypeLabel(project.project_type)}</Tag>
                    {lifecycleLabel(project)}
                  </Space>
                  <Paragraph ellipsis={{ rows: 4 }}>{project.summary || "暂无摘要"}</Paragraph>
                  <Text type="secondary">{lifecycleTime(project)}</Text>
                  <ProjectFileStatus project={project} compact />
                </Space>
              </Card>
            </List.Item>
          )}
        />
      )}
      <Drawer
        title={<span className="projectDrawerTitle">{selectedProject ? selectedProject.title : "作品预览"}</span>}
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
                  {projectCategoryTag(selectedProject)}
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
                        <LiveMarkdownEditor
                          value={draftSummary}
                          onChange={setDraftSummary}
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
