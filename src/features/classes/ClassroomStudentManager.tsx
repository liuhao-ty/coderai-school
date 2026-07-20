import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Drawer,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { GraduationCap, Pencil, Plus, Trash2, UsersRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type { Classroom, SchoolStage } from "../../domain-types";
import { api, loadTeacherProfile } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";
import { CLASSROOM_SCHOOL_STAGE_OPTIONS, classroomSchoolStageLabel } from "../../lib/schoolStages";
import type { StudentProfile } from "../../types";


const { Text } = Typography;

type TeacherOption = { id: number; name: string; username: string };

export function ClassroomStudentManager({
  classrooms,
  students,
  onRefresh,
}: {
  classrooms: Classroom[];
  students: StudentProfile[];
  onRefresh: () => Promise<void>;
}) {
  const { message } = AntApp.useApp();
  const profile = loadTeacherProfile();
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const [teacherForm] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [editingClassroom, setEditingClassroom] = useState<Classroom | null>(null);
  const [assigningClassroom, setAssigningClassroom] = useState<Classroom | null>(null);
  const [teacherOptions, setTeacherOptions] = useState<TeacherOption[]>([]);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api.get("/api/teachers/options")
      .then((response) => setTeacherOptions(response.data.teachers || []))
      .catch((error) => message.error(explainError(error)));
  }, [message]);

  const filteredClassrooms = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return classrooms;
    return classrooms.filter((classroom) => (
      classroom.name.toLowerCase().includes(keyword)
      || classroom.teachers?.some((teacher) => `${teacher.name} ${teacher.username}`.toLowerCase().includes(keyword))
    ));
  }, [classrooms, search]);

  const createClassroom = async (values: { name: string; grade_level: SchoolStage | "mixed" }) => {
    setSaving(true);
    try {
      await api.post("/api/classrooms", values);
      createForm.resetFields();
      await onRefresh();
      message.success(profile?.role === "admin" ? "班级已创建，可继续分配授课教师" : "班级已创建并自动分配给当前教师");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (classroom: Classroom) => {
    setEditingClassroom(classroom);
    editForm.setFieldsValue({ name: classroom.name, grade_level: classroom.grade_level });
  };

  const updateClassroom = async (values: { name: string; grade_level: SchoolStage | "mixed" }) => {
    if (!editingClassroom) return;
    setSaving(true);
    try {
      await api.put(`/api/classrooms/${editingClassroom.id}`, values);
      setEditingClassroom(null);
      await onRefresh();
      message.success("班级资料已更新");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const openTeacherAssignment = (classroom: Classroom) => {
    setAssigningClassroom(classroom);
    teacherForm.setFieldsValue({ teacher_ids: classroom.teacher_ids || [] });
  };

  const updateTeachers = async (values: { teacher_ids: number[] }) => {
    if (!assigningClassroom) return;
    const teacherIds = values.teacher_ids || [];
    if (profile?.role === "teacher" && !teacherIds.includes(profile.id)) {
      message.warning("教师不能从自己的授课班级中移除自己，请联系管理员调整");
      return;
    }
    setSaving(true);
    try {
      await api.put(`/api/classrooms/${assigningClassroom.id}/teachers`, { teacher_ids: teacherIds });
      setAssigningClassroom(null);
      await onRefresh();
      message.success(teacherIds.length ? "授课教师已更新" : "班级已设为暂未分配");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const deleteClassroom = async (classroom: Classroom) => {
    try {
      await api.delete(`/api/classrooms/${classroom.id}`);
      await onRefresh();
      message.success("空班级已删除");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      <Alert
        type="info"
        showIcon
        message="班级与授课教师分开授权"
        description="所有班级都可用于学员分班；只有授课教师和管理员可以维护班级内的课程、作品、提交和安全记录。"
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card title={<IconTitle icon={<Plus size={18} />} text="创建班级" />}>
            <Form form={createForm} layout="vertical" onFinish={createClassroom} initialValues={{ grade_level: "mixed" }}>
              <Form.Item name="name" label="班级名称" rules={[{ required: true, message: "请输入班级名称" }]}>
                <Input placeholder="例如：周六 AI 基础班" />
              </Form.Item>
              <Form.Item name="grade_level" label="班级学龄">
                <Select options={CLASSROOM_SCHOOL_STAGE_OPTIONS} />
              </Form.Item>
              <Button type="primary" htmlType="submit" icon={<Plus size={16} />} loading={saving} block>创建班级</Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card
            title={<IconTitle icon={<GraduationCap size={18} />} text="班级列表" />}
            extra={<Input.Search allowClear aria-label="搜索班级" placeholder="搜索班级或授课教师" onChange={(event) => setSearch(event.target.value)} />}
          >
            <List
              dataSource={filteredClassrooms}
              locale={{ emptyText: "暂无班级" }}
              renderItem={(classroom) => {
                const studentCount = students.filter((student) => student.classroom_id === classroom.id).length;
                return (
                  <List.Item
                    actions={classroom.can_manage ? [
                      <Button key="teachers" size="small" icon={<UsersRound size={14} />} onClick={() => openTeacherAssignment(classroom)}>授课教师</Button>,
                      <Button key="edit" size="small" icon={<Pencil size={14} />} onClick={() => openEdit(classroom)}>编辑</Button>,
                      <Popconfirm
                        key="delete"
                        title="删除这个班级？"
                        description="仅无学生且没有任何教学历史的班级可以删除。"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={() => deleteClassroom(classroom)}
                      >
                        <Button danger size="small" icon={<Trash2 size={14} />}>删除</Button>
                      </Popconfirm>,
                    ] : []}
                  >
                    <List.Item.Meta
                      title={(
                        <Space wrap>
                          <Text strong>{classroom.name}</Text>
                          <Tag color={classroom.assignment_status === "assigned" ? "blue" : "orange"}>
                            {classroom.assignment_status === "assigned" ? "已分配" : "暂未分配"}
                          </Tag>
                          {!classroom.can_manage && <Tag>只读</Tag>}
                        </Space>
                      )}
                      description={(
                        <Space direction="vertical" size={6}>
                          <Space wrap>
                            <Tag>{classroom.grade_level_label || classroomSchoolStageLabel(classroom.grade_level)}</Tag>
                            <Tag color={studentCount ? "cyan" : "default"}>{studentCount} 名学员</Tag>
                            <Text type="secondary">创建于 {formatBeijingTime(classroom.created_at)}</Text>
                          </Space>
                          <Space wrap>
                            <Text type="secondary">授课教师：</Text>
                            {classroom.teachers?.length
                              ? classroom.teachers.map((teacher) => <Tag key={teacher.id}>{teacher.name} · {teacher.username}</Tag>)
                              : <Text type="secondary">暂未分配</Text>}
                          </Space>
                        </Space>
                      )}
                    />
                  </List.Item>
                );
              }}
            />
          </Card>
        </Col>
      </Row>

      <Drawer title={editingClassroom ? `编辑班级：${editingClassroom.name}` : "编辑班级"} open={Boolean(editingClassroom)} width={480} onClose={() => setEditingClassroom(null)}>
        <Form form={editForm} layout="vertical" onFinish={updateClassroom}>
          <Form.Item name="name" label="班级名称" rules={[{ required: true, message: "请输入班级名称" }]}><Input /></Form.Item>
          <Form.Item name="grade_level" label="班级学龄">
            <Select options={CLASSROOM_SCHOOL_STAGE_OPTIONS} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving} block>保存班级资料</Button>
        </Form>
      </Drawer>

      <Drawer title={assigningClassroom ? `授课教师：${assigningClassroom.name}` : "授课教师"} open={Boolean(assigningClassroom)} width={520} onClose={() => setAssigningClassroom(null)}>
        <Alert
          className="mb16"
          type="info"
          showIcon
          message={profile?.role === "admin" ? "管理员可以将班级设为暂未分配" : "当前教师必须保留在授课教师列表中"}
        />
        <Form form={teacherForm} layout="vertical" onFinish={updateTeachers}>
          <Form.Item name="teacher_ids" label="授课教师">
            <Select
              mode="multiple"
              allowClear={profile?.role === "admin"}
              optionFilterProp="label"
              placeholder="选择参与授课的教师"
              options={teacherOptions.map((teacher) => ({ value: teacher.id, label: `${teacher.name} · ${teacher.username}` }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving} block>保存授课教师</Button>
        </Form>
      </Drawer>
    </Space>
  );
}
