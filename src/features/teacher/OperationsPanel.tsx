import { Alert, App as AntApp, Button, Card, Col, Input, List, Popconfirm, Row, Space, Statistic, Tag, Typography } from "antd";
import { FileSearch, HardDrive, RotateCcw, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime, formatBytes } from "../../lib/format";
import type { FileConsistencyScan, OperationLog, StorageStatistics } from "../../types";


const { Text } = Typography;

export function OperationsPanel() {
  const { message } = AntApp.useApp();
  const [storage, setStorage] = useState<StorageStatistics | null>(null);
  const [logs, setLogs] = useState<OperationLog[]>([]);
  const [selectedLog, setSelectedLog] = useState<{ name: string; content: string; size_bytes: number } | null>(null);
  const [scan, setScan] = useState<FileConsistencyScan | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState("");

  const loadOverview = async () => {
    setLoading(true);
    try {
      const [storageRes, logRes] = await Promise.all([
        api.get("/api/system/operations/storage"),
        api.get("/api/system/operations/logs")
      ]);
      setStorage(storageRes.data);
      setLogs(logRes.data.logs || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  const readLog = async (name: string) => {
    setActionLoading(`log:${name}`);
    try {
      const res = await api.get(`/api/system/operations/logs/${encodeURIComponent(name)}`);
      setSelectedLog(res.data);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setActionLoading("");
    }
  };

  const clearLog = async (name: string) => {
    setActionLoading(`clear-log:${name}`);
    try {
      await api.post(`/api/system/operations/logs/${encodeURIComponent(name)}/clear`);
      if (selectedLog?.name === name) setSelectedLog({ name, content: "", size_bytes: 0 });
      await loadOverview();
      message.success("日志已清空");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setActionLoading("");
    }
  };

  const clearCache = async () => {
    setActionLoading("cache");
    try {
      const res = await api.post("/api/system/operations/cache/clear");
      await loadOverview();
      message.success(`已清理 ${res.data.cleared_files} 个缓存文件，共 ${formatBytes(res.data.cleared_bytes)}`);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setActionLoading("");
    }
  };

  const scanFiles = async () => {
    setActionLoading("scan");
    try {
      const res = await api.get("/api/system/operations/files/scan");
      setScan(res.data);
      message.success("文件一致性扫描完成");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setActionLoading("");
    }
  };

  const repairMissingFiles = async () => {
    if (!scan) return;
    setActionLoading("repair");
    try {
      const res = await api.post("/api/system/operations/files/repair", {
        project_ids: scan.missing_projects.map((item) => item.id),
        asset_ids: scan.missing_assets.map((item) => item.id)
      });
      setScan(res.data.scan);
      await loadOverview();
      message.success(`已修复 ${res.data.repaired_projects} 条作品记录，移除 ${res.data.removed_asset_records} 条失效素材记录`);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setActionLoading("");
    }
  };

  const missingCount = (scan?.missing_projects.length || 0) + (scan?.missing_assets.length || 0);

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      <Card
        title={<IconTitle icon={<HardDrive size={18} />} text="存储占用" />}
        extra={<Button loading={loading} icon={<RotateCcw size={15} />} onClick={() => void loadOverview()}>刷新</Button>}
      >
        {storage ? (
          <Space direction="vertical" size={12} className="fullWidth">
            <Space wrap>
              <Statistic title="总占用" value={formatBytes(storage.total_bytes)} />
              <Statistic title="文件数量" value={storage.total_files} />
              <Text type="secondary" copyable>{storage.data_dir}</Text>
            </Space>
            <List
              size="small"
              grid={{ gutter: 12, xs: 1, sm: 2, lg: 4 }}
              dataSource={storage.categories}
              renderItem={(item) => (
                <List.Item>
                  <div className="storageStatItem">
                    <Text strong>{item.label}</Text>
                    <Text>{formatBytes(item.size_bytes)}</Text>
                    <Text type="secondary">{item.file_count} 个文件</Text>
                  </div>
                </List.Item>
              )}
            />
          </Space>
        ) : <Text type="secondary">正在读取存储信息...</Text>}
      </Card>

      <Card
        title={<IconTitle icon={<FileSearch size={18} />} text="文件一致性" />}
        extra={<Button loading={actionLoading === "scan"} icon={<FileSearch size={15} />} onClick={() => void scanFiles()}>开始扫描</Button>}
      >
        {!scan ? (
          <Text type="secondary">扫描数据库中的作品和素材路径，报告缺失记录与受管目录中的孤立文件。</Text>
        ) : (
          <Space direction="vertical" size={12} className="fullWidth">
            <Space wrap>
              <Tag color="blue">作品 {scan.scanned_projects}</Tag>
              <Tag color="blue">素材 {scan.scanned_assets}</Tag>
              <Tag color={missingCount ? "red" : "green"}>缺失记录 {missingCount}</Tag>
              <Tag color={scan.orphan_count ? "orange" : "green"}>孤立文件 {scan.orphan_count}</Tag>
            </Space>
            {missingCount > 0 && (
              <Alert
                type="warning"
                showIcon
                message="发现文件缺失记录"
                description="修复会保留作品内容但清除失效文件路径；缺失素材的无效数据库记录会被删除。真实文件不会被删除。"
              />
            )}
            <List
              size="small"
              dataSource={[...scan.missing_projects.map((item) => ({ ...item, kind: "作品" })), ...scan.missing_assets.map((item) => ({ ...item, kind: "素材" }))]}
              locale={{ emptyText: "没有发现缺失记录" }}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={<Space><Tag>{item.kind}</Tag><Text>{item.title}</Text>{!item.managed && <Tag color="orange">外部路径</Tag>}</Space>}
                    description={<Text type="secondary" copyable>{item.file_path}</Text>}
                  />
                </List.Item>
              )}
            />
            {missingCount > 0 && (
              <Popconfirm
                title="修复全部缺失记录？"
                description="作品记录会保留，缺失素材元数据会被移除。此操作不能自动找回原文件。"
                okText="执行修复"
                cancelText="取消"
                onConfirm={() => void repairMissingFiles()}
              >
                <Button danger loading={actionLoading === "repair"}>修复全部缺失记录</Button>
              </Popconfirm>
            )}
            {scan.orphan_count > 0 && (
              <List
                size="small"
                header={<Text strong>孤立文件（只报告，不自动删除）</Text>}
                dataSource={scan.orphan_files.slice(0, 20)}
                renderItem={(item) => (
                  <List.Item extra={<Text type="secondary">{formatBytes(item.size_bytes)}</Text>}>
                    <Text type="secondary" copyable>{item.file_path}</Text>
                  </List.Item>
                )}
              />
            )}
          </Space>
        )}
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card
            title={<IconTitle icon={<ScrollText size={18} />} text="运行日志" />}
            extra={
              <Popconfirm title="清理运行缓存？" description="只删除工作流等可重建缓存，不删除作品和素材。" onConfirm={() => void clearCache()}>
                <Button danger loading={actionLoading === "cache"}>清理缓存</Button>
              </Popconfirm>
            }
          >
            <List
              size="small"
              dataSource={logs}
              locale={{ emptyText: "暂无日志文件" }}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button key="read" size="small" loading={actionLoading === `log:${item.name}`} onClick={() => void readLog(item.name)}>查看</Button>,
                    <Popconfirm key="clear" title="清空这个日志？" onConfirm={() => void clearLog(item.name)}>
                      <Button danger size="small" loading={actionLoading === `clear-log:${item.name}`}>清空</Button>
                    </Popconfirm>
                  ]}
                >
                  <List.Item.Meta title={item.name} description={`${formatBytes(item.size_bytes)} · ${formatBeijingTime(item.modified_at)}`} />
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title={selectedLog ? `日志预览：${selectedLog.name}` : "日志预览"}>
            <Input.TextArea
              className="operationsLogPreview"
              readOnly
              value={selectedLog?.content || "选择左侧日志查看末尾内容。"}
              autoSize={{ minRows: 14, maxRows: 24 }}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
