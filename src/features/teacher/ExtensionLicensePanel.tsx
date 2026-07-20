import { Alert, App as AntApp, Button, Card, Col, Descriptions, Form, Input, Popconfirm, Row, Space, Tag, Typography } from "antd";
import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";

import { IconTitle } from "../../components/IconTitle";
import { api } from "../../lib/api";
import { explainError } from "../../lib/errors";
import { PluginManagementPanel } from "./PluginManagementPanel";


const { Text } = Typography;

const licensePlanLabels: Record<string, string> = {
  community: "社区版",
  school: "学校版",
  enterprise: "机构版",
  commercial: "商业版"
};

const licenseStatusLabels: Record<string, string> = {
  community: "社区授权",
  active: "授权有效",
  expired: "已到期",
  not_yet_valid: "尚未生效",
  device_mismatch: "设备不匹配",
  seat_limit_exceeded: "席位超限",
  invalid: "授权无效"
};

type LicenseStatus = {
  license_key: string;
  organization: string;
  plan: string;
  valid: boolean;
  usable: boolean;
  signature_verified: boolean;
  status: string;
  message: string;
  license_id: string;
  issued_at: string;
  not_before: string;
  expires_at: string;
  seats: number;
  seats_used: number;
  seats_remaining: number;
  features: string[];
  device_id: string;
  device_bound: boolean;
  authorized_devices: string[];
  issuer: string;
};

export function ExtensionLicensePanel() {
  const { message } = AntApp.useApp();
  const [licenseForm] = Form.useForm<{ license_key: string }>();
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const loadExtensions = async () => {
    setLoading(true);
    try {
      const licenseRes = await api.get("/api/system/license");
      const nextLicense = licenseRes.data.license as LicenseStatus;
      setLicense(nextLicense);
      licenseForm.setFieldsValue({
        license_key: nextLicense.license_key || ""
      });
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadExtensions();
  }, []);

  const saveLicense = async (values: { license_key: string }) => {
    setLoading(true);
    try {
      const res = await api.post("/api/system/license", values);
      setLicense(res.data.license);
      licenseForm.setFieldValue("license_key", res.data.license.license_key || "");
      message.success("授权信息已保存");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  const useCommunityLicense = async () => {
    setLoading(true);
    try {
      const res = await api.post("/api/system/license", { license_key: "" });
      setLicense(res.data.license);
      licenseForm.setFieldValue("license_key", "");
      message.success("已切换为社区授权");
    } catch (error) {
      message.error(explainError(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space direction="vertical" size={16} className="fullWidth">
      <Card title={<IconTitle icon={<KeyRound size={18} />} text="许可证校验" />}>
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={13}>
            <Space direction="vertical" size={12} className="fullWidth">
              <Alert
                type={license?.usable ? (license.status === "community" ? "info" : "success") : license?.valid ? "warning" : "error"}
                showIcon
                message={license ? license.message : "正在读取授权状态"}
                description={
                  license && (
                    <Space direction="vertical" size={8}>
                      <Space wrap>
                        <Tag color={license.signature_verified ? "green" : "default"}>
                          {license.signature_verified ? "Ed25519 签名已验证" : "内置社区模式"}
                        </Tag>
                        <Tag color={license.usable ? "blue" : "red"}>
                          {licenseStatusLabels[license.status] || license.status}
                        </Tag>
                        {license.device_bound && <Tag color="gold">设备绑定</Tag>}
                      </Space>
                      <Descriptions size="small" column={1} className="licenseSummary">
                        <Descriptions.Item label="机构">{license.organization}</Descriptions.Item>
                        <Descriptions.Item label="版本">{licensePlanLabels[license.plan] || license.plan}</Descriptions.Item>
                        <Descriptions.Item label="许可证编号">{license.license_id}</Descriptions.Item>
                        <Descriptions.Item label="签发方">{license.issuer}</Descriptions.Item>
                        <Descriptions.Item label="席位">
                          {license.seats_used} / {license.seats}
                        </Descriptions.Item>
                        <Descriptions.Item label="剩余席位">{license.seats_remaining}</Descriptions.Item>
                        {license.issued_at && <Descriptions.Item label="签发日期">{license.issued_at}</Descriptions.Item>}
                        {license.expires_at && <Descriptions.Item label="到期日期">{license.expires_at}</Descriptions.Item>}
                      </Descriptions>
                      <div className="licenseDeviceCode">
                        <Text strong>当前设备安装码：</Text>
                        <Text code copyable={{ text: license.device_id }}>{license.device_id}</Text>
                      </div>
                      <Space wrap>
                        {license.features.map((feature) => (
                          <Tag key={feature} color="blue">
                            {feature}
                          </Tag>
                        ))}
                      </Space>
                    </Space>
                  )
                }
              />
              <Text type="secondary">
                机构签发许可证时需要当前设备安装码。续期或解绑设备时重新签发并导入新许可证，私钥只保留在机构授权环境中。
              </Text>
            </Space>
          </Col>
          <Col xs={24} xl={11}>
            <Form
              form={licenseForm}
              layout="vertical"
              onFinish={saveLicense}
              initialValues={{ license_key: "" }}
            >
              <Form.Item
                name="license_key"
                label="签名许可证"
                extra={<span className="licenseFormHelp">许可证以 CODERAI-LIC1 开头，机构、席位、设备和有效期均由数字签名保护。</span>}
              >
                <Input.TextArea rows={7} placeholder="CODERAI-LIC1.载荷.签名" spellCheck={false} />
              </Form.Item>
              <Space wrap>
                <Button type="primary" htmlType="submit" loading={loading}>
                  导入并验证
                </Button>
                {license?.plan !== "community" && (
                  <Popconfirm
                    title="切换为社区授权？"
                    description="已保存的商业许可证会从本机移除，社区版最多允许 30 个在读学生账号。"
                    okText="确认切换"
                    cancelText="取消"
                    onConfirm={useCommunityLicense}
                  >
                    <Button danger loading={loading}>清除许可证</Button>
                  </Popconfirm>
                )}
              </Space>
            </Form>
          </Col>
        </Row>
      </Card>

      <PluginManagementPanel licenseAllowsPlugins={Boolean(license?.valid && license?.usable && license.features.includes("plugins"))} />
    </Space>
  );
}
