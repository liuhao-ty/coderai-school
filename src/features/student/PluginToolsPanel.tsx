import { Alert, App as AntApp, Button, Card, Input, List, Space, Tag, Typography } from "antd";
import { Play, Puzzle } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";


const { Text } = Typography;

type PluginTool = {
  id: string;
  name: string;
  description: string;
  action: string;
};

type StudentPlugin = {
  id: string;
  name: string;
  description: string;
  version: string;
  publisher: { name: string } | null;
  tools: PluginTool[];
};

export function PluginToolsPanel({ supportsText, onRefresh }: { supportsText: boolean; onRefresh: () => Promise<void> }) {
  const { message } = AntApp.useApp();
  const [plugins, setPlugins] = useState<StudentPlugin[]>([]);
  const [prompts, setPrompts] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, string>>({});
  const [runningTool, setRunningTool] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get("/api/plugins/catalog");
        setPlugins(res.data.plugins || []);
      } catch {
        setPlugins([]);
      }
    };
    void load();
  }, []);

  const runTool = async (plugin: StudentPlugin, tool: PluginTool) => {
    const key = `${plugin.id}/${tool.id}`;
    const prompt = (prompts[key] || "").trim();
    if (!prompt) {
      message.warning("请先输入要处理的内容");
      return;
    }
    setRunningTool(key);
    setResults((current) => ({ ...current, [key]: "" }));
    try {
      const res = await api.post(`/api/plugins/${plugin.id}/tools/${tool.id}/run`, { prompt, save_project: true });
      setResults((current) => ({ ...current, [key]: res.data.text || "" }));
      await onRefresh();
      message.success(res.data.project ? "插件结果已保存到作品库" : "插件运行完成");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setRunningTool("");
    }
  };

  const tools = plugins.flatMap((plugin) => plugin.tools.map((tool) => ({ plugin, tool })));
  if (!tools.length) return null;

  return (
    <Card className="mb16" title={<IconTitle icon={<Puzzle size={18} />} text="课堂插件工具" />}>
      {!supportsText && <Alert className="mb16" type="warning" showIcon message="插件工具需要文字模型，请联系教师配置 AI 服务" />}
      <List
        dataSource={tools}
        renderItem={({ plugin, tool }) => {
          const key = `${plugin.id}/${tool.id}`;
          return (
            <List.Item>
              <Space direction="vertical" size={10} className="fullWidth">
                <Space wrap>
                  <Text strong>{tool.name}</Text>
                  <Tag color="blue">{plugin.name}</Tag>
                  <Tag>{plugin.version}</Tag>
                  {plugin.publisher && <Text type="secondary">发布者：{plugin.publisher.name}</Text>}
                </Space>
                {tool.description && <Text>{tool.description}</Text>}
                <Input.TextArea
                  rows={3}
                  aria-label={`${tool.name}输入内容`}
                  value={prompts[key] || ""}
                  onChange={(event) => setPrompts((current) => ({ ...current, [key]: event.target.value }))}
                  placeholder="输入课堂问题、创意或需要处理的内容"
                  maxLength={4000}
                  showCount
                />
                <Button
                  type="primary"
                  icon={<Play size={16} />}
                  loading={runningTool === key}
                  disabled={!supportsText}
                  onClick={() => void runTool(plugin, tool)}
                >
                  运行并保存
                </Button>
                {results[key] && <pre className="resultText">{results[key]}</pre>}
              </Space>
            </List.Item>
          );
        }}
      />
    </Card>
  );
}
