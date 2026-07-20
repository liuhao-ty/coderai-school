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
  return (
    <Alert
      className="workspaceError"
      type="error"
      showIcon
      message="课堂数据加载失败"
      description={
        <Space direction="vertical" size={12}>
          <span>{message || "无法连接本地服务，请检查网络和服务状态。"}</span>
          <Button icon={<RotateCcw size={15} />} onClick={onRetry}>重新加载</Button>
        </Space>
      }
    />
  );
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span>{title}{description ? `：${description}` : ""}</span>} />;
}
