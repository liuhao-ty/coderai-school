# CoderAI 学堂离线许可证签发

## 协议

- 许可证格式为 `CODERAI-LIC1.<规范 JSON 载荷>.<Ed25519 签名>`，载荷和签名使用 Base64URL 编码。
- 应用只包含公钥；签发私钥必须保存在项目目录、安装目录和备份目录之外。
- 签名保护机构、版本、席位、功能、生效日期、到期日期和设备安装码，教师端不能修改这些字段。
- 当前设备安装码可在“教师端 → 扩展与授权”中复制。安装码是机器标识的不可逆 SHA-256 摘要。
- 续期使用相同许可证编号和更晚到期日重新签发；解绑时重新签发并从 `--device` 参数中移除旧安装码。

## 首次建立签发密钥

在安全的机构授权电脑上设置私钥密码，私钥路径不要放在本项目中：

```powershell
$env:CODERAI_LICENSE_KEY_PASSWORD = "使用密码管理器生成的强密码"
python tools\license_issuer.py keygen `
  --private-key D:\CoderAI-License-Secrets\issuer-private.pem `
  --public-key D:\CoderAI-License-Secrets\issuer-public.txt
```

发布前必须将 `issuer-public.txt` 中的 Base64URL 值写入 `backend/app/licensing.py` 的发布公钥常量并重新构建应用。应用不接受环境变量覆盖信任公钥或设备安装码，防止本机用户替换信任根。不要复制、提交或备份私钥到 `workspace_data/`。

## 签发设备绑定许可证

```powershell
$env:CODERAI_LICENSE_KEY_PASSWORD = "使用密码管理器生成的强密码"
python tools\license_issuer.py issue `
  --private-key D:\CoderAI-License-Secrets\issuer-private.pem `
  --organization "示例少儿编程中心" `
  --license-id "LIC-SCHOOL-001" `
  --seats 100 `
  --expires-at 2027-07-31 `
  --device DEV-0123456789ABCDEF0123 `
  --output D:\CoderAI-License-Secrets\LIC-SCHOOL-001.license
```

多个教学设备可以重复传入 `--device`。只有明确允许任意设备时才能使用 `--unbound`。

## 续期与解绑

续期时沿用 `--license-id`，更新 `--expires-at` 后重新签发。设备解绑时删除旧设备的 `--device` 参数，只保留仍授权的安装码。新许可证导入成功后会替换当前设备上的旧许可证。

当前是离线许可证协议，不具备在线吊销能力。重新签发并不会远程删除旧设备已经持有的历史许可证；完成解绑时还必须在旧设备清除许可证，或在后续版本接入在线授权与吊销列表。

## 社区模式

教师清除许可证后会回到社区授权。社区授权最多允许 30 个在读学生账号；已归档和停用账号不占用席位。超过席位时不会删除数据或中断现有学生登录，但会阻止创建、恢复或重新启用更多学生账号。
