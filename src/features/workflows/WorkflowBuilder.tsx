import {
  Alert, App as AntApp, Button, Card, Col, Form, Input, List, Popconfirm, Row, Select, Space, Tag, Tooltip, Typography
} from "antd";
import dayjs from "dayjs";
import { FileDown, FileUp, GitBranch, Play, Plus, Save, Trash2, Unlink, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import ReactFlow, { addEdge, applyEdgeChanges, Background, Connection, Controls, Edge, EdgeChange, Node } from "reactflow";
import remarkGfm from "remark-gfm";

import { IconTitle } from "../../components/IconTitle";
import type {
  Classroom,
  Project,
  ProviderCapability,
  ProviderState,
  SavedWorkflow,
  SystemModelOption,
  WorkflowRunItem,
  WorkflowRunResponse,
  WorkflowTemplate,
} from "../../domain-types";
import { api } from "../../lib/api";
import { saveTextFile } from "../../lib/downloads";
import { workflowRunStatusColor, workflowRunStatusLabel } from "../../lib/domain";
import { errorCode, explainError } from "../../lib/errors";
import { formatBeijingTime, parseJsonObject } from "../../lib/format";


const { Title, Text, Paragraph } = Typography;

function workflowPathExists(
  start: string,
  target: string,
  edges: SavedWorkflow["definition"]["edges"],
) {
  const successors = new Map<string, string[]>();
  edges.forEach((edge) => successors.set(edge.source, [...(successors.get(edge.source) || []), edge.target]));
  const visited = new Set<string>();
  const pending = [start];
  while (pending.length) {
    const current = pending.shift()!;
    if (current === target) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    pending.push(...(successors.get(current) || []));
  }
  return false;
}

export function WorkflowBuilder({
  onRefresh,
  provider,
  classrooms,
  isTeacher
}: {
  onRefresh: () => Promise<void>;
  provider: ProviderState;
  classrooms: Classroom[];
  isTeacher: boolean;
}) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm<{ template_id: string; prompt: string }>();
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [modelCatalog, setModelCatalog] = useState<SystemModelOption[]>([]);
  const [savedWorkflows, setSavedWorkflows] = useState<SavedWorkflow[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("text_to_image");
  const [editingWorkflow, setEditingWorkflow] = useState<SavedWorkflow | null>(null);
  const [draftName, setDraftName] = useState("我的工作流");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftStatus, setDraftStatus] = useState("draft");
  const [draftClassroomId, setDraftClassroomId] = useState<number | undefined>();
  const [draftNodes, setDraftNodes] = useState<SavedWorkflow["definition"]["nodes"]>([
    { id: "input", type: "input", label: "学生创意", position: { x: 20, y: 80 } }
  ]);
  const [draftEdges, setDraftEdges] = useState<SavedWorkflow["definition"]["edges"]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("input");
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const [savingWorkflow, setSavingWorkflow] = useState(false);
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);
  const [errorText, setErrorText] = useState("");
  const [lastPrompt, setLastPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [runs, setRuns] = useState<WorkflowRunItem[]>([]);
  const [currentRun, setCurrentRun] = useState<WorkflowRunItem | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const pollGeneration = useRef(0);
  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId);
  const selectedSavedWorkflow = selectedTemplateId.startsWith("saved:")
    ? savedWorkflows.find((item) => item.id === Number(selectedTemplateId.slice(6)))
    : undefined;
  const editingDraftGraph = Boolean(editingWorkflow) || draftNodes.length > 1;
  const displayDefinitionNodes = editingDraftGraph
    ? draftNodes
    : selectedSavedWorkflow?.definition.nodes;
  const selectedDefinitionNodes = selectedSavedWorkflow?.definition.nodes || selectedTemplate?.nodes || [];
  const workflowNeedsText = selectedDefinitionNodes.some((node) => node.type === "text.generate");
  const workflowNeedsImage = selectedTemplateId === "text_to_image"
    || selectedDefinitionNodes.some((node) => node.type === "image.generate");
  const workflowHasText = modelCatalog.some((item) => item.capability === "text" && item.available);
  const workflowHasImage = modelCatalog.some((item) => item.capability === "image" && item.available);
  const workflowAvailable = !catalogLoading
    && !catalogError
    && (!workflowNeedsText || workflowHasText)
    && (!workflowNeedsImage || workflowHasImage);
  const workflowUnavailableMessage = catalogLoading
    ? "正在读取可用模型"
    : catalogError
      ? `模型目录暂时无法读取：${catalogError}`
      : workflowNeedsText && !workflowHasText
        ? "文字路由中没有可用模型，请联系管理员检查模型服务"
        : workflowNeedsImage && !workflowHasImage
          ? "图片路由中没有可用模型，请联系管理员检查即梦等图片服务"
          : !provider.configured
            ? "管理员尚未配置模型服务"
            : "";
  const selectedModelItem = (node: SavedWorkflow["definition"]["nodes"][number], capability: ProviderCapability) => {
    const providerId = Number(node.params?.provider_id || 0);
    const model = String(node.params?.model || "");
    return modelCatalog.find((item) => item.capability === capability && item.provider_id === providerId && item.model === model);
  };
  const selectedModelValue = (node: SavedWorkflow["definition"]["nodes"][number], capability: ProviderCapability) => {
    const selected = selectedModelItem(node, capability);
    if (selected) return selected.key;
    const providerId = Number(node.params?.provider_id || 0);
    const model = String(node.params?.model || "");
    return providerId && model ? `stale:${providerId}:${capability}:${model}` : "auto";
  };
  const modelSelectOptions = (node: SavedWorkflow["definition"]["nodes"][number], capability: ProviderCapability) => {
    const options = [
      { value: "auto", label: "系统自动路由", disabled: false },
      ...modelCatalog
        .filter((item) => item.capability === capability)
        .map((item) => ({
          value: item.key,
          label: item.available
            ? `${item.provider_name} · ${item.display_name} · ${item.configuration_name}`
            : `${item.provider_name} · ${item.display_name}（${item.reason}）`,
          disabled: !item.available,
        })),
    ];
    const current = selectedModelValue(node, capability);
    if (current.startsWith("stale:")) {
      options.unshift({ value: current, label: `${String(node.params?.model || "原模型")}（配置已失效）`, disabled: true });
    }
    return options;
  };
  const nodes: Node[] = useMemo(() => {
    const sourceNodes = displayDefinitionNodes || selectedTemplate?.nodes || [
        { id: "input", type: "input", label: "学生创意" },
        { id: "text", type: "text.generate", label: "提示词优化" },
        { id: "image", type: "image.generate", label: "生成图片" }
      ];
    return sourceNodes.map((node, index, allNodes) => {
      const state = currentRun?.node_states?.[node.id];
      const capability: ProviderCapability | null = node.type === "text.generate" ? "text" : node.type === "image.generate" ? "image" : null;
      const selectedModel = capability ? selectedModelItem(node, capability) : null;
      const colors: Record<string, { border: string; background: string }> = {
        pending: { border: "#bfbfbf", background: "#fafafa" },
        running: { border: "#1677ff", background: "#e6f4ff" },
        success: { border: "#52c41a", background: "#f6ffed" },
        failed: { border: "#ff4d4f", background: "#fff2f0" },
        blocked: { border: "#fa8c16", background: "#fff7e6" },
        canceled: { border: "#8c8c8c", background: "#f5f5f5" }
      };
      return {
        id: node.id,
        position: (node as { position?: { x: number; y: number } }).position || { x: 20 + index * 240, y: 80 },
        data: {
          label: (
            <div className="workflowCanvasNodeLabel">
              <Space size={6}><span>{node.label}</span>{state?.cached && <Tag color="blue">缓存</Tag>}</Space>
              {capability && <span className="workflowCanvasModelName">{selectedModel ? selectedModel.display_name : "系统自动路由"}</span>}
            </div>
          ),
        },
        type: node.type === "input" ? "input" : node.type === "image.generate" ? "output" : undefined,
        selected: node.id === selectedNodeId,
        style: state ? { border: `2px solid ${colors[state.status]?.border || "#bfbfbf"}`, background: colors[state.status]?.background || "#fff" } : undefined
      };
    });
  }, [currentRun, displayDefinitionNodes, modelCatalog, selectedNodeId, selectedTemplate]);
  const edges: Edge[] = useMemo(() => {
    if (editingDraftGraph) {
      return draftEdges.map((edge) => ({
        ...edge,
        selected: edge.id === selectedEdgeId,
        animated: edge.id === selectedEdgeId,
      }));
    }
    if (selectedSavedWorkflow) return selectedSavedWorkflow.definition.edges;
    return nodes.slice(0, -1).map((node, index) => ({ id: `e${node.id}-${nodes[index + 1].id}`, source: node.id, target: nodes[index + 1].id }));
  }, [draftEdges, editingDraftGraph, nodes, selectedEdgeId, selectedSavedWorkflow]);

  const loadSavedWorkflows = () => {
    api.get("/api/workflows").then((res) => setSavedWorkflows(res.data.workflows || [])).catch(() => undefined);
  };

  useEffect(() => {
    api
      .get("/api/workflows/templates")
      .then((res) => {
        setTemplates(res.data.templates || []);
        const firstTemplate = res.data.templates?.[0];
        if (firstTemplate) {
          setSelectedTemplateId(firstTemplate.id);
          form.setFieldsValue({ template_id: firstTemplate.id });
        }
      })
      .catch(() => undefined);
    loadWorkflowRuns();
    loadSavedWorkflows();
  }, [form]);

  useEffect(() => {
    setCatalogLoading(true);
    setCatalogError("");
    api.get("/api/models/catalog")
      .then((res) => setModelCatalog(res.data.models || []))
      .catch((error) => {
        setModelCatalog([]);
        setCatalogError(explainError(error));
      })
      .finally(() => setCatalogLoading(false));
  }, []);

  const loadWorkflowRuns = () => {
    api.get("/api/workflows/runs").then((res) => setRuns(res.data.runs || [])).catch(() => undefined);
  };

  const finishAsyncRun = async (runItem: WorkflowRunItem) => {
    setCurrentRun(runItem);
    setLoading(false);
    loadWorkflowRuns();
    if (runItem.status === "success" || runItem.status === "partial_failed") {
      const output = parseJsonObject(runItem.output_json) as WorkflowRunResponse["output"] & { project?: Project };
      setResult({ run_id: runItem.id, status: runItem.status, output, project: output.project || null });
      setErrorText("");
      await onRefresh();
      if (runItem.status === "partial_failed") {
        message.warning("工作流部分完成，可重试失败分支");
      } else {
        message.success("工作流运行完成");
      }
    } else if (runItem.status === "failed") {
      setErrorText(runItem.error_message || "工作流运行失败");
      message.error(runItem.error_message || "工作流运行失败");
    } else if (runItem.status === "canceled") {
      setErrorText(runItem.error_message || "工作流运行已取消");
      message.warning("工作流运行已取消");
    }
  };

  const watchWorkflowRun = async (runId: number, generation: number) => {
    while (pollGeneration.current === generation) {
      try {
        const res = await api.get(`/api/workflows/runs/${runId}`);
        const runItem = res.data.run as WorkflowRunItem;
        setCurrentRun(runItem);
        if (["success", "partial_failed", "failed", "canceled"].includes(runItem.status)) {
          await finishAsyncRun(runItem);
          return;
        }
      } catch (error) {
        setLoading(false);
        setErrorText(explainError(error));
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 900));
    }
  };

  const run = async (values: { template_id?: string; prompt: string }) => {
    pollGeneration.current += 1;
    const generation = pollGeneration.current;
    setLoading(true);
    setResult(null);
    setCurrentRun(null);
    setErrorText("");
    setLastPrompt(values.prompt);
    const templateId = values.template_id || selectedTemplateId || "text_to_image";
    try {
      const workflowId = templateId.startsWith("saved:") ? Number(templateId.slice(6)) : undefined;
      const res = await api.post("/api/workflows/run-async", { template_id: workflowId ? "custom" : templateId, workflow_id: workflowId, prompt: values.prompt });
      const runItem = res.data.run as WorkflowRunItem;
      setCurrentRun(runItem);
      void watchWorkflowRun(runItem.id, generation);
      message.info("工作流已开始运行");
    } catch (error) {
      const detail = explainError(error);
      setLoading(false);
      setErrorText(detail);
      message.error(detail);
      loadWorkflowRuns();
    }
  };

  const cancelCurrentRun = async () => {
    if (!currentRun || !["pending", "running"].includes(currentRun.status)) return;
    try {
      await api.post(`/api/workflows/runs/${currentRun.id}/cancel`);
      message.info("已请求取消，将在当前节点结束后停止");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const retryWorkflowNode = async (nodeId: string) => {
    if (!currentRun) return;
    pollGeneration.current += 1;
    const generation = pollGeneration.current;
    setLoading(true);
    setErrorText("");
    setResult(null);
    try {
      const res = await api.post(`/api/workflows/runs/${currentRun.id}/retry-node`, { node_id: nodeId });
      setCurrentRun(res.data.run);
      void watchWorkflowRun(currentRun.id, generation);
      message.info("已从失败节点继续运行");
    } catch (error) {
      setLoading(false);
      message.error(explainError(error));
    }
  };

  const resetEditor = () => {
    setEditingWorkflow(null);
    setDraftName("我的工作流");
    setDraftDescription("");
    setDraftStatus("draft");
    setDraftClassroomId(undefined);
    setDraftNodes([{ id: "input", type: "input", label: "学生创意", position: { x: 20, y: 80 } }]);
    setDraftEdges([]);
    setSelectedNodeId("input");
    setSelectedEdgeId("");
  };

  const editWorkflow = (workflow: SavedWorkflow) => {
    setEditingWorkflow(workflow);
    setDraftName(workflow.name);
    setDraftDescription(workflow.description);
    setDraftStatus(workflow.status);
    setDraftClassroomId(workflow.classroom_id || undefined);
    setDraftNodes(workflow.definition.nodes);
    setDraftEdges(workflow.definition.edges);
    setSelectedNodeId("input");
    setSelectedEdgeId("");
    setSelectedTemplateId(`saved:${workflow.id}`);
    form.setFieldsValue({ template_id: `saved:${workflow.id}` });
  };

  const canConnectNodes = (source: string, target: string) => {
    const sourceNode = draftNodes.find((node) => node.id === source);
    if (!sourceNode || source === target || sourceNode.type === "image.generate" || target === "input") return false;
    const edgesWithoutPair = draftEdges.filter((edge) => !(edge.source === source && edge.target === target));
    return !workflowPathExists(target, source, edgesWithoutPair);
  };

  const connectWorkflowNodes = (connection: Connection) => {
    const source = String(connection.source || "");
    const target = String(connection.target || "");
    if (!source || !target || !canConnectNodes(source, target)) {
      message.warning("这条连线会形成循环，或使用了不能作为上游的图片节点");
      return;
    }
    if (draftEdges.some((edge) => edge.source === source && edge.target === target)) {
      message.info("这两个节点已经连接");
      return;
    }
    setDraftEdges((current) => addEdge({
      ...connection,
      id: `e-${source}-${target}-${Date.now()}`,
    }, current) as SavedWorkflow["definition"]["edges"]);
  };

  const addWorkflowNode = (type: "text.generate" | "image.generate") => {
    const id = `${type === "text.generate" ? "text" : "image"}-${Date.now()}`;
    const selectedNode = draftNodes.find((node) => node.id === selectedNodeId);
    if (selectedNode?.type === "image.generate") {
      message.info("图片节点是终端节点，请选择输入或文字节点后再添加分支");
      return;
    }
    const selectedSource = selectedNode
      || draftNodes.find((node) => node.type === "input")
      || draftNodes[0];
    const capability: ProviderCapability = type === "text.generate" ? "text" : "image";
    const defaultModel = modelCatalog.find((item) => item.capability === capability && item.available && item.provider_id);
    const modelParams = defaultModel ? {
      provider_id: defaultModel.provider_id,
      provider_type: defaultModel.provider_type,
      model: defaultModel.model,
    } : {};
    const node = {
      id,
      type,
      label: type === "text.generate" ? "文字生成" : "图片生成",
      params: type === "text.generate"
        ? { mode: "prompt_refine", ...modelParams }
        : { style: "classroom-friendly", size: "1024x1024", ...modelParams },
      position: {
        x: (selectedSource?.position?.x || 20) + 260,
        y: (selectedSource?.position?.y || 80)
          + draftEdges.filter((edge) => edge.source === selectedSource?.id).length * 150,
      }
    };
    setDraftNodes((current) => [...current, node]);
    if (selectedSource) {
      setDraftEdges((current) => [...current, {
        id: `e-${selectedSource.id}-${id}-${Date.now()}`,
        source: selectedSource.id,
        target: id,
      }]);
    }
    setSelectedNodeId(id);
    setSelectedEdgeId("");
  };

  const removeWorkflowNode = (nodeId: string) => {
    if (nodeId === "input") return;
    setDraftNodes((current) => current.filter((node) => node.id !== nodeId));
    setDraftEdges((current) => current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    setSelectedNodeId("input");
  };

  const updateNodeUpstreams = (nodeId: string, upstreamIds: string[]) => {
    const retained = draftEdges.filter((edge) => edge.target !== nodeId);
    const nextEdges = upstreamIds
      .filter((source) => canConnectNodes(source, nodeId))
      .map((source, index) => ({
        id: draftEdges.find((edge) => edge.source === source && edge.target === nodeId)?.id
          || `e-${source}-${nodeId}-${Date.now()}-${index}`,
        source,
        target: nodeId,
      }));
    setDraftEdges([...retained, ...nextEdges]);
    setSelectedEdgeId("");
  };

  const upstreamOptionsFor = (nodeId: string) => draftNodes
    .filter((candidate) => candidate.id !== nodeId && candidate.type !== "image.generate" && canConnectNodes(candidate.id, nodeId))
    .map((candidate) => ({ value: candidate.id, label: candidate.label }));

  const deleteSelectedEdge = () => {
    if (!selectedEdgeId) return;
    setDraftEdges((current) => current.filter((edge) => edge.id !== selectedEdgeId));
    setSelectedEdgeId("");
  };

  const updateWorkflowNode = (nodeId: string, patch: Partial<SavedWorkflow["definition"]["nodes"][number]>) => {
    setDraftNodes((current) => current.map((node) => node.id === nodeId ? { ...node, ...patch } : node));
  };

  const updateWorkflowNodeModel = (nodeId: string, capability: ProviderCapability, value: string) => {
    setDraftNodes((current) => current.map((node) => {
      if (node.id !== nodeId) return node;
      const params = { ...(node.params || {}) };
      delete params.provider_id;
      delete params.provider_type;
      delete params.model;
      if (value !== "auto") {
        const selected = modelCatalog.find((item) => item.key === value && item.capability === capability && item.available);
        if (selected?.provider_id) {
          params.provider_id = selected.provider_id;
          params.provider_type = selected.provider_type;
          params.model = selected.model;
        }
      }
      return { ...node, params };
    }));
  };

  const saveWorkflow = async () => {
    setSavingWorkflow(true);
    try {
      const payload = {
        name: draftName,
        description: draftDescription,
        status: isTeacher ? draftStatus : "draft",
        classroom_id: isTeacher ? draftClassroomId : undefined,
        definition: { nodes: draftNodes, edges: draftEdges }
      };
      const res = editingWorkflow
        ? await api.put(`/api/workflows/${editingWorkflow.id}`, payload)
        : await api.post("/api/workflows", payload);
      await loadSavedWorkflows();
      editWorkflow(res.data.workflow);
      message.success(isTeacher && draftStatus === "published" ? "工作流已发布" : "工作流草稿已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingWorkflow(false);
    }
  };

  const copyWorkflow = async (workflowId: number) => {
    try {
      const res = await api.post(`/api/workflows/${workflowId}/copy`);
      loadSavedWorkflows();
      editWorkflow(res.data.workflow);
      message.success("工作流已复制为草稿");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const deleteWorkflow = async (workflowId: number) => {
    try {
      await api.delete(`/api/workflows/${workflowId}`);
      resetEditor();
      loadSavedWorkflows();
      message.success("工作流已删除");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const exportWorkflow = async (workflow: SavedWorkflow) => {
    const packageData = {
      format: "coderai-workflow",
      package_version: 1,
      exported_at: dayjs().tz("Asia/Shanghai").format(),
      workflow: {
        name: workflow.name,
        description: workflow.description,
        version: workflow.version,
        definition: workflow.definition
      }
    };
    try {
      const filename = `${workflow.name.replace(/[\\/:*?"<>|]/g, "-") || "workflow"}.coderai-workflow.json`;
      if (await saveTextFile(JSON.stringify(packageData, null, 2), filename, "application/json")) {
        message.success("工作流模板已导出");
      }
    } catch (error) {
      message.error(`工作流模板导出失败：${explainError(error)}`);
    }
  };

  const importWorkflow = async (file: File) => {
    setSavingWorkflow(true);
    try {
      const parsed = JSON.parse(await file.text());
      if (parsed.format !== "coderai-workflow" || Number(parsed.package_version) !== 1 || !parsed.workflow?.definition) {
        throw new Error("不是受支持的 CoderAI 工作流模板文件");
      }
      const imported = parsed.workflow;
      const res = await api.post("/api/workflows", {
        name: `${String(imported.name || "导入工作流")} - 导入`,
        description: String(imported.description || ""),
        status: "draft",
        classroom_id: null,
        definition: imported.definition
      });
      loadSavedWorkflows();
      editWorkflow(res.data.workflow);
      message.success("工作流模板已导入为草稿");
    } catch (error) {
      message.error(error instanceof SyntaxError ? "工作流模板 JSON 格式不正确" : explainError(error));
    } finally {
      setSavingWorkflow(false);
    }
  };

  const retry = () => {
    if (!lastPrompt) return;
    form.setFieldsValue({ template_id: selectedTemplateId, prompt: lastPrompt });
    run({ template_id: selectedTemplateId, prompt: lastPrompt });
  };

  return (
    <div className="page">
      <div className="pageTitle">
        <Title level={2}>工作流制作</Title>
        <Text>{selectedSavedWorkflow?.description || selectedTemplate?.description || "编辑节点、保存草稿，并把流程发布到课堂。"}</Text>
      </div>
      <Card className="mb16" title="工作流编辑器" extra={<Button icon={<Plus size={16} />} onClick={resetEditor}>新建</Button>}>
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={8}><Input value={draftName} onChange={(event) => setDraftName(event.target.value)} placeholder="工作流名称" /></Col>
          <Col xs={24} lg={8}><Input value={draftDescription} onChange={(event) => setDraftDescription(event.target.value)} placeholder="工作流说明" /></Col>
          {isTeacher && <Col xs={12} lg={4}><Select className="fullWidth" value={draftStatus} onChange={setDraftStatus} options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }]} /></Col>}
          {isTeacher && <Col xs={12} lg={4}><Select className="fullWidth" allowClear value={draftClassroomId} onChange={setDraftClassroomId} placeholder="全部班级" options={classrooms.map((item) => ({ value: item.id, label: item.name }))} /></Col>}
        </Row>
        <Space wrap className="mt16">
          <Button icon={<GitBranch size={16} />} onClick={() => addWorkflowNode("text.generate")}>添加文字分支</Button>
          <Button icon={<GitBranch size={16} />} onClick={() => addWorkflowNode("image.generate")}>添加图片分支</Button>
          <Tooltip title="先在画布中选择连线">
            <Button danger icon={<Unlink size={16} />} disabled={!selectedEdgeId} onClick={deleteSelectedEdge}>删除连线</Button>
          </Tooltip>
          <Button type="primary" icon={<Save size={16} />} loading={savingWorkflow} disabled={!draftName.trim()} onClick={saveWorkflow}>保存{isTeacher && draftStatus === "published" ? "并发布" : "草稿"}</Button>
          <Button icon={<FileUp size={16} />} loading={savingWorkflow} onClick={() => document.getElementById(`workflow-import-${isTeacher ? "teacher" : "student"}`)?.click()}>导入模板</Button>
          <input
            id={`workflow-import-${isTeacher ? "teacher" : "student"}`}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importWorkflow(file);
              event.currentTarget.value = "";
            }}
          />
        </Space>
        <List
          className="mt16 workflowNodeList"
          size="small"
          dataSource={draftNodes}
          renderItem={(node) => (
            <List.Item
              className={node.id === selectedNodeId ? "workflowNodeListItem selected" : "workflowNodeListItem"}
              onClick={() => { setSelectedNodeId(node.id); setSelectedEdgeId(""); }}
              actions={node.type !== "input" ? [<Button key="delete" danger size="small" icon={<Trash2 size={13} />} onClick={(event) => { event.stopPropagation(); removeWorkflowNode(node.id); }}>删除节点</Button>] : []}
            >
              <Space wrap className="fullWidth">
                <Tag>{node.type === "input" ? "输入" : node.type === "text.generate" ? "文字" : "图片"}</Tag>
                <Input className="workflowNodeName" value={node.label} onChange={(event) => updateWorkflowNode(node.id, { label: event.target.value })} />
                {node.type !== "input" && (
                  <Select
                    className="workflowNodeUpstreamSelect"
                    mode="multiple"
                    maxTagCount="responsive"
                    aria-label={`${node.label}的上游节点`}
                    placeholder="选择一个或多个上游节点"
                    value={draftEdges.filter((edge) => edge.target === node.id).map((edge) => edge.source)}
                    options={upstreamOptionsFor(node.id)}
                    onChange={(values) => updateNodeUpstreams(node.id, values)}
                    onClick={(event) => event.stopPropagation()}
                  />
                )}
                {(node.type === "text.generate" || node.type === "image.generate") && (
                  <Select
                    className="workflowNodeModelSelect"
                    aria-label={`${node.label}使用模型`}
                    showSearch
                    optionFilterProp="label"
                    value={selectedModelValue(node, node.type === "text.generate" ? "text" : "image")}
                    options={modelSelectOptions(node, node.type === "text.generate" ? "text" : "image")}
                    onChange={(value) => updateWorkflowNodeModel(node.id, node.type === "text.generate" ? "text" : "image", value)}
                  />
                )}
                {node.type === "text.generate" && (
                  <Select
                    value={String(node.params?.mode || "prompt_refine")}
                    onChange={(value) => updateWorkflowNode(node.id, { params: { ...node.params, mode: value } })}
                    options={[
                      { value: "prompt_refine", label: "提示词优化" },
                      { value: "story", label: "故事扩写" },
                      ...(isTeacher ? [{ value: "code_explain", label: "代码解释" }] : []),
                    ]}
                  />
                )}
                {node.type === "image.generate" && (
                  <Select
                    value={String(node.params?.style || "classroom-friendly")}
                    onChange={(value) => updateWorkflowNode(node.id, { params: { ...node.params, style: value, size: "1024x1024" } })}
                    options={[{ value: "classroom-friendly", label: "课堂友好" }, { value: "明亮卡通", label: "明亮卡通" }, { value: "科技课堂", label: "科技课堂" }]}
                  />
                )}
              </Space>
            </List.Item>
          )}
        />
        <List
          className="mt16"
          size="small"
          dataSource={savedWorkflows}
          locale={{ emptyText: "还没有保存的工作流" }}
          renderItem={(workflow) => (
            <List.Item actions={[
              ...(isTeacher || workflow.owner_user_id ? [<Button key="edit" size="small" onClick={() => editWorkflow(workflow)}>编辑</Button>] : []),
              <Button key="export" size="small" icon={<FileDown size={14} />} onClick={() => void exportWorkflow(workflow)}>导出</Button>,
              <Button key="copy" size="small" onClick={() => copyWorkflow(workflow.id)}>复制</Button>,
              ...(isTeacher || workflow.owner_user_id ? [<Popconfirm key="delete" title="删除这个工作流？" onConfirm={() => deleteWorkflow(workflow.id)}><Button danger size="small">删除</Button></Popconfirm>] : [])
            ]}>
              <List.Item.Meta title={<Space wrap><Text strong>{workflow.name}</Text><Tag color={workflow.status === "published" ? "green" : "default"}>{workflow.status === "published" ? "已发布" : "草稿"}</Tag><Tag>v{workflow.version}</Tag></Space>} description={`${workflow.owner_name}${workflow.classroom_name ? ` · ${workflow.classroom_name}` : " · 全部班级"}`} />
            </List.Item>
          )}
        />
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className="flowCard">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              fitView
              deleteKeyCode={["Backspace", "Delete"]}
              nodesDraggable={editingDraftGraph}
              onConnect={(connection: Connection) => {
                if (editingDraftGraph) connectWorkflowNodes(connection);
              }}
              onEdgesChange={(changes: EdgeChange[]) => {
                if (!editingDraftGraph) return;
                setDraftEdges((current) => applyEdgeChanges(changes, current) as SavedWorkflow["definition"]["edges"]);
              }}
              onEdgesDelete={(deletedEdges) => {
                if (!editingDraftGraph) return;
                const deletedIds = new Set(deletedEdges.map((edge) => edge.id));
                setDraftEdges((current) => current.filter((edge) => !deletedIds.has(edge.id)));
                setSelectedEdgeId("");
              }}
              onNodeClick={(_, node) => {
                if (editingDraftGraph) {
                  setSelectedNodeId(node.id);
                  setSelectedEdgeId("");
                }
              }}
              onEdgeClick={(_, edge) => {
                if (editingDraftGraph) {
                  setSelectedEdgeId(edge.id);
                  setSelectedNodeId("");
                }
              }}
              onPaneClick={() => setSelectedEdgeId("")}
              onNodeDragStop={(_, node) => {
                if (editingDraftGraph) {
                  setDraftNodes((current) => current.map((item) => item.id === node.id ? { ...item, position: node.position } : item));
                }
              }}
            >
              <Background />
              <Controls />
            </ReactFlow>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title={<IconTitle icon={<Workflow size={18} />} text="运行工作流" />}>
            {!workflowAvailable && <Alert className="mb16" type="warning" showIcon message={workflowUnavailableMessage} />}
            <Form form={form} layout="vertical" onFinish={run} initialValues={{ template_id: selectedTemplateId }}>
              <Form.Item name="template_id" label="工作流模板">
                <Select
                  value={selectedTemplateId}
                  onChange={(value) => setSelectedTemplateId(value)}
                  options={[
                    ...templates.map((template) => ({ value: template.id, label: `内置 · ${template.name}` })),
                    ...savedWorkflows.map((workflow) => ({ value: `saved:${workflow.id}`, label: `${workflow.status === "published" ? "已发布" : "草稿"} · ${workflow.name}` }))
                  ]}
                />
              </Form.Item>
              <Form.Item name="prompt" label="学生创意" rules={[{ required: true, message: "请输入工作流输入" }]}>
                <Input.TextArea rows={5} placeholder="例如：生成一个AI音乐课的项目封面" />
              </Form.Item>
              <Space wrap>
                <Button icon={<Play size={16} />} type="primary" htmlType="submit" loading={loading} disabled={!workflowAvailable}>运行</Button>
                {currentRun && ["pending", "running"].includes(currentRun.status) && <Button danger onClick={cancelCurrentRun}>取消运行</Button>}
              </Space>
            </Form>
            {currentRun && (
              <Card className="mt16" size="small" title={<Space><span>节点状态</span><Tag color={workflowRunStatusColor(currentRun.status)}>{workflowRunStatusLabel(currentRun.status)}</Tag></Space>}>
                <List
                  size="small"
                  dataSource={Object.entries(currentRun.node_states || {})}
                  renderItem={([nodeId, state]) => (
                    <List.Item actions={state.status === "failed" || state.status === "canceled" ? [<Button key="retry" size="small" onClick={() => retryWorkflowNode(nodeId)}>重试此节点及后代</Button>] : []}>
                      <Space wrap>
                        <Tag color={workflowRunStatusColor(state.status)}>{workflowRunStatusLabel(state.status)}</Tag>
                        <Text>{state.label}</Text>
                        {state.cached && <Tag color="blue">使用缓存</Tag>}
                        {state.error && <Text type="danger">{state.error}</Text>}
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            )}
            {errorText && (
              <Alert
                className="mt16"
                type="error"
                showIcon
                message="工作流运行失败"
                description={
                  <Space direction="vertical" size={10}>
                    <Text>{errorText}</Text>
                    <Button icon={<Play size={16} />} onClick={retry} loading={loading}>
                      重试
                    </Button>
                  </Space>
                }
              />
            )}
            {result && (
              <Space direction="vertical" size={16} className="fullWidth mt16">
                <Alert
                  type={result.status === "partial_failed" || (result.output.image?.moderation_status && result.output.image.moderation_status !== "approved") ? "warning" : "success"}
                  showIcon
                  message={
                    result.status === "partial_failed"
                      ? "工作流部分完成"
                      : result.output.image?.moderation_status && result.output.image.moderation_status !== "approved"
                        ? "工作流完成，图片等待教师复核"
                        : "工作流运行完成"
                  }
                  description={
                    result.status === "partial_failed"
                      ? "成功分支已保留并保存，可在节点状态中重试失败节点。"
                      : result.output.image?.moderation_message || (result.project ? `已保存到作品库：${result.project.title}` : "运行结果已生成。")
                  }
                />
                {(() => {
                  const terminalTexts = (result.output.terminal_outputs || []).filter(
                    (item) => item.status === "success" && typeof item.output === "string",
                  );
                  if (!terminalTexts.length) {
                    return (
                      <Card size="small" title={result.output.output_title || "工作流输出"}>
                        <article className="markdownPreview workflowResultPreview">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.output.refined_prompt || result.output.text || ""}</ReactMarkdown>
                        </article>
                      </Card>
                    );
                  }
                  return terminalTexts.map((item) => (
                    <Card key={item.node_id} size="small" title={item.label}>
                      <article className="markdownPreview workflowResultPreview">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{String(item.output || "")}</ReactMarkdown>
                      </article>
                    </Card>
                  ));
                })()}
                {(result.output.images || (result.output.image ? [result.output.image] : []))
                  .filter((image) => image.url)
                  .map((image, index) => (
                    <img key={`${image.url}-${index}`} className="generatedImage" src={image.url} alt={`工作流生成图片 ${index + 1}`} />
                  ))}
              </Space>
            )}
          </Card>
        </Col>
      </Row>
      <Card className="mt16" title="运行历史">
        <List
          dataSource={runs}
          locale={{ emptyText: "还没有运行记录" }}
          renderItem={(item) => {
            const input = parseJsonObject(item.input_json);
            const output = parseJsonObject(item.output_json);
            return (
              <List.Item actions={[<Button key="reuse" size="small" onClick={() => {
                const prompt = String(input.prompt || "");
                const templateId = String(input.template_id || selectedTemplateId);
                setSelectedTemplateId(templateId);
                form.setFieldsValue({ template_id: templateId, prompt });
              }}>再次编辑</Button>]}> 
                <List.Item.Meta
                  title={<Space wrap><Text strong>{String(input.prompt || "未命名工作流")}</Text><Tag color={workflowRunStatusColor(item.status)}>{workflowRunStatusLabel(item.status)}</Tag></Space>}
                  description={
                    <Space direction="vertical" size={4}>
                      <Text type="secondary">{formatBeijingTime(item.created_at)}</Text>
                      {item.error_message ? <Text type="danger">{item.error_message}</Text> : <Text ellipsis>{String(output.output_title || "工作流输出已保存")}</Text>}
                    </Space>
                  }
                />
              </List.Item>
            );
          }}
        />
      </Card>
    </div>
  );
}
