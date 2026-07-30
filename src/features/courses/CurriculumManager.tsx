import {
  Alert,
  App,
  Button,
  Checkbox,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  Archive,
  ArrowDown,
  ArrowUp,
  Download,
  Eye,
  FileText,
  FileUp,
  Pencil,
  Plus,
  Presentation,
  RefreshCcw,
  Send,
  Trash2,
  UploadCloud,
  UserRoundCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  CourseMaterialKind,
  CourseMaterialState,
  CoursePackageItem,
  CurriculumCourseItem,
  SchoolStage,
} from "../../domain-types";
import { api } from "../../lib/api";
import { saveBlobFile } from "../../lib/downloads";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { ALL_SCHOOL_STAGES, SCHOOL_STAGE_OPTIONS, schoolStageLabel } from "../../lib/schoolStages";


const { Text, Title, Paragraph } = Typography;

type PackageFormValues = {
  title: string;
  description?: string;
  package_version?: string;
  author_user_id?: number;
  school_stages: SchoolStage[];
  cover_path?: string;
};

type CourseAuthorOption = {
  id: number;
  name: string;
  username: string;
  role: "teacher" | "admin";
  active: boolean;
};

type CourseFormValues = {
  title: string;
  description?: string;
  assignment_instructions?: string;
  tools?: string[];
  rubric: Array<{ criterion: string; max_score: number }>;
};

const materialIcons: Record<CourseMaterialKind, React.ReactNode> = {
  slides: <Presentation size={18} />,
  starter_markdown: <FileText size={18} />,
  result_markdown: <FileText size={18} />,
};

const materialAccept: Record<CourseMaterialKind, string> = {
  slides: ".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation",
  starter_markdown: ".md,text/markdown,text/plain",
  result_markdown: ".md,text/markdown,text/plain",
};

function packageStatus(status: CoursePackageItem["status"]) {
  if (status === "published") return { color: "green", label: "已发布" };
  if (status === "archived") return { color: "default", label: "已归档" };
  return { color: "gold", label: "草稿" };
}

function conversionStatus(material: CourseMaterialState) {
  if (material.missing) return { color: "default", label: "待补充" };
  if (material.kind !== "slides") return { color: "green", label: "可用" };
  if (material.conversion_status === "ready") return { color: "green", label: "预览就绪" };
  if (material.conversion_status === "failed") return { color: "red", label: "转换失败" };
  if (material.conversion_status === "processing") return { color: "blue", label: "正在转换" };
  return { color: "gold", label: "等待转换" };
}

