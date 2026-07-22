# CoderAI 学堂

CoderAI 学堂是面向少儿 AI 课程学习的 Windows 桌面应用。当前发布目标为 `0.2.0-beta.1`：中国大陆单机构、免费封闭内测、最多 50 人同时在线、必须联网。

## 当前状态

- 前端：React 18、TypeScript、Vite、Ant Design、React Flow
- 桌面端：Tauri 2，Windows Credential Manager 保存生产令牌，支持单实例、日志、严格 CSP 和签名更新
- 云端 API：FastAPI、PostgreSQL、Alembic、Redis、Celery
- 文件：S3 兼容对象存储，客户端只通过鉴权接口访问
- 部署：Docker Compose、Caddy、Prometheus、Alertmanager、独立备份任务
- 版本：`0.2.0-beta.1`

云端生产客户端不会启动本地 FastAPI，也不会在学生电脑保存业务 SQLite。SQLite 和本地文件模式仅用于开发、自动化测试及一次性历史迁移。

当前代码已具备云端内测所需的主体能力，但尚未部署到正式云主机，也没有正式域名、代码签名证书、私有 Git 远端或机构真实 AI 密钥。详见 [项目状态](docs/PROJECT_STATUS.md) 与 [云端部署手册](docs/CLOUD_DEPLOYMENT.md)。

## 产品能力

### 学生端

- 管理员分配的学生账号登录，不开放自行注册
- 小学低龄、小学高龄、初中高中三类学龄策略
- 文字、图片、视频、编程助手和工作流
- 按个人或班级排课学习，查看课程资料并提交作品
- Markdown 作品预览与编辑、图片预览、作品归档和提交历史
- 查看教师评分、反馈、隐私政策和个人数据导出

### 教师端

- 获授权班级、学员、课程包、排课、提交和安全记录管理
- 预览管理员发布的课程；PPT 仅在线预览，Markdown 资料可查看和下载
- 按学员或班级排课、批改作业、重置学生密码
- 归档学生不出现在教师维护和排课列表，历史教学记录只读
- 登录设备、操作审计和教师密码维护

### 管理员端

- 职员账号与学生开户、教学管理和全机构数据查看
- 课程包、课程、PPT、工程包和成果包维护
- 课程包教师白名单、课程作者、排课和批改管理
- 多 AI 服务商、模型路由、机构级密钥和用量管理
- 隐私政策、监护人授权、数据保留预览/例外/审批/执行
- 机构数据导出；云端整库恢复仅允许平台运维执行

## 云端结构

```text
Windows Tauri client
  -> HTTPS / Caddy
     -> FastAPI (2 workers)
        -> PostgreSQL
        -> Redis / Celery
        -> S3-compatible object storage
        -> institution-configured AI providers

Operations
  -> Prometheus / Alertmanager
  -> independent PostgreSQL + object backups
```

所有业务记录包含 `organization_id`。机构从请求头和会话推导，客户端不能指定数据归属；用户名唯一约束为“机构 + 用户名”。生产 AI 密钥使用服务器 AES-GCM 主密钥加密，旧 Windows DPAPI 密文不会迁移到 Linux。

## 开发环境

已固定的工具版本：

- Node.js `24.15.0`
- Python `3.12.10`
- Rust `1.88.0`
- LibreOffice（PPTX 转 PDF 测试需要）

安装依赖：

```powershell
python -m pip install --requirement requirements-dev.lock
npm.cmd ci
```

启动本地开发环境：

```powershell
npm.cmd run dev
```

- 前端：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8000`
- API 文档：`http://127.0.0.1:8000/docs`

也可以运行 [启动脚本](start-coderai.ps1)：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-coderai.ps1
```

本地全新开发数据库会创建仅用于开发的 `admin / 123456` 并要求首次改密。云端模式不会创建默认管理员，必须使用运维命令生成强密码账号：

```powershell
python -m backend.app.cli bootstrap-admin --help
```

学生新建或重置后的固定密码仍为 `bcm123456`，这是当前内测已接受风险，不代表商业正式版推荐方案。

## 测试

```powershell
$env:PYTHONUTF8 = "1"
npm.cmd run lint:backend
npm.cmd test
npm.cmd run build
npm.cmd run test:tauri
npm.cmd run test:e2e
npm.cmd run test:ppt-conversion
npm.cmd run check:full
python -m pip_audit --requirement requirements.lock
npm.cmd audit --audit-level=high
```

仓库路径包含中文时，Windows 上运行 `pip-audit` 应保留 `PYTHONUTF8=1`，避免其依赖库误解码 `pip` 子进程输出。

云服务集成测试在 GitHub Actions 中使用真实 PostgreSQL、Redis 和 MinIO 容器运行。由于当前 Windows 机器没有 Docker/WSL，本机不能复现该容器测试。

50 账号非 AI 接口压测工具：

```powershell
npm.cmd run test:load -- --base-url https://api.example.cn --accounts tools/pilot-load-accounts.json --report test-results/pilot-load.json
```

账号文件不得提交到 Git。示例见 `tools/pilot-load-accounts.example.json`，详细流程见 [云端部署手册](docs/CLOUD_DEPLOYMENT.md)。

## 桌面构建

本机未签名冒烟构建：

```powershell
$env:PATH = "E:\Developer\Rust\cargo\bin;$env:PATH"
$env:VITE_API_URL = "https://api.coderai.example.invalid"
$env:VITE_ORGANIZATION_CODE = "coderai-pilot"
$env:VITE_UPDATER_ENABLED = "false"
$env:TAURI_CONFIG = '{"bundle":{"createUpdaterArtifacts":false}}'
npm.cmd run tauri:build -- --no-bundle
```

正式 NSIS 构建必须注入 HTTPS API、更新地址、Windows Authenticode 证书和 Tauri 更新签名私钥。完整步骤见 [Windows 发布手册](docs/WINDOWS_RELEASE.md)。

## 部署与迁移

- [云端部署与运行](docs/CLOUD_DEPLOYMENT.md)
- [SQLite 到云端迁移及回滚](docs/CLOUD_MIGRATION.md)
- [备份与恢复演练](docs/BACKUP_RECOVERY.md)
- [Windows 签名发布与灰度](docs/WINDOWS_RELEASE.md)
- [内测合规清单](docs/PILOT_COMPLIANCE.md)
- [系统架构](docs/ARCHITECTURE.md)
- [项目状态](docs/PROJECT_STATUS.md)

正式迁移前必须冻结本地写入并使用迁移副本。当前 `workspace_data` 保持原样；没有真实 PostgreSQL、对象存储和独立备份位置时，不得执行正式迁移。

## 安全边界

- 生产令牌保存在 Windows Credential Manager，不保存在桌面 WebView 的 `localStorage`
- 生产 CSP 仅允许正式 API、更新服务和受控媒体源
- 云端密钥使用 AES-GCM；主密钥只通过服务器环境或密钥管理服务注入
- 机构管理员只能导出本机构脱敏数据，不能恢复或覆盖平台数据库
- 备份目标必须与生产对象存储独立，并执行 SHA-256 校验和恢复演练
- 连续登录失败 5 次锁定 15 分钟；会话可撤销且高风险操作写入审计
- 数据到期删除必须经过预览、例外检查、管理员审批和精确确认，不自动静默删除
