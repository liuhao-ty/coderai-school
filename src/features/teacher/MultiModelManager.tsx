import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import {
  ArrowDown,
  ArrowUp,
  CirclePlus,
  KeyRound,
  Pencil,
  Play,
  Route,
  Save,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import type {
  ProviderCapability,
  ProviderFormValues,
  ProviderPreset,
  ProviderRouteMap,
  ProviderState,
} from "../../domain-types";
import { api } from "../../lib/api";
import { capabilityLabel } from "../../lib/domain";
import { explainError } from "../../lib/errors";
import { formatBeijingTime } from "../../lib/format";


const { Text } = Typography;
const capabilities: ProviderCapability[] = ["text", "image", "video"];
const emptyRoutes: ProviderRouteMap = { text: [], image: [], video: [] };

const capabilityColors: Record<ProviderCapability, string> = {
  text: "blue",
  image: "green",
  video: "gold",
};

function modelFor(provider: ProviderState, capability: ProviderCapability) {
  return provider[`${capability}_model` as "text_model" | "image_model" | "video_model"] || "";
}

function healthTag(provider: ProviderState) {
  if (!provider.configured) return <Tag color="red">密钥未配置</Tag>;
  if (provider.last_test_status === "success") return <Tag color="green">测试通过</Tag>;
  if (provider.last_test_status === "failed") return <Tag color="red">测试失败</Tag>;
  return <Tag>待测试</Tag>;
}

export function MultiModelManager({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const { message } = AntApp.useApp();
  const [form] = Form.useForm<ProviderFormValues>();
  const [providers, setProviders] = useState<ProviderState[]>([]);
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [routes, setRoutes] = useState<ProviderRouteMap>(emptyRoutes);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingRoutes, setSavingRoutes] = useState(false);
  const [testingKey, setTestingKey] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<ProviderState | null>(null);
  const [loadError, setLoadError] = useState("");
  const selectedProviderType = Form.useWatch("provider_type", form);
  const selectedTextModel = Form.useWatch("text_model", form);
  const selectedImageModel = Form.useWatch("image_model", form);
  const selectedVideoModel = Form.useWatch("video_model", form);
  const selectedPreset = presets.find((preset) => preset.provider_type === selectedProviderType);

  const modelOptions = (capability: ProviderCapability) => {
    const current = { text: selectedTextModel, image: selectedImageModel, video: selectedVideoModel }[capability] || "";
    const catalog = selectedPreset?.models?.[capability] || [];
    const options = catalog.map((model) => ({
      value: model.id,
      label: model.name === model.id ? model.id : `${model.name} · ${model.id}`,
    }));
    if (current && !catalog.some((model) => model.id === current)) {
      options.unshift({ value: current, label: `${current} · 现有兼容配置` });
    }
    return options;
  };

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [providerRes, presetRes] = await Promise.all([
        api.get("/api/settings/providers"),
        api.get("/api/settings/provider-presets"),
      ]);
      setProviders(providerRes.data.providers || []);
      setRoutes({ ...emptyRoutes, ...(providerRes.data.routes || {}) });
      setPresets(presetRes.data.presets || []);
    } catch (error) {
      setLoadError(explainError(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const providerById = useMemo(
    () => new Map(providers.filter((provider) => provider.id).map((provider) => [provider.id as number, provider])),
    [providers],
  );

  const applyPreset = (providerType: string) => {
    const preset = presets.find((item) => item.provider_type === providerType);
    if (!preset) return;
    form.setFieldsValue({
      name: preset.name,
      provider_type: preset.provider_type,
      base_url: preset.base_url,
      text_model: preset.text_model,
      image_model: preset.image_model,
      video_model: preset.video_model,
    });
  };

  const openCreate = () => {
    const preset = presets[0];
    setEditingProvider(null);
    form.resetFields();
    form.setFieldsValue({
      name: preset?.name || "OpenAI Compatible",
      provider_type: preset?.provider_type || "openai_compatible",
      base_url: preset?.base_url || "https://api.openai.com/v1",
      api_key: "",
      text_model: preset?.text_model || "gpt-4o-mini",
      image_model: preset?.image_model || "gpt-image-1",
      video_model: preset?.video_model || "",
      enabled: true,
    });
    setModalOpen(true);
  };

  const openEdit = (provider: ProviderState) => {
    setEditingProvider(provider);
    form.setFieldsValue({
      name: provider.name || "",
      provider_type: provider.provider_type || "openai_compatible",
      base_url: provider.base_url || "",
      api_key: "",
      text_model: provider.text_model || "",
      image_model: provider.image_model || "",
      video_model: provider.video_model || "",
      enabled: provider.enabled !== false,
    });
    setModalOpen(true);
  };

  const saveProvider = async (values: ProviderFormValues) => {
    setSaving(true);
    try {
      if (editingProvider?.id) {
        await api.put(`/api/settings/providers/${editingProvider.id}`, values);
      } else {
        await api.post("/api/settings/providers", values);
      }
      setModalOpen(false);
      await Promise.all([load(), onRefresh()]);
      message.success(editingProvider ? "模型服务已更新" : "模型服务已添加");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSaving(false);
    }
  };

  const setEnabled = async (provider: ProviderState, enabled: boolean) => {
    if (!provider.id) return;
    try {
      await api.patch(`/api/settings/providers/${provider.id}/enabled`, { enabled });
      await Promise.all([load(), onRefresh()]);
      message.success(enabled ? "模型服务已启用" : "模型服务已停用");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const deleteProvider = async (provider: ProviderState) => {
    if (!provider.id) return;
    try {
      await api.delete(`/api/settings/providers/${provider.id}`);
      await Promise.all([load(), onRefresh()]);
      message.success("模型服务已删除");
    } catch (error) {
      message.error(explainError(error));
    }
  };

  const testProvider = async (provider: ProviderState, capability: ProviderCapability) => {
    if (!provider.id) return;
    const key = `${provider.id}:${capability}`;
    setTestingKey(key);
    try {
      const response = await api.post(`/api/settings/providers/${provider.id}/test`, { capability });
      await load();
      const latency = response.data.latency_ms ? `，${response.data.latency_ms} ms` : "";
      message.success(`${capabilityLabel(capability)}测试通过${latency}`);
    } catch (error) {
      await load();
      message.error(explainError(error));
    } finally {
      setTestingKey("");
    }
  };

  const routeOptions = (capability: ProviderCapability) => providers.filter((provider) => (
    provider.id
    && provider.enabled
    && provider.configured
    && provider.capabilities?.includes(capability)
    && modelFor(provider, capability)
    && !routes[capability].includes(provider.id)
  ));

  const addRouteProvider = (capability: ProviderCapability, providerId: number) => {
    setRoutes((current) => ({ ...current, [capability]: [...current[capability], providerId] }));
  };

  const removeRouteProvider = (capability: ProviderCapability, providerId: number) => {
    setRoutes((current) => ({ ...current, [capability]: current[capability].filter((id) => id !== providerId) }));
  };

  const moveRouteProvider = (capability: ProviderCapability, index: number, offset: number) => {
    setRoutes((current) => {
      const next = [...current[capability]];
      const target = index + offset;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...current, [capability]: next };
    });
  };

  const saveRoutes = async () => {
    setSavingRoutes(true);
    try {
      await api.put("/api/settings/provider-routes", { routes });
      await Promise.all([load(), onRefresh()]);
      message.success("模型路由已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setSavingRoutes(false);
    }
  };

  const testMenu = (provider: ProviderState): MenuProps => ({
    items: capabilities
      .filter((capability) => provider.capabilities?.includes(capability) && modelFor(provider, capability))
      .map((capability) => ({
        key: capability,
        label: `测试${capabilityLabel(capability)}`,
        onClick: () => void testProvider(provider, capability),
      })),
  });

  const columns: TableColumnsType<ProviderState> = [
    {
      title: "服务配置",
      key: "provider",
      width: 230,
      render: (_, provider) => (
        <Space direction="vertical" size={4} className="providerIdentity">
          <Space wrap size={6}>
            <Text strong>{provider.name}</Text>
            <Tag>{presets.find((preset) => preset.provider_type === provider.provider_type)?.name || provider.provider_type}</Tag>
          </Space>
          <Text type="secondary" ellipsis={{ tooltip: provider.base_url }}>{provider.base_url}</Text>
          <Space wrap size={4}>
            {healthTag(provider)}
            {!provider.enabled && <Tag>已停用</Tag>}
          </Space>
        </Space>
      ),
    },
    {
      title: "模型",
      key: "models",
      width: 220,
      render: (_, provider) => (
        <div className="providerModelList">
          {capabilities.map((capability) => provider.capabilities?.includes(capability) && modelFor(provider, capability) && (
            <div className="modelLine" key={capability}>
              <Tag color={capabilityColors[capability]}>{capabilityLabel(capability)}</Tag>
              <Text ellipsis={{ tooltip: modelFor(provider, capability) }}>{modelFor(provider, capability)}</Text>
            </div>
          ))}
        </div>
      ),
    },
    {
      title: "运行状态",
      key: "routing",
      width: 180,
      render: (_, provider) => (
        <Space direction="vertical" size={5}>
          <Space wrap size={4}>
            {(provider.routed_capabilities || []).map((capability) => (
              <Tag color={capabilityColors[capability]} key={capability}>{capabilityLabel(capability)}路由</Tag>
            ))}
            {!provider.routed_capabilities?.length && <Text type="secondary">未加入路由</Text>}
          </Space>
          <Text type="secondary">调用记录 {provider.usage_count || 0}</Text>
          {provider.last_tested_at && <Text type="secondary">测试于 {formatBeijingTime(provider.last_tested_at)}</Text>}
        </Space>
      ),
    },
    {
      title: "启用",
      key: "enabled",
      width: 68,
      render: (_, provider) => (
        <Switch
          size="small"
          checked={provider.enabled !== false}
          aria-label={`${provider.name || "模型服务"}启用状态`}
          onChange={(checked) => void setEnabled(provider, checked)}
        />
      ),
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 134,
      render: (_, provider) => (
        <Space size={4}>
          <Dropdown menu={testMenu(provider)} trigger={["click"]} disabled={!provider.configured}>
            <Tooltip title="测试模型">
              <Button
                icon={<Play size={15} />}
                loading={testingKey.startsWith(`${provider.id}:`)}
                aria-label={`测试${provider.name || "模型服务"}`}
              />
            </Tooltip>
          </Dropdown>
          <Tooltip title="编辑配置">
            <Button icon={<Pencil size={15} />} aria-label={`编辑${provider.name || "模型服务"}`} onClick={() => openEdit(provider)} />
          </Tooltip>
          <Popconfirm
            title="删除模型服务？"
            description="已绑定视频任务的配置不能删除，可改为停用。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void deleteProvider(provider)}
          >
            <Tooltip title="删除配置">
              <Button danger icon={<Trash2 size={15} />} aria-label={`删除${provider.name || "模型服务"}`} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (loading && !providers.length) {
    return <div className="panelLoading"><Spin /></div>;
  }

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      {loadError && <Alert type="error" showIcon message="模型配置加载失败" description={loadError} />}
      <Card
        title={<IconTitle icon={<Route size={18} />} text="能力路由" />}
        extra={<Button type="primary" icon={<Save size={16} />} loading={savingRoutes} onClick={() => void saveRoutes()}>保存路由</Button>}
      >
        <div className="routeGrid">
          {capabilities.map((capability) => (
            <section className="routeLane" key={capability} aria-label={`${capabilityLabel(capability)}路由`}>
              <div className="routeLaneHeader">
                <Tag color={capabilityColors[capability]}>{capabilityLabel(capability)}</Tag>
                <Text type="secondary">{routes[capability].length} 个模型</Text>
              </div>
              <div className="routeItems">
                {routes[capability].map((providerId, index) => {
                  const provider = providerById.get(providerId);
                  if (!provider) return null;
                  return (
                    <div className="routeItem" key={providerId}>
                      <div className="routeRank">{index === 0 ? "主" : index}</div>
                      <div className="routeItemText">
                        <Text strong ellipsis={{ tooltip: provider.name }}>{provider.name}</Text>
                        <Text type="secondary" ellipsis={{ tooltip: modelFor(provider, capability) }}>{modelFor(provider, capability) || "模型未配置"}</Text>
                      </div>
                      {!provider.enabled && <Tag>停用</Tag>}
                      <Space size={0}>
                        <Tooltip title="上移">
                          <Button type="text" icon={<ArrowUp size={14} />} disabled={index === 0} aria-label="上移" onClick={() => moveRouteProvider(capability, index, -1)} />
                        </Tooltip>
                        <Tooltip title="下移">
                          <Button type="text" icon={<ArrowDown size={14} />} disabled={index === routes[capability].length - 1} aria-label="下移" onClick={() => moveRouteProvider(capability, index, 1)} />
                        </Tooltip>
                        <Tooltip title="移出路由">
                          <Button type="text" danger icon={<Trash2 size={14} />} aria-label="移出路由" onClick={() => removeRouteProvider(capability, providerId)} />
                        </Tooltip>
                      </Space>
                    </div>
                  );
                })}
                {!routes[capability].length && <div className="routeEmpty">未配置路由</div>}
              </div>
              <Select
                className="fullWidth"
                aria-label={`为${capabilityLabel(capability)}路由添加模型`}
                value={undefined}
                placeholder="添加模型"
                suffixIcon={<CirclePlus size={15} />}
                options={routeOptions(capability).map((provider) => ({
                  value: provider.id as number,
                  label: `${provider.name} · ${modelFor(provider, capability)}`,
                }))}
                onChange={(providerId: number) => addRouteProvider(capability, providerId)}
                notFoundContent="没有可用模型"
              />
            </section>
          ))}
        </div>
      </Card>

      <Card
        title={<IconTitle icon={<KeyRound size={18} />} text="服务商与模型配置" />}
        extra={<Button type="primary" icon={<CirclePlus size={16} />} onClick={openCreate}>添加服务</Button>}
      >
        <Table
          rowKey={(provider) => provider.id || provider.name || "provider"}
          columns={columns}
          dataSource={providers}
          pagination={false}
          scroll={{ x: 850 }}
          locale={{ emptyText: "尚未添加模型服务" }}
        />
      </Card>

      <Modal
        title={editingProvider ? "编辑模型服务" : "添加模型服务"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnHidden
        width={760}
      >
        <Form form={form} layout="vertical" onFinish={saveProvider} requiredMark={false}>
          <Form.Item name="provider_type" label="服务商" rules={[{ required: true }]}>
            <Select
              options={presets.map((preset) => ({ value: preset.provider_type, label: preset.name }))}
              onChange={applyPreset}
            />
          </Form.Item>
          {selectedPreset && (
            <div className="providerPresetSummary">
              <Text type="secondary">{selectedPreset.description}</Text>
              <Space wrap size={4}>
                {selectedPreset.capabilities.map((capability) => (
                  <Tag color={capabilityColors[capability as ProviderCapability]} key={capability}>{capabilityLabel(capability)}</Tag>
                ))}
              </Space>
            </div>
          )}
          <Row gutter={12}>
            <Col xs={24} md={18}>
              <Form.Item name="name" label="配置名称" rules={[{ required: true, message: "请输入配置名称" }]}>
                <Input placeholder="例如：DeepSeek 课堂主模型" maxLength={80} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true, message: "请输入 Base URL" }]}>
            <Input placeholder="https://api.example.com/v1" />
          </Form.Item>
          <Form.Item name="api_key" label={`API Key${editingProvider ? `（${editingProvider.api_key_masked || "未配置"}）` : ""}`}>
            <Input.Password placeholder={editingProvider ? "留空保留原密钥" : "输入 API Key"} autoComplete="new-password" />
          </Form.Item>
          <Row gutter={12}>
            <Col xs={24} md={8}>
              <Form.Item name="text_model" label="文字模型">
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择文字模型"
                  disabled={!selectedPreset?.capabilities.includes("text")}
                  options={modelOptions("text")}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="image_model" label="图片模型">
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择图片模型"
                  disabled={!selectedPreset?.capabilities.includes("image")}
                  options={modelOptions("image")}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="video_model" label="视频模型">
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择视频模型"
                  disabled={!selectedPreset?.capabilities.includes("video")}
                  options={modelOptions("video")}
                />
              </Form.Item>
            </Col>
          </Row>
          <div className="modalActions">
            <Button onClick={() => setModalOpen(false)}>取消</Button>
            <Button type="primary" htmlType="submit" icon={<Save size={16} />} loading={saving}>保存</Button>
          </div>
        </Form>
      </Modal>
    </Space>
  );
}
