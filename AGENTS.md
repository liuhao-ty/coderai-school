# CoderAI 学堂开发代理指南

本文件适用于整个仓库。若子目录以后出现更具体的 `AGENTS.md`，以离目标文件最近的规则为准。

## 1. 开始工作前

1. 先运行 `git status --short`，确认当前分支和用户已有修改；不得覆盖、还原或清理不属于本次任务的改动。
2. 先阅读与任务相关的代码，再决定实现方式。不要仅根据旧计划、聊天记录或早期 SQLite 版本推断现状。
3. 动态状态以以下位置为准：
   - 产品和部署状态：`docs/PROJECT_STATUS.md`
   - 架构与权限边界：`docs/ARCHITECTURE.md`
   - 实际脚本和依赖：`package.json`、锁文件、`.python-version`、`.nvmrc`、`rust-toolchain.toml`
   - 当前版本：`package.json`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml`、`backend/app/main.py`
4. 修改应贴合现有 React、FastAPI、Tauri、SQLAlchemy 和 Ant Design 模式，避免无关重构。
5. 除非用户明确要求，不执行发布、云端部署、数据迁移、会话批量撤销、Git 提交、推送或标签操作。

## 2. 产品与部署边界

- 产品是 Windows Tauri 桌面应用，不是公开 Web 学生端。
- 生产客户端是薄客户端，只连接唯一 HTTPS 云端 API；不得恢复 Python sidecar、客户端业务 SQLite 或本地 AI Key。
- 本地开发和自动化测试允许使用 SQLite 与 `workspace_data/`，但这些不是生产架构。
- 云端业务使用 FastAPI、PostgreSQL、Redis/Celery 和 S3 兼容对象存储；Caddy 提供 TLS、反向代理和桌面更新文件。
- 当前为单机构封闭技术内测。未完成的 Authenticode、独立异地备份、50 人压测、真实 AI 验收或合规事项必须如实标记，不能推断为已完成。
- 不主动恢复此前跳过的 P0 证据链产品功能；测试、日志和必要运维记录仍需保留。

## 3. 主要目录

```text
src/                         React + TypeScript 前端
src/features/                按身份和业务领域拆分的界面
src/lib/api.ts               Axios、组织代码、恢复重试和客户端版本头
src/lib/downloads.ts         浏览器/Tauri 下载与原生另存为
src/store/appStore.ts        客户端共享状态
src-tauri/                   Tauri 2 Rust 桌面工程
backend/app/main.py           主 FastAPI 接口和领域权限
backend/app/auth.py           账号、密码、会话和角色依赖
backend/app/models.py         SQLAlchemy 模型
backend/app/db.py             本地兼容迁移和数据库会话
backend/app/storage.py        本地/S3 文件引用与鉴权读取
backend/app/services.py       AI 服务商适配和能力路由
backend/migrations/           已发布的 Alembic 云端迁移
tests/                        Python 单元和 API 测试
tests/e2e/                    Playwright 桌面视口流程测试
deploy/                       Compose、Caddy、监控、证书和备份
tools/                        构建、更新清单、PPT 转换和压测工具
docs/                         架构、部署、发布、备份和合规说明
```

## 4. 必须保持的业务规则

### 身份与机构

- 所有云端业务数据必须带机构归属。机构从请求头和已验证会话推导，不接受客户端任意指定资源所属机构。
- 用户名唯一性是“机构 + 规范化用户名”，不是全平台绝对唯一。
- 学生只能由管理员单独创建或 CSV 批量开户；`/api/auth/student-register` 保持关闭。
- 教师不能创建、导入、启停或归档学生账号。教师可查看未归档学生，并执行分班、排课、批改和固定密码重置。
- 管理员拥有全机构教学管理和系统管理权限。
- 归档学生不得出现在教师维护和新排课选项中；获授权班级内的历史作品、提交和批改仅只读。
- `classroom_teachers` 控制班级授权，`course_package_teachers` 控制课程包预览和排课授权。前端隐藏不能替代后端权限校验。
- 教师被撤销课程包权限后，不能查看资料、创建或修改排课、继续批改；已有学生排课和提交不能因此被删除。

### 课程、排课与作品

- 课程结构固定为“课程包 -> 课程 -> PPT、工程包 Markdown、成果包 Markdown”。三类资料均可缺省。
- 课程包至少包含一门课程才可发布；资料缺失不能阻止课程发布和排课。
- 教师可在线预览转换后的 PDF，但不能下载 PPTX/PDF；教师可查看和下载工程包、成果包 Markdown。
- 学生只可访问工程包，不得获得 PPT/PDF 或成果包的列表、预览、下载和直连接口权限。
- 学生工程包支持填写、Markdown 源码编辑、GFM 预览和答案保存；不得修改管理员原始模板。
- 新作品提交只通过课程排课接口完成。旧课堂任务保留通知、历史提交和反馈只读，不重新开放提交入口。
- 学员作品的“提交时间”按最近一次提交版本计算；北京时间展示与日期筛选语义必须保持一致。

### AI、工作流与内容安全

- AI 服务按机构支持多服务商、多模型和能力路由。模型可见不等于可用，后端必须再次校验服务商、模型和能力。
- 不得用文字主模型状态推断图片或视频能力；统一使用模型目录和 provider status。
- 服务商专用协议使用现有适配器，不要假设所有服务商兼容 OpenAI 接口。
- API Key 只能以加密形式存储，不能写入响应、日志、Git、测试快照或错误详情。更新配置时空 Key 不得清除已有密钥。
- 工作流是 DAG，必须保持循环、重复边、悬空节点、不可达节点和终端节点校验；分支失败不能破坏无关成功分支。
- 学生输入与 AI 输出继续经过年龄策略、内容审核、用量记录和作品权限链路。

## 5. 前端约定

- 继续使用 React、TypeScript、Ant Design、React Flow 和 Lucide 图标，不引入第二套 UI 框架。
- 管理员、教师、学生使用独立路由和导航信息架构；不要将管理入口重新混入同一工作台。
- 桌面工作台保持左侧导航和顶部栏固定，仅右侧内容区纵向滚动。
- 紧凑管理界面使用稳定网格和响应式换行；不得让文字、分数输入、标签或工具栏在 `900x800` 等窄窗口中重叠。
- 课程、作品和资料的原始云端 URL、本地路径及对象存储键不得直接展示。
- 下载统一复用 `src/lib/downloads.ts`。Tauri 中优先使用系统原生另存为，避免调用浏览器下载管理器；浏览器行为只作为开发回退。
- 图片使用鉴权 Blob 或受控远程预览，关闭或切换作品时释放 Blob URL。
- 业务请求统一走 `src/lib/api.ts`，保留机构代码、客户端版本、只读请求并发上限、取消旧请求和短暂 502/503/504 恢复逻辑。
- 路由变更需要保留已有书签的兼容跳转，并补充前进、后退和刷新恢复测试。

## 6. 后端、数据库与文件

- API 权限在后端逐接口检查。不得依赖前端传入的 `can_manage`、角色、机构或资源所有者字段。
- PostgreSQL 是云端结构来源，新增生产字段必须创建新的 Alembic 迁移；不要修改已经发布的迁移文件。
- SQLite 兼容迁移必须可重复执行，并在破坏性结构调整前自动备份；只用于本地和历史迁移。
- 对象存储引用使用 `object://bucket/key` 和机构前缀。前端只能通过鉴权文件接口访问，不暴露 MinIO/S3 凭据或真实路径。
- 删除数据库记录和对象文件时必须保持事务补偿或隔离区恢复能力，避免记录与文件不一致。
- 外部 AI、视频和文件网络调用前，先将所需数据库配置复制为运行时数据并释放数据库连接，不要在远程等待期间占用连接池。
- GET/HEAD 并发闸门、数据库池参数、`DATABASE_BUSY` 503 和 `Retry-After` 行为属于低配试点稳定性保护，不得无依据删除。
- Caddy 访问日志必须继续删除 Authorization、Cookie、教师令牌和学生令牌；API 日志不得输出密码、刷新令牌、AI Key 或正文敏感数据。
- 时间响应使用现有北京时间格式化工具。修改日期筛选时要明确起止日期包含全天，不要混用本地机器时区。

