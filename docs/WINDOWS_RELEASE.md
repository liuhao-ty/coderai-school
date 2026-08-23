# Windows 桌面签名发布

适用版本：`0.2.0-beta.7`

## 发布产物

- Windows x86_64 NSIS 安装包
- Tauri NSIS 更新安装包与 `.sig`
- `latest.json` 更新清单
- 发布说明和 SHA-256

生产客户端只连接正式 HTTPS API，不包含 FastAPI、Python、SQLite 或 AI Key。

## 前置条件

- Windows Server 2022/Windows 11 构建环境
- Node.js `24.15.0`
- Rust `1.88.0`
- WebView2 构建依赖
- 有效的 Windows Authenticode PFX 及时间戳服务
- Tauri updater 私钥和密码
- 固定公网 IPv4 的可信 HTTPS API、更新查询地址和更新文件地址
- 私有 Git 远端、受保护发布分支和已审核标签

当前仓库已包含 Tauri 更新公钥。加密私钥材料保存在仓库外：

```text
E:\CoderAI学堂-secrets
```

不得提交 PFX、Updater 私钥、密码或导出的明文凭据。

`tools/build-tauri.ps1` 保持纯 ASCII，以兼容 Windows PowerShell 5.1。默认密钥目录由项目目录追加 `-secrets` 得到，也可通过 `-SecretRoot` 或 `CODERAI_SECRET_ROOT` 覆盖。

## 本机未签名冒烟

先验证 Rust：

```powershell
$env:PATH = "E:\Developer\Rust\cargo\bin;$env:PATH"
cargo test --locked --manifest-path src-tauri\Cargo.toml
```

只构建可执行文件：

```powershell
$env:VITE_API_URL = "https://api.coderai.example.invalid"
$env:VITE_ORGANIZATION_CODE = "coderai-pilot"
$env:VITE_UPDATER_ENABLED = "false"
$env:TAURI_CONFIG = '{"bundle":{"createUpdaterArtifacts":false}}'
npm.cmd run tauri:build -- --no-bundle
```

构建未签名 NSIS 安装包：

```powershell
.\tools\build-tauri.ps1 `
  -ApiUrl "https://api.coderai.example.invalid" `
  -UpdaterEndpoint "https://updates.coderai.example.invalid/latest.json" `
  -OrganizationCode "coderai-pilot" `
  -UnsignedUpdater
```

`example.invalid` 只能用于编译冒烟，产物不得交付学生。

`-UnsignedUpdater` 会关闭前端更新检查和更新产物生成，但保留运行时所需的更新公钥。Tauri CLI 目前可能在成功生成 NSIS 后输出缺少更新私钥的非致命提示；只可在命令退出码为 `0`、安装包存在且安装/启动/卸载冒烟全部通过时接受该本地结果。

## GitHub 配置

Environment：`pilot-production`

Repository/Environment Variables：

- `CODERAI_PRODUCTION_API_URL`
- `CODERAI_ORGANIZATION_CODE`
- `CODERAI_UPDATER_ENDPOINT`
- `CODERAI_UPDATE_ASSET_BASE_URL`

Secrets：

- `WINDOWS_CERTIFICATE_PFX_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`
- `TAURI_UPDATER_PRIVATE_KEY`
- `TAURI_UPDATER_PRIVATE_KEY_PASSWORD`

要求：

- API、Updater 和 Asset Base URL 全部使用 HTTPS
- 无域名部署时分别使用 `https://<公网IP>`、`https://<公网IP>/desktop-updates/latest.json` 和 `https://<公网IP>/desktop-updates`
- PFX 只授权给受保护的 `pilot-production` 环境
- Environment 启用人工审批
- 发布日志不得输出证书、私钥和密码
- 更新私钥必须与 `src-tauri/tauri.conf.json` 中公钥匹配

## 发布流程

1. 全量门禁和云集成 CI 通过。
2. 更新版本号、发布说明和 `CODERAI_MINIMUM_CLIENT_VERSION` 策略。
3. 从审核后的发布提交创建标签：

```powershell
git tag -s v0.2.0-beta.7 -m "CoderAI 学堂 0.2.0-beta.7"
git push origin v0.2.0-beta.7
```

4. `release-windows.yml` 导入 PFX，构建签名 NSIS 和 Tauri 更新产物。
5. `tools/generate-update-manifest.ps1` 生成 `latest.json`。
6. 将安装包、更新压缩包、签名和清单上传到受控 HTTPS 存储。
7. 在隔离 Windows 10/11 机器验证签名、安装和更新，再发布给灰度用户。

## 签名检查

```powershell
Get-AuthenticodeSignature .\CoderAI*.exe | Format-List Status,StatusMessage,SignerCertificate,TimeStamperCertificate
```

要求：

- `Status` 为 `Valid`
- 发布者名称与产品资料一致
- 时间戳有效，证书到期后旧安装包仍可验证
- 安装包和卸载程序均有签名
- 上传后的 SHA-256 与构建机一致

可选使用 `signtool verify /pa /all /v <installer.exe>` 做第二次验证。

## Windows 验收矩阵

- Windows 10 22H2 x64
- Windows 11 当前受支持版本 x64
- 100%、125%、150%、200% DPI
- 普通用户安装，无管理员权限
- 首次安装、覆盖升级、自动更新、离线安装包
- 单实例、异常退出重启和日志目录
- 断网启动、网络恢复和 API 超时
- 最低客户端版本拦截
- 卸载后 Credential Manager 不残留三类 CoderAI 令牌
- SmartScreen 和常用杀毒软件检查

正式验收必须使用真实公网 IP 的受信任 HTTPS 和签名安装包，不能用浏览器或 `example.invalid` 产物代替。

## 灰度更新

建议使用独立更新渠道或服务器端设备白名单控制：

1. 内部管理员设备
2. 5 至 10 人灰度
3. 单班设备
4. 全机构内测

每阶段至少稳定 3 个教学日。更新服务必须保留上一版本安装包和签名产物，且能暂停继续下发。

## 客户端回滚

Tauri 更新不应通过降低版本号回滚。若 `0.2.0-beta.7` 客户端有严重问题：

1. 立即停止更新清单下发。
2. 从已知良好的回滚提交修复或还原代码。
3. 使用更高版本号（例如 `0.2.0-beta.8`）重新签名发布。
4. 必要时向灰度用户提供上一稳定安装包的人工卸载重装流程。
5. 数据库/接口不兼容时先执行服务端回滚评估，不能只替换客户端。

更新签名失败、安装后不能启动、凭据无法清理或最低版本误拦截均属于停止扩容条件。

## 当前阻塞

- 尚无真实 Authenticode PFX
- 已在真实公网 IP 上完成短期 IP 证书签发及真实续期验证
- `0.2.0-beta.6` Tauri 签名更新文件已上传到 `/desktop-updates/`
- GitHub 远端已建立，尚未配置受保护 Environment Secrets

因此当前已完成 Tauri updater 签名与公网分发，但仍不能宣称已完成 Authenticode、SmartScreen 或跨版本自动安装验收。
