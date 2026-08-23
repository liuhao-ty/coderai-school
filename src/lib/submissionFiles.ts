export const submissionExtensionOptions = [
  [".md", "Markdown"],
  [".txt", "文本"],
  [".pdf", "PDF"],
  [".zip", "ZIP 工程包"],
  [".sb3", "Scratch 3 工程"],
  [".py", "Python"],
  [".html", "HTML"],
  [".css", "CSS"],
  [".js", "JavaScript"],
  [".ts", "TypeScript"],
  [".json", "JSON"],
  [".csv", "CSV"],
  [".docx", "Word"],
  [".pptx", "PowerPoint"],
  [".xlsx", "Excel"],
  [".png", "PNG 图片"],
  [".jpg", "JPEG 图片"],
  [".jpeg", "JPEG 图片"],
  [".webp", "WebP 图片"],
  [".mp4", "MP4 视频"],
].map(([value, name]) => ({ value, label: `${name} (${value})` }));

export const defaultSubmissionExtensions = submissionExtensionOptions.map((item) => item.value);
export const maximumSubmissionFileBytes = 20 * 1024 * 1024;

export function formatMegabytes(bytes: number) {
  return `${Math.max(1, Math.round(bytes / 1024 / 1024))} MB`;
}