## 7. 本地环境与常用命令

固定工具版本以仓库文件为准，当前基线为 Node `24.15.0`、Python `3.12.10`、Rust `1.88.0`。Windows PowerShell 优先使用 `npm.cmd`。

```powershell
# 安装
npm.cmd ci
& .\.venv\Scripts\python.exe -m pip install --requirement requirements-dev.lock

# 本地开发
$env:PATH="$(Resolve-Path .venv\Scripts);$env:PATH"
npm.cmd run dev

# 分项检查
npm.cmd run lint:backend
npm.cmd test
npm.cmd run build
npm.cmd run test:tauri
npm.cmd run test:e2e
npm.cmd run test:ppt-conversion

# 完整门禁
npm.cmd run check:full
```

- 本地 Web：`http://127.0.0.1:5173`
- 本地 API：`http://127.0.0.1:8000`
- 健康检查：`http://127.0.0.1:8000/api/health`
- `npm.cmd run check:full` 包含 Ruff、Python 测试、前端构建、Rust 测试和 Playwright E2E。
- 本机没有 Docker/WSL 时，真实 PostgreSQL、Redis、MinIO 云集成测试允许明确跳过，但最终报告必须说明；不能把跳过写成通过。
- 日常功能更新只运行直接覆盖改动的必要检查，以及可能受影响模块的关联性回归检查；不默认运行 `npm.cmd run check:full` 或其他全量检查。界面变更只检查本次涉及的目标视口和关联流程。

