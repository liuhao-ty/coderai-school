import type { AssetItem, ClassTask, TaskSubmission } from "../domain-types";


export function capabilityLabel(capability: string) {
  if (capability === "text") return "文字生成";
  if (capability === "image") return "图片生成";
  if (capability === "video") return "视频生成配置";
  return capability;
}

export function toolScopeLabel(scope: string) {
  const normalized = scope.trim();
  if (normalized === "text") return "文字生成";
  if (normalized === "image") return "图片生成";
  if (normalized === "workflow") return "工作流";
  if (normalized === "video") return "视频生成";
  return normalized;
}

export function allowedToolsFromTasks(tasks: ClassTask[]) {
  if (!tasks.length) {
    return new Set(["text", "image", "video", "workflow"]);
  }
  const scopes = tasks.flatMap((task) =>
    task.tool_scope
      .split(",")
      .map((scope) => scope.trim())
      .filter(Boolean)
  );
  return new Set(scopes);
}

export function projectTypeLabel(type: string) {
  if (type === "text") return "文字作品";
  if (type === "image") return "图片作品";
  if (type === "video") return "视频作品";
  if (type === "workflow") return "工作流作品";
  if (type === "plugin_text") return "插件文字作品";
  return type || "未分类作品";
}

export function assetTypeLabel(type: string) {
  if (type === "image") return "图片";
  if (type === "video") return "视频";
  if (type === "audio") return "音频";
  if (type === "document") return "文档";
  if (type === "code") return "代码";
  return type || "素材";
}

export function assetName(asset: AssetItem) {
  if (asset.metadata?.name) return asset.metadata.name;
  try {
    const metadata = JSON.parse(asset.metadata_json || "{}");
    if (metadata.name) return String(metadata.name);
  } catch {
    // User-entered metadata can be plain text or invalid JSON; fall back to the file name.
  }
  const normalized = asset.file_path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).pop() || `素材 ${asset.id}`;
}

export function formatFileSize(size: number) {
  if (!size) return "大小未知";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function submissionForTask(submissions: TaskSubmission[], taskId: number) {
  return submissions.find((submission) => submission.task_id === taskId);
}

export function lifecyclePayload(values: Record<string, any>) {
  const payload = { ...values };
  for (const field of ["starts_at", "ends_at", "due_at"]) {
    payload[field] = values[field] ? values[field].format("YYYY-MM-DDTHH:mm:ss") : null;
  }
  return payload;
}

export function coursePackagePayload(values: Record<string, any>) {
  return {
    ...lifecyclePayload(values),
    package_version: String(values.package_version || "1.0.0").trim(),
    author: String(values.author || "").trim(),
    age_range: String(values.age_range || "全年龄").trim(),
    cover_path: String(values.cover_path || "").trim(),
    dependencies: String(values.dependencies_text || "").split(/\r?\n/).map((name) => name.trim()).filter(Boolean).map((name) => ({ name, required: true })),
    checklist: String(values.checklist_text || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
  };
}

export function taskPayload(values: Record<string, any>) {
  const rubric = String(values.rubric_text || "完成度:100").split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const separator = line.search(/[:：]/);
    const criterion = separator >= 0 ? line.slice(0, separator).trim() : "";
    const max_score = separator >= 0 ? Number(line.slice(separator + 1).trim()) : 0;
    if (!criterion || !Number.isInteger(max_score) || max_score <= 0) throw new Error(`评分规则格式不正确：${line}`);
    return { criterion, max_score };
  });
  return { ...lifecyclePayload(values), rubric };
}

export function videoTaskStatusLabel(status: string) {
  if (status === "submitted") return "已提交";
  if (status === "processing") return "生成中";
  if (status === "success") return "已完成";
  if (status === "failed") return "失败";
  if (status === "timed_out") return "已超时";
  if (status === "download_failed") return "下载失败";
  if (status === "expired") return "链接已过期";
  if (status === "canceled") return "已取消";
  return status || "未知";
}

export function videoTaskStatusColor(status: string) {
  if (status === "submitted") return "orange";
  if (status === "processing") return "blue";
  if (status === "success") return "green";
  if (status === "failed") return "red";
  if (status === "timed_out") return "red";
  if (status === "download_failed") return "orange";
  if (status === "expired") return "red";
  if (status === "canceled") return "default";
  return "default";
}

export function submissionStatusLabel(status: string) {
  if (status === "submitted") return "已提交";
  if (status === "reviewed") return "已批改";
  if (status === "returned") return "需修改";
  return status || "未提交";
}

export function submissionStatusColor(status: string) {
  if (status === "submitted") return "orange";
  if (status === "reviewed") return "green";
  if (status === "returned") return "red";
  return "default";
}

export function workflowRunStatusLabel(status: string) {
  if (status === "pending") return "等待中";
  if (status === "running") return "运行中";
  if (status === "success") return "成功";
  if (status === "partial_failed") return "部分完成";
  if (status === "failed") return "失败";
  if (status === "blocked") return "已阻断";
  if (status === "canceled") return "已取消";
  return status || "未知";
}

export function workflowRunStatusColor(status: string) {
  if (status === "pending") return "default";
  if (status === "running") return "blue";
  if (status === "success") return "green";
  if (status === "partial_failed") return "orange";
  if (status === "failed") return "red";
  if (status === "blocked") return "orange";
  if (status === "canceled") return "default";
  return "default";
}
