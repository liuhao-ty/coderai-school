import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  List,
  Popconfirm,
  Row,
  Space,
  Switch,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  Upload
} from "antd";
import { PackageCheck, ShieldCheck, Trash2, UploadCloud } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";


const { Text } = Typography;

function pluginStatusLabel(status: string) {
  if (status === "ready") return "已就绪";
  if (status === "disabled") return "已停用";
  if (status === "invalid") return "校验失败";
  if (status === "legacy_manifest") return "协议过旧";
  return status;
}

type PluginTool = {
  id: string;
  name: string;
  description: string;
  action: string;
};

type PluginPublisher = {
  key_id: string;
  name: string;
  public_key: string;
  fingerprint: string;
  created_at: string;
};

type InstalledPlugin = {
  id: string;
  name: string;
  description: string;
  category: string;
  version: string;
  entry: string;
  capabilities: string[];
  permissions: string[];
  enabled: boolean;
  builtin: boolean;
  status: string;
  signed: boolean;
  runtime_protocol: string;
  publisher: PluginPublisher | null;
  tools: PluginTool[];
  can_uninstall: boolean;
  error_code?: string;
};

export function PluginManagementPanel({ licenseAllowsPlugins }: { licenseAllowsPlugins: boolean }) {
  const { message } = AntApp.useApp();
  const [publisherForm] = Form.useForm<{ key_id: string; name: string; public_key: string }>();
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([]);
  const [publishers, setPublishers] = useState<PluginPublisher[]>([]);
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [changingPluginId, setChangingPluginId] = useState<string | null>(null);

  const loadPlugins = async () => {
    setLoading(true);
    try {
      const [pluginRes, publisherRes] = await Promise.all([
        api.get("/api/plugins"),
        api.get("/api/plugins/publishers")
      ]);
      setPlugins(pluginRes.data.plugins || []);
      setPublishers(publisherRes.data.publishers || []);
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadPlugins();
  }, [licenseAllowsPlugins]);

  const installPackage = async () => {
    if (!packageFile) return;
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", packageFile);
      await api.post("/api/plugins/install", formData, { headers: { "Content-Type": "multipart/form-data" } });
      setPackageFile(null);
      await loadPlugins();
      message.success("签名插件已安装并完成完整性校验");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  const togglePlugin = async (plugin: InstalledPlugin, enabled: boolean) => {
    setChangingPluginId(plugin.id);
    try {
      await api.put(`/api/plugins/${plugin.id}/enabled`, { enabled });
      await loadPlugins();
      message.success(enabled ? "插件已启用" : "插件已停用");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setChangingPluginId(null);
    }
  };

  const uninstallPlugin = async (plugin: InstalledPlugin) => {
    setChangingPluginId(plugin.id);
    try {
      await api.delete(`/api/plugins/${plugin.id}`);
      await loadPlugins();
      message.success("插件已卸载");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setChangingPluginId(null);
    }
  };

  const trustPublisher = async (values: { key_id: string; name: string; public_key: string }) => {
    setLoading(true);
    try {
      await api.post("/api/plugins/publishers", values);
      publisherForm.resetFields();
      await loadPlugins();
      message.success("发布者公钥已加入信任列表");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  const removePublisher = async (publisher: PluginPublisher) => {
    setLoading(true);
    try {
      await api.delete(`/api/plugins/publishers/${publisher.key_id}`);
      await loadPlugins();
      message.success("已移除发布者信任；其插件将停止运行");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  const packageTab = (
    <Row gutter={[20, 20]}>
      <Col xs={24} xl={14}>
        <List
          loading={loading}
          dataSource={plugins}
          locale={{ emptyText: "暂无插件" }}
          renderItem={(plugin) => (
            <List.Item
              actions={[
                plugin.builtin ? (
                  <Tag key="builtin">内置</Tag>
                ) : (
                  <Tooltip key="toggle" title={plugin.enabled ? "停用插件" : "启用插件"}>
                    <Switch
                      aria-label={`${plugin.enabled ? "停用" : "启用"}${plugin.name}`}
                      checked={plugin.enabled}
                      loading={changingPluginId === plugin.id}
                      disabled={
                        (!licenseAllowsPlugins && !plugin.enabled)
                        || plugin.status === "invalid"
                        || plugin.status === "legacy_manifest"
                      }
                      onChange={(checked) => void togglePlugin(plugin, checked)}
                    />
                  </Tooltip>
                ),
                plugin.can_uninstall ? (
                  <Popconfirm
                    key="uninstall"
                    title={`卸载 ${plugin.name}？`}
                    description="插件文件和本机启停状态将被删除，已生成的学生作品会保留。"
                    okText="确认卸载"
                    cancelText="取消"
                    onConfirm={() => uninstallPlugin(plugin)}
                  >
                    <Tooltip title="卸载插件">
                      <Button
                        danger
                        type="text"
                        aria-label={`卸载${plugin.name}`}
                        icon={<Trash2 size={16} />}
                        loading={changingPluginId === plugin.id}
                      />
                    </Tooltip>
                  </Popconfirm>
                ) : null
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Text strong>{plugin.name}</Text>
                    <Tag color={plugin.status === "ready" ? "green" : plugin.status === "disabled" ? "default" : "red"}>
                      {pluginStatusLabel(plugin.status)}
                    </Tag>
                    {plugin.builtin
                      ? <Tag color="cyan">内置信任</Tag>
                      : plugin.signed && <Tag color="blue">签名已验证</Tag>}
                    <Tag>{plugin.runtime_protocol}</Tag>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={6} className="fullWidth">
                    <Text>{plugin.description}</Text>
                    <Space wrap>
                      <Text type="secondary">ID：{plugin.id}</Text>
                      <Text type="secondary">版本：{plugin.version || "未知"}</Text>
                      {plugin.publisher && <Text type="secondary">发布者：{plugin.publisher.name}</Text>}
                    </Space>
                    <Space wrap>
                      {plugin.permissions.map((permission) => <Tag key={permission} color="gold">{permission}</Tag>)}
                      {plugin.tools.map((tool) => <Tag key={tool.id}>{tool.name}</Tag>)}
                    </Space>
                    {plugin.error_code && <Text type="danger">{plugin.error_code}</Text>}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Col>
      <Col xs={24} xl={10}>
        <Space direction="vertical" size={12} className="fullWidth">
          <Alert
            type={licenseAllowsPlugins ? "info" : "warning"}
            showIcon
            message={licenseAllowsPlugins ? "安装签名插件包" : "当前授权未开放自定义插件"}
            description="只接受 Ed25519 签名的声明式插件包。插件不能执行脚本，也不能直接访问网络、文件系统或本机进程。"
          />
          <Upload.Dragger
            accept=".coderai-plugin,.zip"
            maxCount={1}
            disabled={!licenseAllowsPlugins}
            beforeUpload={(file) => {
              setPackageFile(file);
              return false;
            }}
            onRemove={() => {
              setPackageFile(null);
              return true;
            }}
            fileList={packageFile ? [packageFile as any] : []}
          >
            <Space direction="vertical" size={8}>
              <UploadCloud size={28} />
              <Text>选择 `.coderai-plugin` 安装包</Text>
              <Text type="secondary">最大 5 MB，安装前验证签名、兼容版本、权限和全部文件哈希</Text>
            </Space>
          </Upload.Dragger>
          <Space wrap>
            <Button type="primary" icon={<PackageCheck size={16} />} disabled={!licenseAllowsPlugins || !packageFile} loading={loading} onClick={installPackage}>
              验证并安装
            </Button>
            <Button onClick={loadPlugins} loading={loading}>刷新</Button>
          </Space>
        </Space>
      </Col>
    </Row>
  );

  const publisherTab = (
    <Row gutter={[20, 20]}>
      <Col xs={24} xl={13}>
        <List
          loading={loading}
          dataSource={publishers}
          locale={{ emptyText: "尚未信任任何第三方插件发布者" }}
          renderItem={(publisher) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="remove"
                  title={`移除 ${publisher.name} 的信任？`}
                  description="该发布者的所有已安装插件会立即停止验证和运行。"
                  okText="确认移除"
                  cancelText="取消"
                  onConfirm={() => removePublisher(publisher)}
                >
                  <Tooltip title="移除发布者信任">
                    <Button
                      danger
                      type="text"
                      aria-label={`移除${publisher.name}信任`}
                      icon={<Trash2 size={16} />}
                    />
                  </Tooltip>
                </Popconfirm>
              ]}
            >
              <List.Item.Meta
                title={<Space wrap><Text strong>{publisher.name}</Text><Tag>{publisher.key_id}</Tag></Space>}
                description={
                  <Space direction="vertical" size={4}>
                    <Text type="secondary" copyable={{ text: publisher.fingerprint }}>{publisher.fingerprint}</Text>
                    <Text type="secondary">加入时间：{publisher.created_at || "未知"}</Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Col>
      <Col xs={24} xl={11}>
        <Alert className="mb16" type="warning" showIcon message="只信任可核验身份的发布者" description="Key ID 相同但公钥不同会被拒绝。公钥指纹会写入教师审计日志。" />
        <Form form={publisherForm} layout="vertical" onFinish={trustPublisher} disabled={!licenseAllowsPlugins}>
          <Form.Item name="key_id" label="发布者 Key ID" rules={[{ required: true, message: "请输入发布者 Key ID" }]}>
            <Input placeholder="例如：example-school" />
          </Form.Item>
          <Form.Item name="name" label="发布者名称" rules={[{ required: true, message: "请输入发布者名称" }]}>
            <Input placeholder="例如：示例少儿编程机构" />
          </Form.Item>
          <Form.Item name="public_key" label="Ed25519 公钥" rules={[{ required: true, message: "请输入发布者公钥" }]}>
            <Input.TextArea rows={3} placeholder="Base64URL 编码的 32 字节公钥" spellCheck={false} />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<ShieldCheck size={16} />} loading={loading}>
            验证并信任
          </Button>
        </Form>
      </Col>
    </Row>
  );

  const specificationTab = (
    <Space direction="vertical" size={16} className="fullWidth">
      <Alert
        type="info"
        showIcon
        message="第三方安装包使用声明式插件协议 v1；本地模型和视频适配器属于受信任系统适配器，不通过普通插件包执行代码。"
      />
      <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="安装包">`.coderai-plugin` 或 ZIP</Descriptions.Item>
        <Descriptions.Item label="签名">受信任发布者 Ed25519</Descriptions.Item>
        <Descriptions.Item label="当前动作">`text.generate`</Descriptions.Item>
        <Descriptions.Item label="当前权限">`ai.text`、`projects.write`</Descriptions.Item>
        <Descriptions.Item label="大小限制">安装包 5 MB，解压后 20 MB</Descriptions.Item>
        <Descriptions.Item label="文件数量">最多 50 个声明文件</Descriptions.Item>
      </Descriptions>
      <Divider orientation="left">当前可以安装</Divider>
      <List
        size="small"
        dataSource={[
          "课堂文字工具：使用提示词模板调用系统已配置的文字模型。",
          "文字作品工具：将生成结果保存到学生作品库。",
          "签名升级包：版本号高于已安装版本时进行完整性校验后升级。",
        ]}
        renderItem={(item) => <List.Item>{item}</List.Item>}
      />
      <Divider orientation="left">普通插件不能执行</Divider>
      <Text type="secondary">
        Python、JavaScript、EXE、Shell、动态库、安装脚本、任意网络请求、本机进程、环境变量、数据库和任意文件访问均不开放。
      </Text>
      <Divider orientation="left">开发流程</Divider>
      <List
        size="small"
        dataSource={[
          "1. 编写只包含声明式工具定义的 plugin.json。",
          "2. 在项目目录外生成并保管 Ed25519 发布私钥。",
          "3. 使用 tools/plugin_packager.py 构建签名安装包。",
          "4. 管理员登记发布者公钥，再上传安装包完成签名、版本、权限和哈希校验。",
        ]}
        renderItem={(item) => <List.Item>{item}</List.Item>}
      />
      <Text copyable={{ text: "python tools/plugin_packager.py --help" }} code>
        python tools/plugin_packager.py --help
      </Text>
    </Space>
  );

  return (
    <Card title={<IconTitle icon={<PackageCheck size={18} />} text="签名插件" />}>
      <Tabs
        items={[
          { key: "packages", label: "插件安装包", children: packageTab },
          { key: "specification", label: "安装规范", children: specificationTab },
          { key: "publishers", label: "信任发布者", children: publisherTab }
        ]}
      />
    </Card>
  );
}