## 8. 代码和测试要求

- 功能更新遵循“必要检查 + 关联性检查”原则：根据改动范围选择最小充分测试集，不因仓库存在全量门禁就自动执行全量测试。只有用户明确要求，或正在执行正式发布、云端部署等发布流程时，才运行对应的完整门禁。
- TypeScript 保持类型完整，不用 `any` 绕过领域模型，除非第三方边界确实无法描述并有局部说明。
- Python 使用现有 Pydantic/SQLAlchemy 结构，不通过字符串拼接处理结构化 JSON、CSV 或数据库查询。
- 只为复杂、不明显的逻辑添加简短注释，不添加逐行叙述注释。
- 新增接口至少覆盖成功、角色越权、跨机构或跨资源访问和失败状态。
- 权限、迁移、文件删除、会话、密钥和排课改动必须有后端测试；主要用户流程和布局改动应补 Playwright。
- 测试数据写入 `test-results/` 或测试临时目录，不污染 `workspace_data/` 的现有用户数据。
- 不通过降低断言、扩大权限、禁用审核、延长无限重试或删除失败分支来让测试通过。

## 9. 密钥、数据与 Git 安全

- 不读取、输出、提交或复制仓库外密钥，除非用户明确要求执行对应发布操作。
- 禁止提交：`.env`、`deploy.env`、密码、令牌、PFX、Tauri 私钥、SSH 私钥、压测账号、数据库转储、对象数据、`workspace_data/`、构建产物和测试报告。
- 不在命令行中直接写管理员密码或 AI Key；使用安全环境变量、交互输入或现有密钥加载脚本。
- 不恢复任何历史默认管理员账号密码或其他硬编码管理员凭据。首个云端管理员由 CLI 创建。
- 保持 Caddy 和 API 日志脱敏；发现历史令牌泄露时先修复日志，再撤销相关会话。
- 不使用 `git reset --hard`、`git checkout --`、`docker compose down -v` 或任何未经确认的递归删除。
- 提交前执行 `git diff --check`；提交内容保持聚焦，不混入缓存、生成文件或无关格式化。

## 10. 版本、桌面发布与云端部署

只有在用户明确要求发布或部署时执行本节。

### 版本更新

- Windows 版本同步更新 `package.json`、`package-lock.json`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml` 和 `src-tauri/Cargo.lock`。
- API 版本同步更新 `backend/app/main.py`、Compose 镜像标签、云集成断言和相关文档。
- 最低客户端版本只能设置为已经公开提供更新产物的版本，避免把所有现有客户端锁死。

### Windows 发布

- 使用 `tools/build-tauri.ps1` 注入正式 HTTPS API、机构代码和更新地址。
- 使用 `tools/generate-update-manifest.ps1` 生成 UTF-8 无 BOM 的 `latest.json`。
- Tauri updater `.sig` 不等于 Windows Authenticode。没有有效 PFX 时必须明确安装包仍为 `NotSigned`，不能宣称 SmartScreen 验收完成。
- 上传后核对本机构建文件、服务器文件和公网下载文件的 SHA-256，并验证 `latest.json` 的版本、URL、签名和 `Cache-Control: no-store`。

### 云端部署

1. 部署前创建并校验 PostgreSQL、对象存储、旧源码和生产配置快照。
2. 使用提交归档上传源码，不上传本地依赖、缓存、密钥或测试数据。
3. 先验证 Compose/Caddy 配置，再构建镜像和执行 Alembic；不得删除持久卷。
4. 使用滚动重建替换 API、Worker、Beat、Backup 和 Caddy，保留旧镜像和可执行回滚目录。
5. 验证 `/api/version`、`/api/health/ready`、对象存储、Redis、容器重启数、连接池超时和近期 5xx。
6. 涉及日志脱敏时，用伪敏感头发起请求并确认日志不含标记值，再处理历史会话。
7. 观察至少 30 分钟后再记录成功状态；观察命令超时不等于服务失败，应单独执行终检。
8. 更新 `README.md`、`docs/PROJECT_STATUS.md`、`docs/ARCHITECTURE.md`、部署手册和 Windows 发布手册。

详细流程见 `docs/CLOUD_DEPLOYMENT.md`、`docs/BACKUP_RECOVERY.md`、`docs/CLOUD_MIGRATION.md` 和 `docs/WINDOWS_RELEASE.md`。

## 11. 完成任务时

- 说明修改了什么、为什么，以及实际运行了哪些检查。
- 未运行、跳过或受外部条件阻塞的测试必须明确列出。
- 发布任务需给出版本、提交 SHA、健康状态、产物哈希、快照位置和仍存在的上线限制。
- 保持工作区清洁；若存在用户原有改动，明确区分但不要处理它们。
