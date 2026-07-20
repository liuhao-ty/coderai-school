import { Alert, App as AntApp, Button, Card, Input, Popconfirm, Select, Space, Tabs, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { FileDown, FileUp, Save } from "lucide-react";
import { useRef, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime, formatBytes } from "../../lib/format";


const { Text } = Typography;

type BackupPackagePreview = {
  valid: boolean;
  package_version: string;
  migrated_from?: string | null;
  created_at: string;
  database_tables: Record<string, number>;
  file_count: number;
  file_bytes: number;
  existing_file_conflicts: number;
  redactions: string[];
  warnings: string[];
};

export function SystemBackupPanel({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { message } = AntApp.useApp();
  const [backupText, setBackupText] = useState("");
  const [loading, setLoading] = useState("");
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [packagePreview, setPackagePreview] = useState<BackupPackagePreview | null>(null);
  const [conflictStrategy, setConflictStrategy] = useState<"replace" | "keep_existing">("replace");
  const packageInputRef = useRef<HTMLInputElement | null>(null);

  const downloadPackage = async () => {
    setLoading("download");
    try {
      const res = await api.get("/api/system/backups/export", { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `coderai-backup-${dayjs().format("YYYYMMDD-HHmmss")}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      message.success("完整压缩备份已生成并下载");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const preflightPackage = async (file?: File) => {
    if (!file) return;
    setPackageFile(file);
    setPackagePreview(null);
    setLoading("preflight");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api.post("/api/system/backups/preflight", formData);
      setPackagePreview(res.data.preview);
      message.success("备份包预检通过");
    } catch (error) {
      setPackageFile(null);
      message.error(explainError(error));
    } finally {
      setLoading("");
      if (packageInputRef.current) packageInputRef.current.value = "";
    }
  };

  const restorePackage = async () => {
    if (!packageFile || !packagePreview) return;
    setLoading("restore");
    try {
      const formData = new FormData();
      formData.append("file", packageFile);
      formData.append("conflict_strategy", conflictStrategy);
      const res = await api.post("/api/system/backups/restore", formData);
      await onRefresh();
      setPackageFile(null);
      setPackagePreview(null);
      message.success(`完整恢复成功，共恢复 ${res.data.restored_files} 个文件`);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const exportBackup = async () => {
    setLoading("legacy-export");
    try {
      const res = await api.get("/api/system/backup");
      setBackupText(JSON.stringify(res.data, null, 2));
      message.success("系统备份已生成");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading("");
    }
  };

  const restoreBackup = async () => {
    setLoading("legacy-restore");
    try {
      const parsed = JSON.parse(backupText);
      const res = await api.post("/api/system/restore", parsed);
      await onRefresh();
      message.success(
        `导入完成：班级 ${res.data.imported.classrooms}，学生 ${res.data.imported.students}，课程 ${res.data.imported.courses}，课时 ${res.data.imported.lessons}，任务 ${res.data.imported.tasks}，作品 ${res.data.imported.projects}，素材 ${res.data.imported.assets}，提交 ${res.data.imported.submissions}`
      );
    } catch (error) {
      message.error(error instanceof SyntaxError ? "备份 JSON 格式不正确" : explainError(error));
    } finally {
      setLoading("");
    }
  };

  return (
    <Card title={<IconTitle icon={<Save size={18} />} text="数据备份" />}>
      <Tabs
        items={[
          {
            key: "package",
            label: "完整压缩备份",
            children: (
              <Space direction="vertical" size={12} className="fullWidth">
                <Alert
                  type="success"
                  showIcon
                  message="数据库与真实文件完整备份"
                  description="压缩包包含脱敏 SQLite、作品、AI 输出、素材、输入和插件文件，以及视频任务、工作流运行、隐私政策、授权状态、用量和许可证等记录。API Key、教师会话和监护人联系方式不会写入备份。"
                />
                <Space wrap>
                  <Button icon={<FileDown size={15} />} type="primary" loading={loading === "download"} onClick={() => void downloadPackage()}>
                    下载完整备份
                  </Button>
                  <input
                    ref={packageInputRef}
                    type="file"
                    accept=".zip,application/zip"
                    hidden
                    onChange={(event) => void preflightPackage(event.target.files?.[0])}
                  />
                  <Button icon={<FileUp size={15} />} loading={loading === "preflight"} onClick={() => packageInputRef.current?.click()}>
                    选择备份包并预检
                  </Button>
                  {packageFile && <Text type="secondary">{packageFile.name}</Text>}
                </Space>
                {packagePreview && (
                  <Space direction="vertical" size={12} className="fullWidth">
                    <Alert
                      type="info"
                      showIcon
                      message={`备份版本 ${packagePreview.package_version}，创建于 ${formatBeijingTime(packagePreview.created_at)}`}
                      description={packagePreview.migrated_from ? `清单已从 ${packagePreview.migrated_from} 自动迁移。` : "数据库完整性和所有文件哈希校验均已通过。"}
                    />
                    <Space wrap>
                      <Tag color="blue">真实文件 {packagePreview.file_count}</Tag>
                      <Tag color="blue">文件大小 {formatBytes(packagePreview.file_bytes)}</Tag>
                      <Tag color={packagePreview.existing_file_conflicts ? "orange" : "green"}>文件冲突 {packagePreview.existing_file_conflicts}</Tag>
                      <Tag>学生 {packagePreview.database_tables.users || 0}</Tag>
                      <Tag>作品 {packagePreview.database_tables.projects || 0}</Tag>
                      <Tag>素材 {packagePreview.database_tables.assets || 0}</Tag>
                      <Tag>视频任务 {packagePreview.database_tables.video_tasks || 0}</Tag>
                      <Tag>工作流运行 {packagePreview.database_tables.workflow_runs || 0}</Tag>
                      <Tag>用量记录 {packagePreview.database_tables.usage_logs || 0}</Tag>
                    </Space>
                    {packagePreview.warnings.map((warning) => <Text key={warning} type="secondary">{warning}</Text>)}
                    <Select
                      value={conflictStrategy}
                      onChange={setConflictStrategy}
                      options={[
                        { value: "replace", label: "覆盖现有受管文件（推荐完整恢复）" },
                        { value: "keep_existing", label: "保留同名现有文件" }
                      ]}
                    />
                    <Popconfirm
                      title="执行完整系统恢复？"
                      description="数据库将被替换。系统会先建立回滚快照，失败时自动恢复当前数据库和文件。"
                      okText="开始恢复"
                      cancelText="取消"
                      onConfirm={() => void restorePackage()}
                    >
                      <Button danger type="primary" loading={loading === "restore"}>执行完整恢复</Button>
                    </Popconfirm>
                  </Space>
                )}
              </Space>
            )
          },
          {
            key: "legacy",
            label: "兼容 JSON 导入",
            children: (
              <Space direction="vertical" size={12} className="fullWidth">
                <Alert
                  type="warning"
                  showIcon
                  message="旧版结构化追加导入"
                  description="只合并部分业务记录，不复制真实文件，也不具备完整回滚能力。新备份请使用“完整压缩备份”。"
                />
                <Input.TextArea
                  rows={10}
                  value={backupText}
                  onChange={(event) => setBackupText(event.target.value)}
                  placeholder="点击导出生成兼容 JSON，或粘贴旧版备份 JSON 后追加导入。"
                />
                <Space wrap>
                  <Button onClick={exportBackup} loading={loading === "legacy-export"}>导出兼容 JSON</Button>
                  <Popconfirm title="追加导入兼容备份？" description="将合并备份中的结构化记录，不复制文件。" onConfirm={restoreBackup}>
                    <Button loading={loading === "legacy-restore"} disabled={!backupText.trim()}>追加导入</Button>
                  </Popconfirm>
                </Space>
              </Space>
            )
          }
        ]}
      />
    </Card>
  );
}

