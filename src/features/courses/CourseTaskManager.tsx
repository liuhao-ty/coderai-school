import {
  Alert, App as AntApp, Button, Card, Col, DatePicker, Drawer, Form, Input, List, Popconfirm, Radio, Row, Segmented, Select, Space, Statistic, Tabs, Tag, Typography
} from "antd";
import {
  BookOpen, ClipboardList, Edit3, Eye, FileDown, FileUp, Image, Plus, RotateCcw, Save, Trash2, Video
} from "lucide-react";
import dayjs from "dayjs";
import { useEffect, useMemo, useRef, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { AssetItem, ClassTask, Classroom, Course, Lesson, Project } from "../../domain-types";
import { ProjectFileStatus } from "../projects/ProjectLibrary";
import { api } from "../../lib/api";
import { assetName, assetTypeLabel, coursePackagePayload, formatFileSize, taskPayload, toolScopeLabel } from "../../lib/domain";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Title, Text, Paragraph } = Typography;

export function CourseTaskManager({
  courses,
  lessons,
  assets,
  classTasks,
  classrooms,
  onRefresh
}: {
  courses: Course[];
  lessons: Lesson[];
  assets: AssetItem[];
  classTasks: ClassTask[];
  classrooms: Classroom[];
  onRefresh: () => Promise<void>;
}) {
  const { message } = AntApp.useApp();
  const [courseForm] = Form.useForm();
  const [editCourseForm] = Form.useForm();
  const [lessonForm] = Form.useForm();
  const [editLessonForm] = Form.useForm();
  const [assetForm] = Form.useForm();
  const [taskForm] = Form.useForm();
  const [editTaskForm] = Form.useForm();
  const [savingCourse, setSavingCourse] = useState(false);
  const [savingLesson, setSavingLesson] = useState(false);
  const [savingAsset, setSavingAsset] = useState(false);
  const [savingTask, setSavingTask] = useState(false);
  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [packageText, setPackageText] = useState("");
  const [packageLoading, setPackageLoading] = useState(false);
  const [packageConflictStrategy, setPackageConflictStrategy] = useState("rename");
  const [editingCourse, setEditingCourse] = useState<Course | null>(null);
  const [updatingCourse, setUpdatingCourse] = useState(false);
  const [editingLesson, setEditingLesson] = useState<Lesson | null>(null);
  const [updatingLesson, setUpdatingLesson] = useState(false);
  const [editingTask, setEditingTask] = useState<ClassTask | null>(null);
  const [updatingTask, setUpdatingTask] = useState(false);
  const [previewAsset, setPreviewAsset] = useState<AssetItem | null>(null);
  const [previewAssetUrl, setPreviewAssetUrl] = useState("");
  const [previewAssetLoading, setPreviewAssetLoading] = useState(false);

  const createCourse = async (values: Record<string, any>) => {
    setSavingCourse(true);
    try {
      await api.post("/api/courses/import", coursePackagePayload(values));
      courseForm.resetFields();
      await onRefresh();
      message.success("课程已创建");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingCourse(false);
    }
  };

  const createLesson = async (values: { course_id?: number; title: string; content: string; order_index: number }) => {
    setSavingLesson(true);
    try {
      await api.post("/api/lessons", {
        ...values,
        order_index: Number(values.order_index || 0)
      });
      lessonForm.resetFields();
      await onRefresh();
      message.success("课时已创建");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingLesson(false);
    }
  };

  const saveAsset = async (values: { asset_type: string; file_path?: string; display_name?: string; description?: string; tags?: string; classroom_id?: number; lesson_id?: number }) => {
    setSavingAsset(true);
    try {
      if (assetFile) {
        const formData = new FormData();
        formData.append("file", assetFile);
        formData.append("asset_type", values.asset_type || "document");
        formData.append("display_name", values.display_name || "");
        formData.append("description", values.description || "");
        formData.append("tags", values.tags || "");
        if (values.classroom_id) formData.append("classroom_id", String(values.classroom_id));
        if (values.lesson_id) formData.append("lesson_id", String(values.lesson_id));
        await api.post("/api/assets/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" }
        });
      } else {
        await api.post("/api/assets/register", {
          asset_type: values.asset_type || "document",
          file_path: values.file_path || "",
          display_name: values.display_name || "",
          description: values.description || "",
          tags: (values.tags || "").split(",").map((tag) => tag.trim()).filter(Boolean),
          classroom_id: values.classroom_id,
          lesson_id: values.lesson_id
        });
      }
      assetForm.resetFields();
      setAssetFile(null);
      await onRefresh();
      message.success("素材已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingAsset(false);
    }
  };

  const createTask = async (values: Record<string, any>) => {
    setSavingTask(true);
    try {
      await api.post("/api/classes/tasks", {
        ...taskPayload(values),
        tool_scope: values.tool_scope.join(",")
      });
      taskForm.resetFields();
      await onRefresh();
      message.success("课堂任务已发布");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingTask(false);
    }
  };

  const exportCoursePackage = async () => {
    setPackageLoading(true);
    try {
      const res = await api.get("/api/courses/export");
      setPackageText(JSON.stringify(res.data, null, 2));
      message.success("课程包已生成");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setPackageLoading(false);
    }
  };

  const importCoursePackage = async () => {
    setPackageLoading(true);
    try {
      const parsed = JSON.parse(packageText);
      if (Array.isArray(parsed.courses)) {
        for (const [index, course] of parsed.courses.entries()) {
          await api.post("/api/courses/import", {
            title: course.title,
            description: course.description || "",
            package_version: course.package_version || "1.0.0",
            author: course.author || "",
            age_range: course.age_range || "全年龄",
            cover_path: course.cover_path || "",
            dependencies: course.dependencies || [],
            checklist: course.checklist || [],
            conflict_strategy: packageConflictStrategy,
            status: "draft",
            lessons: Array.isArray(parsed.lessons)
              ? parsed.lessons
                  .filter((lesson: Lesson) => !lesson.course_id || lesson.course_id === course.id || index === 0)
                  .map((lesson: Lesson) => ({
                    title: lesson.title,
                    content: lesson.content || "",
                    order_index: lesson.order_index || 0
                  }))
              : [],
            tasks: Array.isArray(parsed.tasks)
              ? parsed.tasks.filter((task: ClassTask) => !task.course_id || task.course_id === course.id || index === 0).map((task: ClassTask) => ({
                  title: task.title,
                  instructions: task.instructions || "",
                  tool_scope: task.tool_scope || "text,image,workflow",
                  lesson_title: task.lesson_title || "",
                  rubric: task.rubric || [{ criterion: "完成度", max_score: 100 }]
                }))
              : []
          });
        }
      } else {
        await api.post("/api/courses/import", { ...parsed, conflict_strategy: packageConflictStrategy });
      }
      await onRefresh();
      message.success("课程包已导入");
    } catch (error) {
      message.error(error instanceof SyntaxError ? "课程包 JSON 格式不正确" : explainError(error));
    } finally {
      setPackageLoading(false);
    }
  };

  const openEditCourse = (course: Course) => {
    setEditingCourse(course);
    editCourseForm.setFieldsValue({
      title: course.title,
      description: course.description,
      package_version: course.package_version,
      author: course.author,
      age_range: course.age_range,
      cover_path: course.cover_path,
      dependencies_text: course.dependencies.map((item) => item.name).join("\n"),
      checklist_text: course.checklist.join("\n"),
      classroom_id: course.classroom_id ?? undefined,
      status: course.status,
      starts_at: course.starts_at ? dayjs(course.starts_at) : null,
      ends_at: course.ends_at ? dayjs(course.ends_at) : null
    });
  };

  const openEditLesson = (lesson: Lesson) => {
    setEditingLesson(lesson);
    editLessonForm.setFieldsValue({
      course_id: lesson.course_id ?? undefined,
      title: lesson.title,
      content: lesson.content,
      order_index: lesson.order_index
    });
  };

  const updateLesson = async (values: { course_id?: number; title: string; content: string; order_index: number }) => {
    if (!editingLesson) return;
    setUpdatingLesson(true);
    try {
      await api.put(`/api/lessons/${editingLesson.id}`, {
        ...values,
        order_index: Number(values.order_index || 0)
      });
      setEditingLesson(null);
      await onRefresh();
      message.success("课时已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUpdatingLesson(false);
    }
  };

  const updateCourse = async (values: Record<string, any>) => {
    if (!editingCourse) return;
    setUpdatingCourse(true);
    try {
      await api.put(`/api/courses/${editingCourse.id}`, coursePackagePayload(values));
      setEditingCourse(null);
      await onRefresh();
      message.success("课程已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUpdatingCourse(false);
    }
  };

  const deleteItem = async (kind: "courses" | "lessons" | "assets", id: number, successText: string) => {
    try {
      await api.delete(`/api/${kind}/${id}`);
      await onRefresh();
      message.success(successText);
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const openAssetPreview = async (asset: AssetItem) => {
    if (previewAssetUrl && !previewAssetUrl.startsWith("http")) URL.revokeObjectURL(previewAssetUrl);
    setPreviewAsset(asset);
    setPreviewAssetUrl("");
    if (asset.file_path.startsWith("http")) {
      setPreviewAssetUrl(asset.file_path);
      return;
    }
    setPreviewAssetLoading(true);
    try {
      const res = await api.get(`/api/assets/${asset.id}/file`, { responseType: "blob" });
      setPreviewAssetUrl(URL.createObjectURL(res.data));
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setPreviewAssetLoading(false);
    }
  };

  const closeAssetPreview = () => {
    if (previewAssetUrl && !previewAssetUrl.startsWith("http")) URL.revokeObjectURL(previewAssetUrl);
    setPreviewAssetUrl("");
    setPreviewAsset(null);
  };

  const openEditTask = (task: ClassTask) => {
    setEditingTask(task);
    editTaskForm.setFieldsValue({
      title: task.title,
      instructions: task.instructions,
      classroom_id: task.classroom_id ?? undefined,
      lesson_id: task.lesson_id ?? undefined,
      tool_scope: task.tool_scope
        .split(",")
        .map((scope) => scope.trim())
        .filter(Boolean),
      status: task.status,
      starts_at: task.starts_at ? dayjs(task.starts_at) : null,
      due_at: task.due_at ? dayjs(task.due_at) : null,
      rubric_text: task.rubric.map((item) => `${item.criterion}:${item.max_score}`).join("\n")
    });
  };

  const updateTask = async (values: Record<string, any>) => {
    if (!editingTask) return;
    setUpdatingTask(true);
    try {
      await api.put(`/api/classes/tasks/${editingTask.id}`, {
        ...taskPayload(values),
        tool_scope: values.tool_scope.join(",")
      });
      setEditingTask(null);
      await onRefresh();
      message.success("课堂任务已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setUpdatingTask(false);
    }
  };

  const deleteClassTask = async (taskId: number) => {
    try {
      const res = await api.delete(`/api/classes/tasks/${taskId}`);
      await onRefresh();
      const removed = Number(res.data.deleted_submissions || 0);
      message.success(removed ? `课堂任务已删除，同时清理 ${removed} 条提交记录` : "课堂任务已删除");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  return (
    <Card className="mt16" title={<IconTitle icon={<ClipboardList size={18} />} text="课程与课堂任务" />}>
      <Tabs
        items={[
          {
            key: "courses",
            label: "课程包",
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={10}>
                  <Form form={courseForm} layout="vertical" onFinish={createCourse} initialValues={{ status: "draft", package_version: "1.0.0", age_range: "8-16岁" }}>
                    <Form.Item name="title" label="课程名称" rules={[{ required: true, message: "请输入课程名称" }]}>
                      <Input placeholder="例如：AI绘画入门课" />
                    </Form.Item>
                    <Form.Item name="description" label="课程说明">
                      <Input.TextArea rows={4} placeholder="填写课程目标、适用学龄、课堂材料说明" />
                    </Form.Item>
                    <Row gutter={12}>
                      <Col span={12}><Form.Item name="package_version" label="课程版本" rules={[{ required: true }]}><Input placeholder="1.0.0" /></Form.Item></Col>
                      <Col span={12}><Form.Item name="author" label="作者 / 机构"><Input placeholder="课程设计者或机构" /></Form.Item></Col>
                    </Row>
                    <Form.Item name="age_range" label="适用学龄说明"><Input placeholder="例如：小学低龄、小学高龄" /></Form.Item>
                    <Form.Item name="cover_path" label="课程封面"><Input placeholder="HTTPS 图片链接或本地素材路径" /></Form.Item>
                    <Form.Item name="dependencies_text" label="依赖素材"><Input.TextArea rows={3} placeholder={"每行一个素材，例如：\n机器人参考图\n课堂示例项目"} /></Form.Item>
                    <Form.Item name="checklist_text" label="验收清单"><Input.TextArea rows={3} placeholder={"每行一项，例如：\n课时内容完整\n示例作品可打开"} /></Form.Item>
                    <Form.Item name="classroom_id" label="目标班级">
                      <Select
                        allowClear
                        placeholder="不选择则所有班级可见"
                        options={classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }))}
                      />
                    </Form.Item>
                    <Form.Item name="status" label="发布状态">
                      <Radio.Group optionType="button" buttonStyle="solid" options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }]} />
                    </Form.Item>
                    <Row gutter={12}>
                      <Col span={12}><Form.Item name="starts_at" label="开始时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
                      <Col span={12}><Form.Item name="ends_at" label="结束时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
                    </Row>
                    <Button icon={<Plus size={16} />} type="primary" htmlType="submit" loading={savingCourse}>
                      创建课程
                    </Button>
                  </Form>
                  <Card className="mt16" size="small" title="课程包导入导出">
                    <Space direction="vertical" size={12} className="fullWidth">
                      <Input.TextArea
                        rows={8}
                        value={packageText}
                        onChange={(event) => setPackageText(event.target.value)}
                        placeholder={'{"title":"AI绘画入门课","description":"课程说明","tasks":[{"title":"设计机器人海报","tool_scope":"text,image"}]}'}
                      />
                      <Select
                        value={packageConflictStrategy}
                        onChange={setPackageConflictStrategy}
                        options={[{ value: "rename", label: "同名时重命名导入" }, { value: "skip", label: "同名时跳过" }, { value: "replace", label: "同名时替换课程内容" }]}
                      />
                      <Space wrap>
                        <Button onClick={exportCoursePackage} loading={packageLoading}>
                          导出当前课程包
                        </Button>
                        <Button type="primary" onClick={importCoursePackage} loading={packageLoading} disabled={!packageText.trim()}>
                          导入课程包
                        </Button>
                      </Space>
                    </Space>
                  </Card>
                </Col>
                <Col xs={24} lg={14}>
                  <List
                    dataSource={courses}
                    locale={{ emptyText: "暂无课程" }}
                    renderItem={(course) => (
                      <List.Item
                        actions={[
                          <Button key="edit" size="small" onClick={() => openEditCourse(course)}>
                            编辑课程
                          </Button>,
                          <Popconfirm
                            key="delete"
                            title="删除课程及其全部课时？"
                            description="课堂任务不会删除，但会解除课时关联。"
                            okText="删除"
                            cancelText="取消"
                            onConfirm={() => deleteItem("courses", course.id, "课程已删除")}
                          >
                            <Button danger size="small" icon={<Trash2 size={15} />}>删除</Button>
                          </Popconfirm>
                        ]}
                      >
                        <List.Item.Meta
                          title={<Space wrap><Text strong>{course.title}</Text><Tag>v{course.package_version}</Tag><Tag>{course.age_range}</Tag><Tag color={course.status === "published" ? "green" : "default"}>{course.status === "published" ? "已发布" : "草稿"}</Tag>{course.classroom_name ? <Tag color="blue">{course.classroom_name}</Tag> : <Tag>全部班级</Tag>}</Space>}
                          description={
                            <Space direction="vertical" size={4}>
                              <Paragraph ellipsis={{ rows: 2 }}>{course.description || "暂无课程说明"}</Paragraph>
                              <Text type="secondary">作者：{course.author || "未填写"} · 依赖素材 {course.dependencies.length} 项 · 验收项 {course.checklist.length} 项</Text>
                              <Text type="secondary">开放：{course.starts_at ? formatBeijingTime(course.starts_at) : "立即"} · 结束：{course.ends_at ? formatBeijingTime(course.ends_at) : "不限"}</Text>
                              <Text type="secondary">{formatBeijingTime(course.created_at)}</Text>
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </Col>
              </Row>
            )
          },
          {
            key: "lessons",
            label: "课时内容",
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={10}>
                  <Form form={lessonForm} layout="vertical" onFinish={createLesson} initialValues={{ order_index: 0 }}>
                    <Form.Item name="course_id" label="所属课程">
                      <Select
                        allowClear
                        placeholder="选择课程"
                        options={courses.map((course) => ({ value: course.id, label: course.title }))}
                      />
                    </Form.Item>
                    <Form.Item name="title" label="课时标题" rules={[{ required: true, message: "请输入课时标题" }]}>
                      <Input placeholder="例如：第1课 认识AI绘画" />
                    </Form.Item>
                    <Form.Item name="order_index" label="排序">
                      <Input type="number" min={0} />
                    </Form.Item>
                    <Form.Item name="content" label="课时内容（Markdown）">
                      <Input.TextArea rows={10} placeholder="# 课时目标\n\n填写讲解内容、操作步骤和课堂练习。" />
                    </Form.Item>
                    <Button icon={<Plus size={16} />} type="primary" htmlType="submit" loading={savingLesson}>
                      创建课时
                    </Button>
                  </Form>
                </Col>
                <Col xs={24} lg={14}>
                  <List
                    dataSource={lessons}
                    locale={{ emptyText: "暂无课时" }}
                    renderItem={(lesson) => (
                      <List.Item
                        actions={[
                          <Button key="edit" size="small" onClick={() => openEditLesson(lesson)}>编辑课时</Button>,
                          <Popconfirm
                            key="delete"
                            title="删除这个课时？"
                            description="关联的课堂任务会保留，但会解除课时关联。"
                            okText="删除"
                            cancelText="取消"
                            onConfirm={() => deleteItem("lessons", lesson.id, "课时已删除")}
                          >
                            <Button danger size="small" icon={<Trash2 size={15} />}>删除</Button>
                          </Popconfirm>
                        ]}
                      >
                        <List.Item.Meta
                          title={<Space wrap><Text strong>{lesson.title}</Text><Tag>{lesson.course_title || "未关联课程"}</Tag><Tag>排序 {lesson.order_index}</Tag></Space>}
                          description={
                            <Space direction="vertical" size={4}>
                              <Paragraph ellipsis={{ rows: 3 }}>{lesson.content || "暂无课时内容"}</Paragraph>
                              <Text type="secondary">{formatBeijingTime(lesson.created_at)}</Text>
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </Col>
              </Row>
            )
          },
          {
            key: "assets",
            label: "素材库",
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={10}>
                  <Form form={assetForm} layout="vertical" onFinish={saveAsset} initialValues={{ asset_type: "document" }}>
                    <Form.Item name="asset_type" label="素材类型" rules={[{ required: true }]}>
                      <Select
                        options={[
                          { value: "image", label: "图片" },
                          { value: "video", label: "视频" },
                          { value: "audio", label: "音频" },
                          { value: "document", label: "文档" },
                          { value: "code", label: "代码" }
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="classroom_id" label="目标班级">
                      <Select
                        allowClear
                        placeholder="不选择则所有班级可见"
                        options={classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }))}
                      />
                    </Form.Item>
                    <Form.Item name="lesson_id" label="关联课时">
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder="可选：关联课程课时"
                        options={lessons.map((lesson) => ({ value: lesson.id, label: `${lesson.course_title || "未关联课程"} · ${lesson.title}` }))}
                      />
                    </Form.Item>
                    <Form.Item label="上传文件">
                      <Input
                        type="file"
                        onChange={(event) => setAssetFile(event.target.files?.[0] || null)}
                      />
                      <Text type="secondary">最大 50 MB，文件后缀必须与素材类型一致。</Text>
                    </Form.Item>
                    <Form.Item name="file_path" label="或登记已有路径 / URL">
                      <Input placeholder="例如：D:\\课程素材\\机器人.png 或 https://..." disabled={Boolean(assetFile)} />
                    </Form.Item>
                    <Form.Item name="display_name" label="素材名称">
                      <Input placeholder="例如：机器人参考图" />
                    </Form.Item>
                    <Form.Item name="description" label="素材说明">
                      <Input.TextArea rows={3} placeholder="说明素材用途、来源和课堂使用方式" />
                    </Form.Item>
                    <Form.Item name="tags" label="标签">
                      <Input placeholder="使用英文逗号分隔，例如：机器人,第1课,参考图" />
                    </Form.Item>
                    <Button icon={<Plus size={16} />} type="primary" htmlType="submit" loading={savingAsset}>
                      保存素材
                    </Button>
                  </Form>
                </Col>
                <Col xs={24} lg={14}>
                  <List
                    dataSource={assets}
                    locale={{ emptyText: "暂无素材" }}
                    renderItem={(asset) => (
                      <List.Item
                        actions={[
                          <Button key="preview" size="small" icon={<Eye size={15} />} onClick={() => openAssetPreview(asset)}>预览</Button>,
                          <Popconfirm key="delete" title="从素材库删除这个素材？" okText="删除" cancelText="取消" onConfirm={() => deleteItem("assets", asset.id, "素材已删除")}>
                            <Button danger size="small" icon={<Trash2 size={15} />}>删除</Button>
                          </Popconfirm>
                        ]}
                      >
                        <List.Item.Meta
                          title={
                            <Space wrap>
                              <Text strong>{assetName(asset)}</Text>
                              <Tag>{assetTypeLabel(asset.asset_type)}</Tag>
                              {asset.classroom_name ? <Tag color="blue">{asset.classroom_name}</Tag> : <Tag>全部班级</Tag>}
                              {asset.lesson_title && <Tag color="cyan">{asset.course_title ? `${asset.course_title} · ` : ""}{asset.lesson_title}</Tag>}
                              <ProjectFileStatus project={{ file_status: asset.file_status, file_path: asset.file_path } as Project} compact />
                            </Space>
                          }
                          description={
                            <Space direction="vertical" size={6} className="fullWidth">
                              <Text copyable={{ text: asset.file_path }} type="secondary">
                                {asset.file_path}
                              </Text>
                              <Space wrap>
                                <Tag color={asset.safety_status === "verified" ? "green" : "orange"}>{asset.safety_status === "verified" ? "已校验" : "远程待核验"}</Tag>
                                <Text type="secondary">{asset.file_extension || "未知后缀"} · {formatFileSize(asset.file_size)} · {asset.mime_type || "未知类型"}</Text>
                              </Space>
                              {asset.metadata?.description && <Text type="secondary">{asset.metadata.description}</Text>}
                              {asset.metadata?.tags?.length ? <Space wrap>{asset.metadata.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space> : null}
                              {asset.checksum_sha256 && <Text type="secondary" copyable={{ text: asset.checksum_sha256 }}>SHA-256：{asset.checksum_sha256.slice(0, 16)}…</Text>}
                              <Text type="secondary">{formatBeijingTime(asset.created_at)}</Text>
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </Col>
              </Row>
            )
          },
          {
            key: "tasks",
            label: "课堂任务",
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={10}>
                  <Form
                    form={taskForm}
                    layout="vertical"
                    onFinish={createTask}
                    initialValues={{ tool_scope: ["text", "image", "workflow"], status: "published", rubric_text: "创意表现:30\n任务完成度:40\n技术应用:30" }}
                  >
                    <Form.Item name="title" label="任务标题" rules={[{ required: true, message: "请输入任务标题" }]}>
                      <Input placeholder="例如：用AI设计机器人海报" />
                    </Form.Item>
                    <Form.Item name="instructions" label="任务说明">
                      <Input.TextArea rows={4} placeholder="填写任务目标、提交要求和使用工具限制" />
                    </Form.Item>
                    <Form.Item name="classroom_id" label="目标班级">
                      <Select
                        allowClear
                        placeholder="不选择则对所有学生可见"
                        options={classrooms.map((classroom) => ({
                          value: classroom.id,
                          label: classroom.name
                        }))}
                      />
                    </Form.Item>
                    <Form.Item name="lesson_id" label="所属课时">
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder="可选：关联到课程课时"
                        options={lessons.map((lesson) => ({
                          value: lesson.id,
                          label: `${lesson.course_title || "未关联课程"} · ${lesson.title}`
                        }))}
                      />
                    </Form.Item>
                    <Form.Item name="tool_scope" label="允许工具">
                      <Select
                        mode="multiple"
                        options={[
                          { value: "text", label: "文字生成" },
                          { value: "image", label: "图片生成" },
                          { value: "workflow", label: "工作流" },
                          { value: "video", label: "视频生成" }
                        ]}
                      />
                    </Form.Item>
                    <Form.Item name="rubric_text" label="评分规则" rules={[{ required: true, message: "请填写评分规则" }]}>
                      <Input.TextArea rows={3} placeholder={"每行格式为 评分项:分值\n创意表现:30\n任务完成度:70"} />
                    </Form.Item>
                    <Form.Item name="status" label="发布状态">
                      <Radio.Group optionType="button" buttonStyle="solid" options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }]} />
                    </Form.Item>
                    <Row gutter={12}>
                      <Col span={12}><Form.Item name="starts_at" label="开始时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
                      <Col span={12}><Form.Item name="due_at" label="截止时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
                    </Row>
                    <Button icon={<Plus size={16} />} type="primary" htmlType="submit" loading={savingTask}>
                      发布任务
                    </Button>
                  </Form>
                </Col>
                <Col xs={24} lg={14}>
                  <List
                    dataSource={classTasks}
                    locale={{ emptyText: "暂无课堂任务" }}
                    renderItem={(task) => (
                      <List.Item
                        actions={[
                          <Button key="edit" size="small" onClick={() => openEditTask(task)}>
                            编辑任务
                          </Button>,
                          <Popconfirm
                            key="delete"
                            title="删除课堂任务？"
                            description="该任务的提交与批改记录也会删除，学生作品本身会保留。"
                            okText="删除"
                            cancelText="取消"
                            onConfirm={() => deleteClassTask(task.id)}
                          >
                            <Button danger size="small" icon={<Trash2 size={15} />}>删除</Button>
                          </Popconfirm>
                        ]}
                      >
                        <List.Item.Meta
                          title={<Space wrap><Text strong>{task.title}</Text><Tag color={task.status === "published" ? "green" : "default"}>{task.status === "published" ? "已发布" : "草稿"}</Tag></Space>}
                          description={
                            <Space direction="vertical" size={6}>
                              <Paragraph ellipsis={{ rows: 2 }}>{task.instructions || "暂无任务说明"}</Paragraph>
                              <Space wrap>
                                {task.classroom_id && (
                                  <Tag color="blue">
                                    {classrooms.find((classroom) => classroom.id === task.classroom_id)?.name || "指定班级"}
                                  </Tag>
                                )}
                                {task.lesson_title && <Tag color="cyan">{task.course_title ? `${task.course_title} · ` : ""}{task.lesson_title}</Tag>}
                                {task.tool_scope.split(",").map((scope) => (
                                  <Tag key={scope}>{toolScopeLabel(scope)}</Tag>
                                ))}
                                <Tag color="purple">满分 {task.max_score}</Tag>
                                {task.rubric.map((item) => <Tag key={item.criterion}>{item.criterion} {item.max_score}</Tag>)}
                                <Text type="secondary">开始：{task.starts_at ? formatBeijingTime(task.starts_at) : "立即"}</Text>
                                <Text type="secondary">截止：{task.due_at ? formatBeijingTime(task.due_at) : "不限"}</Text>
                                <Text type="secondary">{formatBeijingTime(task.created_at)}</Text>
                              </Space>
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </Col>
              </Row>
            )
          }
        ]}
      />
      <Drawer
        title={editingCourse ? `编辑课程：${editingCourse.title}` : "编辑课程"}
        open={Boolean(editingCourse)}
        width={680}
        onClose={() => setEditingCourse(null)}
      >
        <Form form={editCourseForm} layout="vertical" onFinish={updateCourse}>
          <Form.Item name="title" label="课程名称" rules={[{ required: true, message: "请输入课程名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="课程说明">
            <Input.TextArea rows={6} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="package_version" label="课程版本" rules={[{ required: true }]}><Input /></Form.Item></Col>
            <Col span={12}><Form.Item name="author" label="作者 / 机构"><Input /></Form.Item></Col>
          </Row>
          <Form.Item name="age_range" label="适用学龄说明"><Input /></Form.Item>
          <Form.Item name="cover_path" label="课程封面"><Input /></Form.Item>
          <Form.Item name="dependencies_text" label="依赖素材"><Input.TextArea rows={3} placeholder="每行一个素材" /></Form.Item>
          <Form.Item name="checklist_text" label="验收清单"><Input.TextArea rows={3} placeholder="每行一个验收项" /></Form.Item>
          <Form.Item name="classroom_id" label="目标班级">
            <Select
              allowClear
              placeholder="不选择则所有班级可见"
              options={classrooms.map((classroom) => ({ value: classroom.id, label: classroom.name }))}
            />
          </Form.Item>
          <Form.Item name="status" label="发布状态">
            <Radio.Group optionType="button" buttonStyle="solid" options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }]} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="starts_at" label="开始时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
            <Col span={12}><Form.Item name="ends_at" label="结束时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={updatingCourse} block>
            保存课程
          </Button>
        </Form>
      </Drawer>
      <Drawer
        title={editingLesson ? `编辑课时：${editingLesson.title}` : "编辑课时"}
        open={Boolean(editingLesson)}
        width={640}
        onClose={() => setEditingLesson(null)}
      >
        <Form form={editLessonForm} layout="vertical" onFinish={updateLesson}>
          <Form.Item name="course_id" label="所属课程">
            <Select
              allowClear
              placeholder="选择课程"
              options={courses.map((course) => ({ value: course.id, label: course.title }))}
            />
          </Form.Item>
          <Form.Item name="title" label="课时标题" rules={[{ required: true, message: "请输入课时标题" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="order_index" label="排序">
            <Input type="number" min={0} />
          </Form.Item>
          <Form.Item name="content" label="课时内容">
            <Input.TextArea rows={10} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={updatingLesson} block>
            保存课时
          </Button>
        </Form>
      </Drawer>
      <Drawer
        title={editingTask ? `编辑任务：${editingTask.title}` : "编辑任务"}
        open={Boolean(editingTask)}
        width={560}
        onClose={() => setEditingTask(null)}
      >
        <Form form={editTaskForm} layout="vertical" onFinish={updateTask}>
          <Form.Item name="title" label="任务标题" rules={[{ required: true, message: "请输入任务标题" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="instructions" label="任务说明">
            <Input.TextArea rows={5} />
          </Form.Item>
          <Form.Item name="classroom_id" label="目标班级">
            <Select
              allowClear
              placeholder="不选择则对所有学生可见"
              options={classrooms.map((classroom) => ({
                value: classroom.id,
                label: classroom.name
              }))}
            />
          </Form.Item>
          <Form.Item name="lesson_id" label="所属课时">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="可选：关联到课程课时"
              options={lessons.map((lesson) => ({
                value: lesson.id,
                label: `${lesson.course_title || "未关联课程"} · ${lesson.title}`
              }))}
            />
          </Form.Item>
          <Form.Item name="tool_scope" label="允许工具" rules={[{ required: true, message: "请选择至少一个工具" }]}>
            <Select
              mode="multiple"
              options={[
                { value: "text", label: "文字生成" },
                { value: "image", label: "图片生成" },
                { value: "workflow", label: "工作流" },
                { value: "video", label: "视频生成" }
              ]}
            />
          </Form.Item>
          <Form.Item name="rubric_text" label="评分规则" rules={[{ required: true, message: "请填写评分规则" }]}>
            <Input.TextArea rows={4} placeholder="每行格式为 评分项:分值" />
          </Form.Item>
          <Form.Item name="status" label="发布状态">
            <Radio.Group optionType="button" buttonStyle="solid" options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }]} />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="starts_at" label="开始时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
            <Col span={12}><Form.Item name="due_at" label="截止时间"><DatePicker showTime className="fullWidth" /></Form.Item></Col>
          </Row>
          <Button type="primary" htmlType="submit" loading={updatingTask} block>
            保存任务
          </Button>
        </Form>
      </Drawer>
      <Drawer title={previewAsset ? assetName(previewAsset) : "素材预览"} open={Boolean(previewAsset)} width={760} onClose={closeAssetPreview}>
        {previewAsset && (
          <Space direction="vertical" size={16} className="fullWidth">
            <Space wrap>
              <Tag>{assetTypeLabel(previewAsset.asset_type)}</Tag>
              <ProjectFileStatus project={{ file_status: previewAsset.file_status, file_path: previewAsset.file_path } as Project} compact />
              <Text type="secondary">{formatBeijingTime(previewAsset.created_at)}</Text>
            </Space>
            {previewAssetLoading && <Alert type="info" showIcon message="正在加载素材预览" />}
            {!previewAssetLoading && previewAssetUrl && <AssetMediaPreview asset={previewAsset} url={previewAssetUrl} />}
            {!previewAssetLoading && !previewAssetUrl && <Alert type="warning" showIcon message="这个素材暂时无法预览" />}
            {previewAsset.metadata_json && (
              <Card size="small" title="素材备注"><pre className="assetMetadata">{previewAsset.metadata_json}</pre></Card>
            )}
          </Space>
        )}
      </Drawer>
    </Card>
  );
}

export function AssetMediaPreview({ asset, url }: { asset: AssetItem; url: string }) {
  const type = asset.asset_type.toLowerCase();
  if (type === "image") return <img className="assetPreviewImage" src={url} alt={assetName(asset)} />;
  if (type === "video") return <video className="generatedVideo" src={url} controls />;
  if (type === "audio") return <audio className="assetPreviewAudio" src={url} controls />;
  return <iframe className="assetPreviewFrame" src={url} title={assetName(asset)} />;
}
