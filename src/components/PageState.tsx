import { Alert, Button, Empty, Skeleton, Space } from "antd";
import { RotateCcw } from "lucide-react";


export function WorkspaceSkeleton() {
  return (
    <div className="workspaceSkeleton" aria-label="正在加载课堂数据" aria-busy="true">
      <Skeleton active title={{ width: "30%" }} paragraph={{ rows: 2 }} />
      <div className="skeletonGrid">
        <Skeleton.Node active className="skeletonPanel" />
        <Skeleton.Node active className="skeletonPanel" />
        <Skeleton.Node active className="skeletonPanel" />
      </div>
      <Skeleton active title={false} paragraph={{ rows: 6 }} />
    </div>
  );
}

export function WorkspaceError({ message, onRetry }: { message: string; onRetry: () => void }) {
  const partial = message.startsWith("部分数据暂未更新");
  return (
    <Alert
      className="workspaceError"
      type={partial ? "warning" : "error"}
      showIcon
      message={partial ? "部分数据暂未更新" : "课堂数据加载失败"}
      description={
        <Space direction="vertical" size={12}>
          <span>{message || "暂时无法连接 CoderAI 云端服务，请检查网络后重试。"}</span>
          <Button icon={<RotateCcw size={15} />} onClick={onRetry}>重新加载</Button>
        </Space>
      }
    />
  );
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span>{title}{description ? `：${description}` : ""}</span>} />;
}
