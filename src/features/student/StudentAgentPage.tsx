import { Alert, App, Button, Empty, Input, List, Modal, Select, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { Bot, Check, Image as ImageIcon, MessageSquarePlus, Pencil, Send, Trash2, Video, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AIGenerationJob, AgentArtifact, AgentConversation, AgentMessage } from "../../domain-types";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Title, Text } = Typography;
type AgentModelOption = { provider_id: number; provider_name: string; model: string };
type AgentWorkflowOption = { id: number; name: string };

function requestId() {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function StudentAgentPage({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { message, modal } = App.useApp();
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  const [models, setModels] = useState<AgentModelOption[]>([]);
  const [workflows, setWorkflows] = useState<AgentWorkflowOption[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [activeJob, setActiveJob] = useState<AIGenerationJob | null>(null);
  const [preview, setPreview] = useState<{ artifact: AgentArtifact; url: string } | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);

  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedId) || null,
    [conversations, selectedId],
  );

  const loadConversation = useCallback(async (conversationId: number) => {
    const [conversationResponse, jobsResponse] = await Promise.all([
      api.get(`/api/agent/conversations/${conversationId}`),
      api.get("/api/ai/jobs"),
    ]);
    setMessages(conversationResponse.data.messages || []);
    setArtifacts(conversationResponse.data.artifacts || []);
    const conversationJob = (jobsResponse.data.jobs || []).find((item: AIGenerationJob) => (
      item.conversation_id === conversationId
      && ["agent_message", "agent_tool", "agent_workflow"].includes(item.operation)
    ));
    setActiveJob(conversationJob || null);
  }, []);

  const loadConversations = useCallback(async () => {
    const [conversationResponse, modelResponse, workflowResponse] = await Promise.all([
      api.get("/api/agent/conversations"),
      api.get("/api/agent/models"),
      api.get("/api/workflows"),
    ]);
    const nextConversations = conversationResponse.data.conversations || [];
    setConversations(nextConversations);
    setModels(modelResponse.data.models || []);
    setWorkflows((workflowResponse.data.workflows || []).map((item: AgentWorkflowOption) => ({ id: item.id, name: item.name })));
    setSelectedId((current) => current && nextConversations.some((item: AgentConversation) => item.id === current)
      ? current
      : nextConversations[0]?.id || null);
  }, []);

  useEffect(() => {
    setLoading(true);
    void loadConversations()
      .catch((error) => message.error(explainError(error)))
      .finally(() => setLoading(false));
  }, [loadConversations, message]);

  useEffect(() => {
    if (!selectedId) {
      setMessages([]);
      setArtifacts([]);
      setActiveJob(null);
      return;
    }
    setLoading(true);
    void loadConversation(selectedId)
      .catch((error) => message.error(explainError(error)))
      .finally(() => setLoading(false));
  }, [loadConversation, message, selectedId]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    if (!activeJob || ["succeeded", "failed", "timed_out", "canceled"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => {
      void api.get(`/api/ai/jobs/${activeJob.id}`).then(async (response) => {
        const nextJob = response.data.job as AIGenerationJob;
        setActiveJob(nextJob);
        if (["succeeded", "failed", "timed_out", "canceled"].includes(nextJob.status)) {
          if (selectedId) await loadConversation(selectedId);
          await loadConversations();
          if (nextJob.status === "succeeded") void onRefresh();
        }
      }).catch(() => undefined);
    }, 1_800);
    return () => window.clearInterval(timer);
  }, [activeJob, loadConversation, loadConversations, onRefresh, selectedId]);

  useEffect(() => () => {
    if (preview?.url) URL.revokeObjectURL(preview.url);
  }, [preview]);

  const createConversation = async () => {
    try {
      const response = await api.post("/api/agent/conversations", { title: "新对话", selected_provider_id: null });
      const conversation = response.data.conversation as AgentConversation;
      setConversations((current) => [conversation, ...current]);
      setSelectedId(conversation.id);
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const updateConversationModel = async (value: "auto" | number) => {
    if (!selectedConversation) return;
    try {
      const response = await api.put(`/api/agent/conversations/${selectedConversation.id}`, {
        selected_provider_id: value === "auto" ? null : value,
      });
      setConversations((current) => current.map((item) => item.id === selectedConversation.id ? response.data.conversation : item));
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const renameConversation = () => {
    if (!selectedConversation) return;
    let nextTitle = selectedConversation.title;
    modal.confirm({
      title: "重命名会话",
      content: <Input defaultValue={nextTitle} maxLength={160} onChange={(event) => { nextTitle = event.target.value; }} />,
      okText: "保存",
      cancelText: "取消",
      onOk: async () => {
        const title = nextTitle.trim();
        if (!title) throw new Error("请输入会话名称");
        const response = await api.put(`/api/agent/conversations/${selectedConversation.id}`, { title });
        setConversations((current) => current.map((item) => item.id === selectedConversation.id ? response.data.conversation : item));
      },
    });
  };

  const deleteConversation = () => {
    if (!selectedConversation) return;
    modal.confirm({
      title: "删除当前会话？",
      content: "会话会从列表中移除，已保存到作品库的文件不受影响。",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await api.delete(`/api/agent/conversations/${selectedConversation.id}`);
        setConversations((current) => current.filter((item) => item.id !== selectedConversation.id));
        setSelectedId((current) => conversations.find((item) => item.id !== current)?.id || null);
      },
    });
  };

  const sendMessage = async () => {
    const content = draft.trim();
    if (!selectedId || !content || activeJob && ["queued", "running"].includes(activeJob.status)) return;
    setDraft("");
    try {
      const response = await api.post(`/api/agent/conversations/${selectedId}/messages`, {
        content,
        client_request_id: requestId(),
      });
      setMessages((current) => [...current, response.data.user_message, response.data.assistant_message].filter(Boolean));
      setActiveJob(response.data.job);
    } catch (error) {
      setDraft(content);
      message.error(explainError(error));
    }
  };

  const confirmTool = (suggestion: NonNullable<AgentMessage["tool_suggestion"]>) => {
    if (!selectedId || !suggestion.capability || !suggestion.prompt) return;
    if (suggestion.capability === "workflow" && !workflows.length) {
      message.warning("当前没有可运行的工作流，请先在工作流生成中保存一个工作流。");
      return;
    }
    const labels = { image: "生成图片", video: "生成视频", workflow: "运行工作流" };
    let selectedWorkflowId = suggestion.capability === "workflow" ? workflows[0]?.id : undefined;
    modal.confirm({
      title: `确认${labels[suggestion.capability]}？`,
      content: suggestion.capability === "workflow" ? (
        <Space direction="vertical" size="middle" className="fullWidth">
          <Text>{suggestion.prompt}</Text>
          <Select
            defaultValue={selectedWorkflowId}
            className="fullWidth"
            options={workflows.map((item) => ({ value: item.id, label: item.name }))}
            onChange={(value) => { selectedWorkflowId = value; }}
          />
        </Space>
      ) : suggestion.prompt,
      okText: "确认执行",
      cancelText: "暂不执行",
      onOk: async () => {
        const response = await api.post(`/api/agent/conversations/${selectedId}/tools`, {
          capability: suggestion.capability,
          prompt: suggestion.prompt,
          client_request_id: requestId(),
          duration_seconds: 5,
          workflow_id: selectedWorkflowId,
        });
        setActiveJob(response.data.job);
      },
    });
  };

  const previewArtifact = async (artifact: AgentArtifact) => {
    try {
      const response = await api.get(`/api/agent/artifacts/${artifact.id}/file`, { responseType: "blob" });
      if (preview?.url) URL.revokeObjectURL(preview.url);
      setPreview({ artifact, url: URL.createObjectURL(response.data) });
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const saveArtifact = async (artifact: AgentArtifact) => {
    try {
      await api.post(`/api/agent/artifacts/${artifact.id}/save-project`, { title: artifact.title });
      message.success("已保存到我的作品");
      if (selectedId) await loadConversation(selectedId);
      await onRefresh();
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const cancelActiveJob = async () => {
    if (!activeJob) return;
    try {
      const response = await api.post(`/api/ai/jobs/${activeJob.id}/cancel`);
      setActiveJob(response.data.job);
      if (selectedId) await loadConversation(selectedId);
      message.info("已请求取消当前任务");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const retryActiveJob = async () => {
    if (!activeJob) return;
    try {
      const response = await api.post(`/api/ai/jobs/${activeJob.id}/retry`);
      setActiveJob(response.data.job);
      message.info("任务已重新进入队列");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const active = Boolean(activeJob && ["queued", "running"].includes(activeJob.status));

  return (
    <div className="agentPage">
      <aside className="agentConversationRail">
        <Button type="primary" icon={<MessageSquarePlus size={16} />} onClick={() => void createConversation()} block>新建会话</Button>
        <List
          dataSource={conversations}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" /> }}
          renderItem={(conversation) => (
            <List.Item
              className={conversation.id === selectedId ? "agentConversation active" : "agentConversation"}
              onClick={() => setSelectedId(conversation.id)}
            >
              <Space direction="vertical" size={2} className="fullWidth">
                <Text strong ellipsis={{ tooltip: conversation.title }}>{conversation.title}</Text>
                <Text type="secondary">{formatBeijingTime(conversation.updated_at)}</Text>
              </Space>
            </List.Item>
          )}
        />
      </aside>
      <main className="agentWorkspace">
        <header className="agentHeader">
          <div>
            <Title level={2}>AI 助手</Title>
            <Text type="secondary">当前会话会保留上下文；图片、视频和工作流必须由你确认后执行。</Text>
          </div>
          {selectedConversation && (
            <Space wrap>
              <Select
                value={selectedConversation.selected_provider_id || "auto"}
                onChange={(value) => void updateConversationModel(value as "auto" | number)}
                options={[
                  { value: "auto", label: "自动选择模型" },
                  ...models.map((item) => ({ value: item.provider_id, label: `${item.provider_name} · ${item.model}` })),
                ]}
                style={{ minWidth: 230 }}
              />
              <Tooltip title="重命名"><Button aria-label="重命名会话" icon={<Pencil size={16} />} onClick={renameConversation} /></Tooltip>
              <Tooltip title="删除"><Button aria-label="删除会话" danger icon={<Trash2 size={16} />} onClick={deleteConversation} /></Tooltip>
            </Space>
          )}
        </header>
        {!selectedConversation ? (
          <div className="agentEmpty"><Empty description="新建一个会话开始学习" /></div>
        ) : (
          <>
            <div className="agentMessages" aria-live="polite">
              {loading && <Spin />}
              {!loading && !messages.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="可以从课程问题、创意构思或代码思路开始" />}
              {messages.map((item) => (
                <article key={item.id} className={`agentMessage ${item.role}`}>
                  <div className="agentAvatar">{item.role === "assistant" ? <Bot size={18} /> : "我"}</div>
                  <div className="agentBubble">
                    {item.status === "pending" ? <Space><Spin size="small" /><Text>正在思考...</Text></Space> : (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content || "回复失败，请重试。"}</ReactMarkdown>
                    )}
                    {item.tool_suggestion?.capability && item.tool_suggestion.prompt && (
                      <Button
                        className="agentToolConfirm"
                        icon={item.tool_suggestion.capability === "image" ? <ImageIcon size={15} /> : item.tool_suggestion.capability === "video" ? <Video size={15} /> : <Workflow size={15} />}
                        onClick={() => confirmTool(item.tool_suggestion!)}
                      >
                        确认{item.tool_suggestion.capability === "image" ? "生成图片" : item.tool_suggestion.capability === "video" ? "生成视频" : "运行工作流"}
                      </Button>
                    )}
                  </div>
                </article>
              ))}
              {artifacts.map((artifact) => (
                <article key={`artifact-${artifact.id}`} className="agentArtifact">
                  <Space wrap>
                    {artifact.artifact_type === "image" ? <ImageIcon size={17} /> : <Video size={17} />}
                    <Text strong>{artifact.title}</Text>
                    <Tag color={artifact.status === "available" ? "green" : artifact.status === "saved" ? "blue" : "gold"}>
                      {artifact.status === "available" ? "临时文件" : artifact.status === "saved" ? "已保存" : artifact.status === "pending_review" ? "等待审核" : artifact.status}
                    </Tag>
                    {artifact.expires_at && !artifact.saved_project_id && <Text type="secondary">保留至 {formatBeijingTime(artifact.expires_at)}</Text>}
                    <Button size="small" disabled={!artifact.file_available || artifact.status === "pending_review"} onClick={() => void previewArtifact(artifact)}>预览</Button>
                    {!artifact.saved_project_id && <Button size="small" type="primary" icon={<Check size={14} />} disabled={artifact.status !== "available"} onClick={() => void saveArtifact(artifact)}>保存到我的作品</Button>}
                  </Space>
                </article>
              ))}
              {activeJob && active && (
                <Alert
                  type="info"
                  showIcon
                  message={activeJob.status === "queued" ? "任务正在排队" : "模型正在生成"}
                  description="任务由云端继续执行，刷新页面或切换会话不会中断。"
                  action={<Button onClick={() => void cancelActiveJob()}>取消任务</Button>}
                />
              )}
              {activeJob && ["failed", "timed_out", "canceled"].includes(activeJob.status) && (
                <Alert
                  type="error"
                  showIcon
                  message={activeJob.error_message || (activeJob.status === "canceled" ? "任务已取消" : "任务失败")}
                  description={activeJob.error_code || activeJob.status}
                  action={<Button danger onClick={() => void retryActiveJob()}>重试</Button>}
                />
              )}
              <div ref={messageEndRef} />
            </div>
            <div className="agentComposer">
              <Input.TextArea
                value={draft}
                autoSize={{ minRows: 2, maxRows: 6 }}
                maxLength={50_000}
                placeholder="输入学习问题，Enter 发送，Shift+Enter 换行"
                onChange={(event) => setDraft(event.target.value)}
                onPressEnter={(event) => {
                  if (!event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
              />
              <Button type="primary" icon={<Send size={17} />} disabled={!draft.trim() || active} loading={active} onClick={() => void sendMessage()}>发送</Button>
            </div>
          </>
        )}
      </main>
      <Modal
        open={Boolean(preview)}
        title={preview?.artifact.title}
        footer={null}
        width={900}
        destroyOnClose
        onCancel={() => {
          if (preview?.url) URL.revokeObjectURL(preview.url);
          setPreview(null);
        }}
      >
        {preview?.artifact.artifact_type === "image" && <img className="agentArtifactPreview" src={preview.url} alt={preview.artifact.title} />}
        {preview?.artifact.artifact_type === "video" && <video className="generatedVideo" src={preview.url} controls />}
      </Modal>
    </div>
  );
}