export function CurriculumManager({
  audience,
  packages,
  onRefresh,
}: {
  audience: "admin" | "teacher";
  packages: CoursePackageItem[];
  onRefresh: () => Promise<void>;
}) {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const [packageEditor, setPackageEditor] = useState<CoursePackageItem | "new" | null>(null);
  const [courseEditor, setCourseEditor] = useState<CurriculumCourseItem | "new" | null>(null);
  const [packageForm] = Form.useForm<PackageFormValues>();
  const [courseForm] = Form.useForm<CourseFormValues>();
  const [busy, setBusy] = useState<string>("");
  const [converter, setConverter] = useState<{ available: boolean; message: string } | null>(null);
  const [preview, setPreview] = useState<{ course: CurriculumCourseItem; kind: CourseMaterialKind } | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [permissionDrawerOpen, setPermissionDrawerOpen] = useState(false);
  const [authorOptions, setAuthorOptions] = useState<CourseAuthorOption[]>([]);
  const [authorOptionsLoading, setAuthorOptionsLoading] = useState(false);
  const [teacherOptions, setTeacherOptions] = useState<Array<{ id: number; name: string; username: string }>>([]);
  const [teacherPermissions, setTeacherPermissions] = useState<number[]>([]);
  const [teacherOptionsLoading, setTeacherOptionsLoading] = useState(false);

  const packageQuery = searchParams.get("package");
  const selectedPackageId = packageQuery && /^\d+$/.test(packageQuery) ? Number(packageQuery) : null;
  const selectedPackage = useMemo(
    () => packages.find((item) => item.id === selectedPackageId) || null,
    [packages, selectedPackageId],
  );

  useEffect(() => {
    if (selectedPackageId !== null && !packages.some((item) => item.id === selectedPackageId)) {
      const next = new URLSearchParams(searchParams);
      next.delete("package");
      setSearchParams(next, { replace: true });
    }
  }, [packages, searchParams, selectedPackageId, setSearchParams]);

  useEffect(() => {
    if (audience !== "admin") return;
    api.get("/api/curriculum/converter-status")
      .then((response) => setConverter(response.data.converter))
      .catch(() => setConverter(null));
  }, [audience]);

  useEffect(() => {
    if (audience !== "admin") return;
    setAuthorOptionsLoading(true);
    api.get("/api/course-authors/options")
      .then((response) => setAuthorOptions(response.data.authors || []))
      .catch((error) => message.error(explainError(error)))
      .finally(() => setAuthorOptionsLoading(false));
  }, [audience, message]);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const selectPackage = (packageId: number | null, replace = false) => {
    const next = new URLSearchParams(searchParams);
    if (packageId === null) next.delete("package");
    else next.set("package", String(packageId));
    setSearchParams(next, { replace });
  };

  const run = async (key: string, action: () => Promise<void>, success: string) => {
    setBusy(key);
    try {
      await action();
      message.success(success);
      await onRefresh();
      return true;
    } catch (error) {
      message.error(explainError(error));
      return false;
    } finally {
      setBusy("");
    }
  };

  const openPackageEditor = (item: CoursePackageItem | "new") => {
    setPackageEditor(item);
    packageForm.setFieldsValue(item === "new" ? {
      title: "",
      description: "",
      package_version: "1.0.0",
      author_user_id: undefined,
      school_stages: [...ALL_SCHOOL_STAGES],
      cover_path: "",
    } : {
      title: item.title,
      description: item.description,
      package_version: item.package_version,
      author_user_id: item.author_user_id ?? undefined,
      school_stages: item.school_stages,
      cover_path: item.cover_path,
    });
  };

  const savePackage = async (values: PackageFormValues) => {
    let packageIdAfterRefresh: number | null = null;
    const saved = await run("package-save", async () => {
      const payload = {
        title: values.title.trim(),
        description: values.description?.trim() || "",
        package_version: values.package_version?.trim() || "1.0.0",
        author_user_id: values.author_user_id,
        school_stages: values.school_stages,
        cover_path: values.cover_path?.trim() || "",
      };
      if (packageEditor === "new") {
        const response = await api.post("/api/course-packages", payload);
        packageIdAfterRefresh = response.data.package.id;
      } else if (packageEditor) {
        await api.put(`/api/course-packages/${packageEditor.id}`, payload);
      }
      setPackageEditor(null);
    }, packageEditor === "new" ? "课程包已创建" : "课程包已更新");
    if (saved && packageIdAfterRefresh !== null) {
      selectPackage(packageIdAfterRefresh);
    }
  };

  const openCourseEditor = (item: CurriculumCourseItem | "new") => {
    setCourseEditor(item);
    courseForm.setFieldsValue(item === "new" ? {
      title: "",
      description: "",
      assignment_instructions: "",
      tools: ["text", "image", "workflow"],
      rubric: [{ criterion: "完成度", max_score: 100 }],
    } : {
      title: item.title,
      description: item.description,
      assignment_instructions: item.assignment_instructions,
      tools: item.tool_scope.split(",").filter(Boolean),
      rubric: item.rubric,
    });
  };

  const saveCourse = async (values: CourseFormValues) => {
    if (!selectedPackage) return;
    await run("course-save", async () => {
      const payload = {
        title: values.title.trim(),
        description: values.description?.trim() || "",
        order_index: courseEditor === "new" ? selectedPackage.courses.length : courseEditor?.order_index || 0,
        assignment_instructions: values.assignment_instructions?.trim() || "",
        tool_scope: (values.tools || []).join(","),
        rubric: values.rubric,
      };
      if (courseEditor === "new") {
        await api.post(`/api/course-packages/${selectedPackage.id}/courses`, payload);
      } else if (courseEditor) {
        await api.put(`/api/curriculum-courses/${courseEditor.id}`, payload);
      }
      setCourseEditor(null);
    }, courseEditor === "new" ? "课程已添加" : "课程已更新");
  };

  const uploadMaterial = async (course: CurriculumCourseItem, kind: CourseMaterialKind, file: File) => {
    await run(`material-${course.id}-${kind}`, async () => {
      const formData = new FormData();
      formData.append("file", file);
      await api.put(`/api/curriculum-courses/${course.id}/materials/${kind}`, formData);
    }, kind === "slides" ? "PPT 已上传，正在生成预览" : "课程资料已上传");
  };

  const closePreview = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl("");
    setPreviewText("");
    setPreview(null);
  };

  const openPreview = async (course: CurriculumCourseItem, kind: CourseMaterialKind) => {
    setPreview({ course, kind });
    setPreviewLoading(true);
    setPreviewText("");
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl("");
    try {
      if (kind === "slides") {
        const response = await api.get(`/api/curriculum-courses/${course.id}/materials/${kind}/preview`, { responseType: "blob" });
        const pdf = new Blob([response.data], { type: "application/pdf" });
        setPreviewUrl(URL.createObjectURL(pdf));
      } else {
        const response = await api.get(`/api/curriculum-courses/${course.id}/materials/${kind}/preview`, { responseType: "text" });
        setPreviewText(String(response.data || ""));
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setPreviewLoading(false);
    }
  };

  const downloadMaterial = async (course: CurriculumCourseItem, material: CourseMaterialState) => {
    setBusy(`download-${course.id}-${material.kind}`);
    try {
      const response = await api.get(`/api/curriculum-courses/${course.id}/materials/${material.kind}/download`, { responseType: "blob" });
      if (await saveBlobFile(response.data, material.original_name || `${material.label}.md`)) {
        message.success("课程资料已保存");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const reorderCourse = async (course: CurriculumCourseItem, direction: -1 | 1) => {
    if (!selectedPackage) return;
    const ids = [...selectedPackage.courses]
      .sort((left, right) => left.order_index - right.order_index)
      .map((item) => item.id);
    const index = ids.indexOf(course.id);
    const next = index + direction;
    if (index < 0 || next < 0 || next >= ids.length) return;
    [ids[index], ids[next]] = [ids[next], ids[index]];
    await run(`order-${course.id}`, () => api.put(`/api/course-packages/${selectedPackage.id}/courses/order`, { course_ids: ids }).then(() => undefined), "课程顺序已调整");
  };

  const exportPackage = async () => {
    if (!selectedPackage) return;
    setBusy("package-export");
    try {
      const response = await api.get(`/api/course-packages/${selectedPackage.id}/export`, { responseType: "blob" });
      if (await saveBlobFile(response.data, `coderai-course-package-${selectedPackage.id}.zip`)) {
        message.success("课程包已导出");
      }
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setBusy("");
    }
  };

  const importPackage = async (file: File) => {
    let packageIdAfterRefresh: number | null = null;
    const imported = await run("package-import", async () => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post("/api/course-packages/import", formData);
      packageIdAfterRefresh = response.data.package.id;
    }, "课程包已导入为草稿");
    if (imported && packageIdAfterRefresh !== null) {
      selectPackage(packageIdAfterRefresh);
    }
  };

  const openTeacherPermissions = async () => {
    if (!selectedPackage) return;
    setTeacherPermissions(selectedPackage.teacher_ids || []);
    setPermissionDrawerOpen(true);
    setTeacherOptionsLoading(true);
    try {
      const response = await api.get("/api/teachers/options");
      setTeacherOptions(response.data.teachers || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setTeacherOptionsLoading(false);
    }
  };

  const saveTeacherPermissions = async () => {
    if (!selectedPackage) return;
    const saved = await run(
      "teacher-permissions",
      () => api.put(`/api/course-packages/${selectedPackage.id}/teachers`, { teacher_ids: teacherPermissions }).then(() => undefined),
      "教师课程权限已更新",
    );
    if (saved) setPermissionDrawerOpen(false);
  };

  const deleteSelectedPackage = async () => {
    if (!selectedPackage) return;
    const deleted = await run(
      "delete-package",
      () => api.delete(`/api/course-packages/${selectedPackage.id}`).then(() => undefined),
      "课程包已删除",
    );
    if (deleted) selectPackage(null, true);
  };

  return (
    <div className="curriculumWorkspace">
      {audience === "admin" && converter && (
        <Alert
          className="mb16"
          showIcon
          type={converter.available ? "success" : "warning"}
          message={converter.available ? "PPT 预览转换服务可用" : "PPT 预览转换服务未配置"}
          description={converter.message}
        />
      )}

      {!selectedPackage && (
        <div className="curriculumToolbar">
          <Space wrap>
            <Text strong>{audience === "admin" ? "课程包管理" : "课程概览"}</Text>
            <Tag>{packages.length} 个</Tag>
          </Space>
          <Space wrap>
            <Button icon={<RefreshCcw size={15} />} loading={busy === "refresh"} onClick={() => void onRefresh()}>刷新</Button>
            {audience === "admin" && (
              <>
                <Upload accept=".zip,application/zip" showUploadList={false} beforeUpload={(file) => { void importPackage(file); return Upload.LIST_IGNORE; }}>
                  <Button icon={<UploadCloud size={15} />} loading={busy === "package-import"}>导入 ZIP</Button>
                </Upload>
                <Button type="primary" icon={<Plus size={16} />} onClick={() => openPackageEditor("new")}>新建课程包</Button>
              </>
            )}
          </Space>
        </div>
      )}

      {!selectedPackage ? (
        <section className="curriculumOverview" aria-label="课程概览">
          <div className="curriculumOverviewGrid">
            <div><Text type="secondary">课程包</Text><Text strong>{packages.length}</Text></div>
            <div><Text type="secondary">已发布</Text><Text strong>{packages.filter((item) => item.status === "published").length}</Text></div>
            <div><Text type="secondary">课程总数</Text><Text strong>{packages.reduce((sum, item) => sum + item.course_count, 0)}</Text></div>
            {audience === "admin" && <div><Text type="secondary">尚未授权教师</Text><Text strong>{packages.filter((item) => item.assignment_status === "unassigned").length}</Text></div>}
          </div>
          {!packages.length && <Empty description={audience === "admin" ? "暂无课程包" : "当前没有已授权课程包"} />}
          {audience === "admin" && packages.some((item) => item.assignment_status === "unassigned") && (
            <Alert type="warning" showIcon message={`${packages.filter((item) => item.assignment_status === "unassigned").length} 个课程包尚未授权教师`} />
          )}
        </section>
      ) : (
        <div className="curriculumDetail">
          {selectedPackage && (
            <section>
              <div className="curriculumPackageHeader">
                <div>
                  <Space wrap>
                    <Title level={3}>{selectedPackage.title}</Title>
                    <Tag color={packageStatus(selectedPackage.status).color}>{packageStatus(selectedPackage.status).label}</Tag>
                    {audience === "admin" && (
                      <Tag color={selectedPackage.assignment_status === "assigned" ? "blue" : "orange"}>
                        {selectedPackage.assignment_status === "assigned" ? `已授权 ${selectedPackage.teacher_ids.length} 名教师` : "尚未授权教师"}
                      </Tag>
                    )}
                  </Space>
                  <Paragraph className="preWrapText" type="secondary">{selectedPackage.description || "暂未填写课程包说明。"}</Paragraph>
                </div>
                <Space wrap>
                  <Button icon={<RefreshCcw size={15} />} loading={busy === "refresh"} onClick={() => void onRefresh()}>刷新</Button>
                  {audience === "admin" && (
                    <>
                    <Button icon={<UserRoundCheck size={15} />} onClick={() => void openTeacherPermissions()}>教师权限</Button>
                    <Button icon={<Pencil size={15} />} onClick={() => openPackageEditor(selectedPackage)}>编辑</Button>
                    <Button icon={<Download size={15} />} loading={busy === "package-export"} onClick={() => void exportPackage()}>导出</Button>
                    {selectedPackage.status !== "published" && selectedPackage.status !== "archived" && (
                      <Button type="primary" icon={<Send size={15} />} disabled={!selectedPackage.courses.length} loading={busy === "publish"} onClick={() => void run("publish", () => api.post(`/api/course-packages/${selectedPackage.id}/publish`).then(() => undefined), "课程包已发布")}>发布</Button>
                    )}
                    {selectedPackage.status === "published" && (
                      <Popconfirm title="归档课程包？" description="现有排课和历史提交会继续保留。" onConfirm={() => run("archive", () => api.post(`/api/course-packages/${selectedPackage.id}/archive`).then(() => undefined), "课程包已归档")}>
                        <Button icon={<Archive size={15} />}>归档</Button>
                      </Popconfirm>
                    )}
                    {selectedPackage.status === "draft" && (
                      <Popconfirm title="删除草稿课程包？" onConfirm={deleteSelectedPackage}>
                        <Button danger icon={<Trash2 size={15} />} />
                      </Popconfirm>
                    )}
                    </>
                  )}
                </Space>
              </div>

              <Descriptions size="small" column={{ xs: 1, md: 3 }} className="curriculumMeta">
                <Descriptions.Item label="版本">{selectedPackage.package_version}</Descriptions.Item>
                <Descriptions.Item label="作者">
                  {selectedPackage.author_account ? (
                    <Space size={6} wrap>
                      <Text>{selectedPackage.author_account.name}</Text>
                      <Tag color={selectedPackage.author_account.role === "admin" ? "purple" : "blue"}>
                        {selectedPackage.author_account.role === "admin" ? "管理员" : "教师"}
                      </Tag>
                      {!selectedPackage.author_account.active && <Tag>已停用</Tag>}
                    </Space>
                  ) : selectedPackage.author || "未指定"}
                </Descriptions.Item>
                <Descriptions.Item label="适用学龄">
                  <Space size={[4, 4]} wrap>
                    {selectedPackage.school_stages.map((stage) => <Tag key={stage}>{schoolStageLabel(stage)}</Tag>)}
                  </Space>
                </Descriptions.Item>
              </Descriptions>

              {audience === "admin" && (
                <div className="curriculumTeacherAccess">
                  <Text type="secondary">可见教师</Text>
                  <Space wrap>
                    {selectedPackage.teachers.length
                      ? selectedPackage.teachers.map((teacher) => <Tag key={teacher.id}>{teacher.name} · {teacher.username}</Tag>)
                      : <Text type="warning">尚未授权教师</Text>}
                  </Space>
                </div>
              )}

              <div className="curriculumSectionHeading">
                <div><Title level={4}>课程内容</Title><Text type="secondary">资料槽位可在发布前后随时补充</Text></div>
                {audience === "admin" && selectedPackage.status !== "archived" && <Button icon={<Plus size={15} />} onClick={() => openCourseEditor("new")}>添加课程</Button>}
              </div>

              {!selectedPackage.courses.length ? (
                <Alert type="info" showIcon message="课程包还没有课程" description="添加至少一门课程后即可发布；PPT、工程包和成果包均可暂时留空。" />
              ) : (
                <Space direction="vertical" size={12} className="fullWidth">
                  {[...selectedPackage.courses].sort((left, right) => left.order_index - right.order_index).map((course, courseIndex) => (
                    <article className="curriculumCourse" key={course.id}>
                      <div className="curriculumCourseHeader">
                        <div>
                          <Space wrap><Tag color="blue">第 {courseIndex + 1} 课</Tag><Title level={4}>{course.title}</Title></Space>
                          <Text className="preWrapText" type="secondary">{course.description || "暂未填写课程简介。"}</Text>
                        </div>
                        {audience === "admin" && (
                          <Space>
                            <Button aria-label="上移课程" icon={<ArrowUp size={15} />} disabled={courseIndex === 0} loading={busy === `order-${course.id}`} onClick={() => void reorderCourse(course, -1)} />
                            <Button aria-label="下移课程" icon={<ArrowDown size={15} />} disabled={courseIndex === selectedPackage.courses.length - 1} loading={busy === `order-${course.id}`} onClick={() => void reorderCourse(course, 1)} />
                            <Button icon={<Pencil size={15} />} onClick={() => openCourseEditor(course)}>编辑</Button>
                            <Popconfirm title="删除课程？" description="已产生排课的课程不能删除。" onConfirm={() => run(`delete-course-${course.id}`, () => api.delete(`/api/curriculum-courses/${course.id}`).then(() => undefined), "课程已删除")}>
                              <Button danger aria-label="删除课程" icon={<Trash2 size={15} />} />
                            </Popconfirm>
                          </Space>
                        )}
                      </div>

                      <div className="curriculumRuleStrip">
                        <div><Text type="secondary">作业说明</Text><Paragraph className="preWrapText">{course.assignment_instructions || "管理员暂未补充"}</Paragraph></div>
                        <div><Text type="secondary">开放工具</Text><Space wrap>{course.tool_scope ? course.tool_scope.split(",").map((tool) => <Tag key={tool}>{tool}</Tag>) : <Tag>不开放 AI 工具</Tag>}</Space></div>
                        <div><Text type="secondary">评分</Text><Text>{course.rubric.reduce((sum, item) => sum + item.max_score, 0)} 分</Text></div>
                      </div>

                      <div className="materialGrid">
                        {(Object.keys(course.materials) as CourseMaterialKind[]).map((kind) => {
                          const material = course.materials[kind];
                          const state = conversionStatus(material);
                          return (
                            <section className="materialSlot" key={kind}>
                              <div className="materialSlotTitle">
                                <Space>{materialIcons[kind]}<Text strong>{material.label}</Text></Space>
                                <Tag color={state.color}>{state.label}</Tag>
                              </div>
                              <Text type="secondary" ellipsis title={material.original_name || undefined}>{material.missing ? "管理员暂未补充" : material.original_name}</Text>
                              {material.updated_at && <Text type="secondary">更新于 {formatBeijingTime(material.updated_at)}</Text>}
                              {audience === "admin" && material.conversion_error && <Text type="danger">{material.conversion_error}</Text>}
                              <Space wrap className="materialActions">
                                {material.can_preview && <Button size="small" icon={<Eye size={14} />} onClick={() => void openPreview(course, kind)}>预览</Button>}
                                {material.can_download && <Button size="small" icon={<Download size={14} />} loading={busy === `download-${course.id}-${kind}`} onClick={() => void downloadMaterial(course, material)}>下载原件</Button>}
                                {audience === "admin" && (
                                  <Upload accept={materialAccept[kind]} showUploadList={false} beforeUpload={(file) => { void uploadMaterial(course, kind, file); return Upload.LIST_IGNORE; }}>
                                    <Button size="small" icon={<FileUp size={14} />} loading={busy === `material-${course.id}-${kind}`}>{material.missing ? "上传" : "替换"}</Button>
                                  </Upload>
                                )}
                                {audience === "admin" && kind === "slides" && material.conversion_status === "failed" && (
                                  <Button size="small" icon={<RefreshCcw size={14} />} onClick={() => void run(`retry-${course.id}`, () => api.post(`/api/curriculum-courses/${course.id}/materials/slides/retry`).then(() => undefined), "已重新提交转换")}>重试</Button>
                                )}
                                {audience === "admin" && !material.missing && (
                                  <Popconfirm title={`删除${material.label}？`} onConfirm={() => run(`material-delete-${course.id}-${kind}`, () => api.delete(`/api/curriculum-courses/${course.id}/materials/${kind}`).then(() => undefined), "资料已删除")}>
                                    <Button size="small" danger aria-label={`删除${material.label}`} icon={<Trash2 size={14} />} />
                                  </Popconfirm>
                                )}
                              </Space>
                            </section>
                          );
                        })}
                      </div>
                    </article>
                  ))}
                </Space>
              )}
            </section>
          )}
        </div>
      )}

      <Modal
        title={packageEditor === "new" ? "新建课程包" : "编辑课程包"}
        open={Boolean(packageEditor)}
        onCancel={() => setPackageEditor(null)}
        footer={null}
        destroyOnClose
      >
        <Form form={packageForm} layout="vertical" onFinish={savePackage}>
          <Form.Item name="title" label="课程包名称" rules={[{ required: true, message: "请输入课程包名称" }]}><Input maxLength={160} /></Form.Item>
          <Form.Item name="description" label="课程包说明"><Input.TextArea rows={3} maxLength={20000} /></Form.Item>
          <div className="formGridTwo">
            <Form.Item name="package_version" label="版本"><Input maxLength={40} /></Form.Item>
            <Form.Item name="author_user_id" label="课程包作者" rules={[{ required: true, message: "请选择教师或管理员" }]}>
              <Select
                showSearch
                loading={authorOptionsLoading}
                optionFilterProp="label"
                placeholder="选择教师或管理员"
                options={authorOptions.map((author) => ({
                  value: author.id,
                  label: `${author.name} · ${author.username} · ${author.role === "admin" ? "管理员" : "教师"}${author.active ? "" : " · 已停用"}`,
                  disabled: !author.active && author.id !== packageForm.getFieldValue("author_user_id"),
                }))}
              />
            </Form.Item>
          </div>
          <Form.Item
            name="school_stages"
            label="适用学龄"
            rules={[{ required: true, type: "array", min: 1, message: "请至少选择一个学龄分类" }]}
          >
            <Select mode="multiple" options={SCHOOL_STAGE_OPTIONS} placeholder="选择课程包适用的学龄分类" />
          </Form.Item>
          <Form.Item name="cover_path" label="封面地址（可选）"><Input maxLength={2000} placeholder="HTTPS 地址或受管素材路径" /></Form.Item>
          <Button type="primary" htmlType="submit" loading={busy === "package-save"} block>保存课程包</Button>
        </Form>
      </Modal>

      <Modal
        title={courseEditor === "new" ? "添加课程" : "编辑课程"}
        open={Boolean(courseEditor)}
        onCancel={() => setCourseEditor(null)}
        footer={null}
        width={720}
        destroyOnClose
      >
        <Form form={courseForm} layout="vertical" onFinish={saveCourse}>
          <Form.Item name="title" label="课程名称" rules={[{ required: true, message: "请输入课程名称" }]}><Input maxLength={160} /></Form.Item>
          <Form.Item name="description" label="课程简介"><Input.TextArea rows={2} maxLength={20000} /></Form.Item>
          <Form.Item name="assignment_instructions" label="提交说明"><Input.TextArea rows={3} maxLength={20000} placeholder="学生提交作品时看到的要求" /></Form.Item>
          <Form.Item name="tools" label="允许使用的 AI 工具">
            <Checkbox.Group options={[{ label: "文字生成", value: "text" }, { label: "图片生成", value: "image" }, { label: "视频生成", value: "video" }, { label: "工作流", value: "workflow" }]} />
          </Form.Item>
          <Divider orientation="left">评分规则</Divider>
          <Form.List name="rubric">
            {(fields, { add, remove }) => (
              <Space direction="vertical" className="fullWidth">
                {fields.map((field) => (
                  <div className="rubricRow" key={field.key}>
                    <Form.Item {...field} name={[field.name, "criterion"]} rules={[{ required: true, message: "填写评分项" }]}><Input placeholder="评分项" maxLength={80} /></Form.Item>
                    <Form.Item {...field} name={[field.name, "max_score"]} rules={[{ required: true, type: "number", min: 1, max: 100 }]}><Input type="number" min={1} max={100} suffix="分" /></Form.Item>
                    <Button aria-label="删除评分项" icon={<Trash2 size={15} />} disabled={fields.length === 1} onClick={() => remove(field.name)} />
                  </div>
                ))}
                <Button icon={<Plus size={15} />} disabled={fields.length >= 10} onClick={() => add({ criterion: "", max_score: 10 })}>添加评分项</Button>
              </Space>
            )}
          </Form.List>
          <Divider />
          <Button type="primary" htmlType="submit" loading={busy === "course-save"} block>保存课程</Button>
        </Form>
      </Modal>

      <Drawer
        title={selectedPackage ? `${selectedPackage.title} · 教师权限` : "教师权限"}
        open={permissionDrawerOpen}
        onClose={() => setPermissionDrawerOpen(false)}
        width={520}
        extra={<Button type="primary" loading={busy === "teacher-permissions"} onClick={() => void saveTeacherPermissions()}>保存权限</Button>}
      >
        <Space direction="vertical" size={16} className="fullWidth">
          <Alert
            type="info"
            showIcon
            message="未选中的教师无法查看课程内容或创建新排课；已有排课保留为历史记录。"
          />
          <Select
            mode="multiple"
            className="fullWidth"
            loading={teacherOptionsLoading}
            value={teacherPermissions}
            onChange={setTeacherPermissions}
            placeholder="选择可查看课程包的教师"
            optionFilterProp="label"
            options={teacherOptions.map((teacher) => ({
              value: teacher.id,
              label: `${teacher.name} · ${teacher.username}`,
            }))}
          />
          <Space wrap>
            <Button onClick={() => setTeacherPermissions(teacherOptions.map((teacher) => teacher.id))}>全选启用教师</Button>
            <Button onClick={() => setTeacherPermissions([])}>清空全部</Button>
          </Space>
        </Space>
      </Drawer>

      <Drawer
        title={preview ? `${preview.course.title} · ${preview.course.materials[preview.kind].label}` : "课程资料预览"}
        open={Boolean(preview)}
        onClose={closePreview}
        width={860}
      >
        {previewLoading && <Alert type="info" showIcon message="正在加载课程资料" />}
        {!previewLoading && preview?.kind === "slides" && previewUrl && <iframe className="coursePdfPreview" src={previewUrl} title="课堂 PPT PDF 预览" />}
        {!previewLoading && preview && preview.kind !== "slides" && (
          <article className="markdownPreview courseMarkdownPreview"><ReactMarkdown remarkPlugins={[remarkGfm]}>{previewText || "资料内容为空。"}</ReactMarkdown></article>
        )}
        {!previewLoading && preview && !previewUrl && !previewText && preview.kind === "slides" && <Alert type="warning" showIcon message="PPT 预览暂不可用" />}
      </Drawer>
    </div>
  );
}
