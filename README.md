# CoderAI 学堂

CoderAI 学堂是面向少儿 AI 课程学习的 Windows 桌面应用。当前开发版本为 Windows 客户端 `0.2.0-beta.7`、云端 API `0.2.0-beta.5`：中国大陆单机构、免费封闭内测、最多 50 人同时在线、必须联网。

## 当前状态

- 前端：React 18、TypeScript、Vite、Ant Design、React Flow
- 桌面端：Tauri 2，Windows Credential Manager 保存生产令牌，支持单实例、日志、严格 CSP 和签名更新
- 云端 API：FastAPI、PostgreSQL、Alembic、Redis、Celery
- 文件：S3 兼容对象存储，客户端只通过鉴权接口访问
- 部署：Docker Compose、Caddy、Prometheus、Alertmanager、独立备份任务
- Windows 客户端开发版：`0.2.0-beta.7`
- 云端 API 开发版：`0.2.0-beta.5`

云端生产客户端不会启动本地 FastAPI，也不会在学生电脑保存业务 SQLite。SQLite 和本地文件模式仅用于开发、自动化测试及一次性历史迁移。

当前阿里云单机构技术内测环境已通过固定公网 IPv4 + Let's Encrypt 短期 IP 证书运行，不强制购买域名；`0.2.0-beta.4` API 与 `0.2.0-beta.6` Windows 客户端已部署，增加服务端只读请求并发保护、客户端版本追踪和网关敏感请求头脱敏。发布分支已同步至 GitHub。该环境仍未完成服务器扩容、Windows Authenticode、独立备份、机构真实 AI 密钥和 50 人压测，不得作为生产安全环境或直接按 50 人规模开放。详见 [项目状态](docs/PROJECT_STATUS.md) 与 [云端部署手册](docs/CLOUD_DEPLOYMENT.md)。

当前开发工作区已将教师 PPT 全屏播放、学生本机附件提交、课程级文件类型/大小限制、异步 AI 任务、学习 Agent、即梦 Seedance 视频、本地或私有 OpenAI 兼容模型连接和插件安装规范归入客户端 `0.2.0-beta.7`、API `0.2.0-beta.5`；这些版本尚未提交、打包或部署到上述云端环境。

## 产品能力

### 学生端

- 管理员分配的学生账号登录，不开放自行注册
- 小学低龄、小学高龄、初中高中三类学龄策略
- 文字、图片、视频、工作流和支持单会话记忆的 AI 助手；图片、视频和工作流工具必须由学生确认后调用
- 文字和图片使用可恢复的异步任务，离开或刷新页面不会中断；生成失败支持重试和取消
- 按个人或班级排课学习，在线查看、实时预览、编辑和保存个人工程包；可从作品库选择作品，或直接提交符合课程规则的本机附件
- 学生端不显示 PPT 和成果包；本机图片先进入教师审核，其他通过文件安全校验的类型可直接提交
- 本机附件只属于提交版本，不进入作品库；提交历史可折叠并通过独立深链接查看不可变快照
- Markdown 作品双栏实时预览与编辑、图片预览、作品归档和提交历史
- 查看教师评分、反馈、隐私政策和个人数据导出

### 教师端

- 获授权班级、学员、课程包、排课、提交和安全记录管理
- 预览管理员发布的课程；PPT 支持在线全屏播放和退出，不能下载 PPTX/PDF，Markdown 资料可查看和下载
- 按学员或班级排课、批改作业、重置学生密码
- 归档学生不出现在教师维护和排课列表，历史教学记录只读
- 登录设备、操作审计和教师密码维护

### 管理员端

- 职员账号与学生开户、教学管理和全机构数据查看
- 课程包、课程、PPT、工程包和成果包维护
- 为每门课程配置学生本机提交的文件类型和单文件上限（最高 20 MB）
- 课程包教师白名单、课程作者、排课和批改管理
- 多 AI 服务商、模型路由、机构级密钥和用量管理；可连接云端 API 可访问的 Ollama、LM Studio 或私有 OpenAI 兼容服务
- 内置 MiniMax 与即梦 Seedance 视频生成适配器；可控制学生在 Agent 中显式选择的文字模型，声明式教学插件的能力和签名要求见 [插件协议](docs/PLUGIN_PROTOCOL.md)
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

本机 SQLite API 测试已验证 10 名不同学生同时提交，以及同一学生并发提交产生连续版本；这只证明提交事务和唯一约束的本地回归，不替代阿里云 PostgreSQL、对象存储和网关上的真实并发压测。

50 账号非 AI 接口压测工具：

```powershell
npm.cmd run test:load -- --base-url https://203.0.113.10 --accounts tools/pilot-load-accounts.json --report test-results/pilot-load.json
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
- [插件安装与开发规范](docs/PLUGIN_PROTOCOL.md)

正式迁移前必须冻结本地写入并使用迁移副本。当前 `workspace_data` 保持原样；没有真实 PostgreSQL、对象存储和独立备份位置时，不得执行正式迁移。

## 安全边界

- 生产令牌保存在 Windows Credential Manager，不保存在桌面 WebView 的 `localStorage`
- 生产 CSP 仅允许正式 API、更新服务和受控媒体源
- 云端密钥使用 AES-GCM；主密钥只通过服务器环境或密钥管理服务注入
- 机构管理员只能导出本机构脱敏数据，不能恢复或覆盖平台数据库
- 备份目标必须与生产对象存储独立，并执行 SHA-256 校验和恢复演练
- 连续登录失败 5 次锁定 15 分钟；会话可撤销且高风险操作写入审计
- 数据到期删除必须经过预览、例外检查、管理员审批和精确确认，不自动静默删除
