# CoderAI 学堂项目状态

更新时间：2026-08-23（北京时间）
目标版本：Windows 客户端 `0.2.0-beta.7`，云端 API `0.2.0-beta.5`
发布范围：单机构、免费封闭内测、最多 50 人同时在线、Windows、必须联网

## 结论

云端内测所需的主体代码已经完成，并已部署 beta 更新：机构租户、PostgreSQL/Alembic、S3 对象存储、Redis/Celery、服务器密钥保护、Tauri 桌面工程、CI、监控、备份、迁移、隐私保留和灰度更新均已落入仓库；2026-08-14 发布云端 API `0.2.0-beta.4` 和客户端 `0.2.0-beta.6`，增加服务端只读请求并发闸门、客户端版本追踪和 Caddy 敏感请求头脱敏。

阿里云单机构技术内测环境已经部署并可由 Windows 客户端访问，但这不等于完成 50 人上线验收。当前主机低于计划规格，且独立备份、Windows 代码签名、机构真实 AI 密钥、50 人压测和合规确认尚未完成；在这些问题解决前不得将该环境描述为生产安全环境。

正式 `workspace_data` 和 SQLite 未执行云迁移，继续保持原样。迁移前备份位于 `E:\CoderAI学堂-backups\pre-cloud-20260720-192149`，云改造前 Git 基线为 `1a3848a`。

## 当前试点部署

- 公网入口：`https://39.108.109.94`，机构代码 `coderai-pilot`，机构名称 `CoderAI`
- 数据方式：全新初始化；PostgreSQL、Redis、MinIO、FastAPI、Celery、Caddy 和监控容器均已运行
- 服务端发布：`0.2.0-beta.4`，构建标识 `f9db63b8800a5ff732b3255e72d61a71f6bb6b08`；Alembic 已升级至 `20260727_0003`
- 上一版快照：服务器 `/srv/coderai/release-backups/pre-beta2-20260726T104726Z`
- HTTPS：Let's Encrypt IP SAN 证书已签发，ACME Webroot 路径与强制证书重载已修复；2026-08-14 自动续期成功，12 小时定时器继续启用
- 发布前快照：服务器 `/srv/coderai/release-backups/pre-beta3-20260730T034851Z`，包含 PostgreSQL、MinIO 对象、旧源码和生产配置
- 本轮发布前快照：服务器 `/srv/coderai/release-backups/pre-567800f-20260730T114459Z`，包含 PostgreSQL、MinIO 对象、旧源码和生产配置
- 稳定性修复前快照：服务器 `/srv/coderai/release-backups/pre-f9db63b-20260814T153842Z`，包含 PostgreSQL、MinIO 对象、旧源码和生产配置；更新文件备份位于 `/srv/coderai/release-backups/pre-beta6-updater-20260814T154952Z`
- 桌面端：`0.2.0-beta.6` 已注入正式 API、机构代码、客户端版本标识和更新地址
- 更新服务：beta.6 安装包、`.sig` 和 `latest.json` 已由 `/desktop-updates/` 提供，公开安装包 SHA-256 为 `8093a529b4fd286ea11b5ec731b162e168f8d5425ab70568200e0176af627f6f`
- 稳定性验证：20 个并发就绪检查全部返回 200，发布后连接池超时计数为 0，API、Worker、Beat、Backup 和 Caddy 重启计数均为 0
- 安全处置：Caddy 已删除访问日志中的 Authorization、Cookie、教师令牌和学生令牌；上线后撤销 8 个教师会话及 5 个学生会话
- 资源限制：当前 ECS 为 2 核、约 1.6 GB 内存、40 GB 系统盘和 4 GB Swap，不满足 4 核、8 GB、100 GB 的 50 人目标规格
- 灾备限制：`CODERAI_BACKUP_ENABLED=false`，暂无独立 OSS/S3 备份；监控持续上报 `coderai_backup_enabled 0`
- 发布限制：安装包没有 Authenticode 签名，仅限知情的内部技术测试

## 当前开发工作区（尚未发布）

以下功能已归入本地 API `0.2.0-beta.5`、客户端 `0.2.0-beta.7`，但尚未提交 Git、构建 Windows 安装包或部署到阿里云；线上版本仍是 API `0.2.0-beta.4`、客户端 `0.2.0-beta.6`：

- 教师/管理员 PPT PDF 预览增加全屏播放、`Esc`/按钮退出和窗口内全屏回退；学生端仍无 PPT 权限
- 管理员可按课程设置学生本机提交扩展名和 1 至 20 MB 上限；学生本机文件直接形成提交附件，不再创建或污染作品库记录
- 文件上传增加流式限额、UTF-8、ZIP 结构、可执行文件及 PDF/图片/视频文件头校验；本机图片附件继续进入教师审核
- 文字和图片工作台改用可恢复、幂等、可取消和重试的异步任务；服务商默认超时调整为文字/Agent 120 秒、图片 180 秒
- 新增学习 Agent：会话列表、单会话持久记忆、模型白名单、工具确认、真实工作流选择和 7 天临时图片/视频附件
- 即梦预设正式声明 Seedance 视频能力，并通过火山方舟异步任务协议提交、查询和回收结果
- 文字生成增加默认“常规生成”；长标题按首个非空行保存至 160 字符，作品卡两行显示，抽屉标题完整换行
- 工程包与文字作品统一使用源码/GFM 双栏实时 Markdown；提交历史默认折叠并支持版本深链接、刷新和不可变快照
- 新增 Alembic `20260823_0005`、`20260823_0006` 和 SQLite 幂等迁移；个人导出、删除、保留及备份文件范围覆盖新附件和 Agent 数据
- 新增可无 API Key 的 `local_openai_compatible` 服务商，连接 API 容器可访问的 Ollama、LM Studio 或私有 OpenAI 兼容地址；不负责安装或托管模型
- MiniMax 视频适配器和本地模型适配器由 `planned` 调整为可配置状态；插件管理增加安装规范，插件 v1 继续限定为签名的声明式文字工具
- 新增 Alembic `20260815_0004` 和 SQLite 幂等迁移，合并历史重复提交、重排版本并建立提交及版本唯一键
- 本机目标测试已覆盖 10 名不同学生并发提交和同一学生重复并发；尚未在阿里云 PostgreSQL、MinIO、Caddy 及当前 ECS 规格上执行写入压测，因此不能据此提高线上并发承诺

## 已完成

### 云端基础

- `organizations` 机构实体及全业务 `organization_id` 边界
- 机构 + 用户名唯一约束、会话机构声明和逐请求租户校验
- PostgreSQL 生产数据层和 Alembic 初始迁移
- SQLite 仅保留开发、测试和一次性迁移用途
- S3 兼容对象存储、机构键前缀、鉴权读取和 SHA-256 校验
- Redis/Celery 处理 PPT、工作流、视频轮询和保留扫描
- AES-GCM 机构密钥保护、旧 DPAPI 密钥拒绝迁移和主密钥轮换命令
- 机构管理员脱敏导出；云端整库恢复接口和界面对机构管理员关闭

### 桌面端

- 完整 Tauri 2 Rust 工程、`Cargo.lock` 和固定 Rust 工具链
- Windows Credential Manager 令牌存储和旧 `localStorage` 令牌迁移清理
- 单实例、应用日志、网络状态、版本拦截和签名更新插件
- 短暂断线或 `502/503/504` 时共享健康探测并自动恢复只读请求，网络故障不再注销教师账号
- 下载模板、CSV、课程资料、课程包、作品和数据导出统一使用 Windows 原生另存为
- Release 可执行文件使用 Windows GUI 子系统，启动不再弹出控制台黑框
- 严格 CSP、最小能力清单、NSIS 元数据、图标和卸载清理
- 生产构建通过 `VITE_API_URL` 连接唯一 HTTPS API，不包含 Python sidecar
- Tauri 更新公钥已提交；私钥材料保存在仓库外 `E:\CoderAI学堂-secrets`

### 部署与运维

- Docker Compose：Caddy、FastAPI、Worker、Beat、PostgreSQL、Redis、MinIO
- Prometheus、PostgreSQL/Redis/Node exporter 和告警规则
- 可选 HTTPS Webhook 告警，未配置时不发送到失效占位地址
- PostgreSQL 与对象文件独立备份能力、30 个日备份、12 个月备份策略（当前试点尚未配置独立备份目标）
- 隔离数据库恢复演练和逐文件 SHA-256 校验
- `/api/health/live`、`/api/health/ready`、`/api/version` 和内部 `/metrics`
- JSON 结构化日志、请求错误率、队列、磁盘、数据库、AI 和备份监控指标
- 固定公网 IPv4 HTTPS、Certbot 5.4 IP 证书、12 小时续期及 48 小时到期告警
- 同一 HTTPS IP 下的 `/desktop-updates/` 签名更新文件托管

### 数据迁移与治理

- 一次性 SQLite -> PostgreSQL + S3 迁移命令
- 迁移前 SQLite/文件清单备份，迁移后数量与 SHA-256 校验
- 迁移完成后本地只读标记和显式回滚解除命令
- AI 密钥不迁移，管理员必须在云端重新录入
- 默认 365 天保留，支持预览、例外、审批、精确确认和脱敏审计
- 监护人授权、版本化隐私政策、云端 AI 开关和学生数据权利

### 自动化与发布

- Node、Python、Rust 和 Python 依赖版本固定并生成锁文件
- GitHub CI：Ruff、pip/npm 安全审计、后端、前端、Tauri、E2E
- PostgreSQL、Redis、MinIO 真实服务集成测试
- Windows 未签名桌面可执行文件及 NSIS 安装、启动、卸载冒烟
- Windows Authenticode + Tauri 更新签名发布工作流
- 更新清单生成与本地 Tauri 构建脚本
- 50 账号非 AI 接口压测工具及私有账号模板

## 既有产品功能

### 学生端

- 管理员分配账号登录，自助注册关闭
- 小学低龄、小学高龄、初中高中三类学龄和对应 AI 策略
- 文字、图片、视频、工作流和作品库
- 个人/班级排课、个人工程包在线查看/实时预览/编辑/保存、作品库或本机附件提交、版本和反馈；学生端不显示 PPT/PDF 和成果包
- 常规文字生成、异步文字/图片任务、单会话学习 Agent、Markdown 双栏编辑、图片预览下载、归档和回收站

### 教师端

- 获授权班级、未归档学生、课程包、排课、提交和安全记录
- 课程只读备课、PPT PDF 全屏播放、Markdown 查看下载
- 学员/班级排课、冲突提示、取消、批改和历史只读
- 学生分班、批量转班和固定密码重置

### 管理员端

- 职员账号、学生开户、学生维护和完整教学管理
- 课程包、课程、三类资料、作者和教师白名单
- 多服务商、多模型、能力路由、审核和用量
- 隐私、账号安全、机构数据导出、插件与许可证；插件 v1 安装和开发边界已形成独立规范

## 验证状态

2026-07-26 本机 release-candidate 回归结果：

- 已检查 `main..HEAD` 云迁移 diff：150 个文件变更，覆盖后端租户/存储/任务队列、部署脚本、Tauri 桌面工程、前端云端运行时和云迁移测试；`git diff --check main..HEAD` 通过。
- Ruff 通过；86 项后端测试完成，其中 85 项通过，1 项云容器集成测试因本机未启用云集成服务跳过。
- 前端生产构建通过；`react-router-dom` 已从 `7.18.1` 调整到 `7.11.0`，`postcss` 已从 `8.5.16` 调整到 `8.5.23`，用于消除本机 `npm audit` 首次报告的高危 advisories。
- Playwright 20 项桌面 E2E 全部通过，新增短暂 `503` 自动恢复和学生 CSV 模板真实下载覆盖。
- Rust 格式检查、Clippy `-D warnings` 和 1 项 Tauri 单元测试通过。
- Tauri updater 私钥已从仓库外安全目录加载，beta.2 release EXE、NSIS 安装包及 `.sig` 构建成功；安装包仍无 Windows Authenticode 签名。
- LibreOffice 真实 PPTX 转 PDF、教师 PDF 预览和原件下载阻断测试通过。
- `python -m pip_audit --requirement requirements.lock` 未发现已知漏洞；当前机器已按 `requirements-dev.in` 安装 `pip-audit==2.9.0` 才能执行该门禁。
- `npm audit --audit-level=high` 首次发现 `postcss` 和 `react-router` 高危 advisories，依赖已修复；修复后 npm 11.12.1 对 `https://registry.npmjs.org/-/npm/v1/security/advisories/bulk` 的响应解析失败，报 `invalid json response body`，`registry.npmmirror.com` 不实现 audit API，因此本机无法取得最终 npm audit 通过输出。`npm ls react-router react-router-dom postcss --depth=1` 已确认当前树为 `react-router-dom@7.11.0`、`react-router@7.11.0`、`postcss@8.5.23`。

尚未执行 Authenticode、SmartScreen、跨版本自动安装与回滚、50 账号压测、机构真实 AI 调用和独立备份恢复演练。本机单元测试中的云容器集成项仍需由 CI 或具备 PostgreSQL/Redis/MinIO 的 Linux 测试环境执行。

2026-08-23 本轮必要及关联性检查：异步任务幂等、超时、取消和重试，Agent 会话隔离、模型白名单、真实 DAG、视频状态同步和临时附件，Seedance 协议，提交附件权限与不可变版本快照，隐私导出/删除/保留，以及 Alembic/SQLite 重复迁移均通过；前端生产构建通过，Agent、长标题、实时 Markdown、课程提交和版本深链接在 `900x800`、`1280x720`、`1920x1080` 目标视口通过。未运行全量门禁，也未执行云端写入压测、桌面打包或部署。

本机可执行的门禁：

```powershell
npm.cmd run lint:backend
npm.cmd test
npm.cmd run build
npm.cmd run test:tauri
npm.cmd run test:e2e
npm.cmd run test:ppt-conversion
npm.cmd run check:full
npm.cmd audit --audit-level=high
python -m pip_audit --requirement requirements.lock
```

本机限制：没有 Docker 或 WSL，因此 PostgreSQL/Redis/MinIO 集成测试只能由 GitHub Actions 或 Linux 测试环境执行。没有正式 PFX 或 Authenticode 证书，因此不能验证 Authenticode 与 SmartScreen；Tauri updater 签名与公网发布已完成。2026-07-26 npm audit 还受 npmjs audit endpoint 解析失败影响，需要在 CI 或网络恢复后复核最终 audit 输出。

## 上线前外部阻塞

- [ ] 创建私有 Git 远端并保护 `main`、`release/**` 和正式标签
- [x] 已准备境内 Linux x64 主机和安全组；当前仅 2 核、约 1.6 GB、40 GB，仍需扩容到目标规格
- [x] 已提供固定公网 IPv4、运维邮箱，并确认 80/443 安全组
- [x] 已在真实 ECS 验证 Let's Encrypt IP 证书首次签发、12 小时续期和 Caddy 重载
- [ ] 准备与生产数据盘独立的对象备份存储
- [ ] 采购 Windows Authenticode 代码签名证书
- [x] 已在仓库外提供 Tauri updater 私钥和密码；GitHub 远端已建立，CI secret 仍需配置
- [x] 已配置公网 IP 下的 Tauri 签名更新产物存储
- [ ] 配置 Alertmanager 邮件或 Webhook 告警接收端
- [ ] 提供机构真实 AI 服务商密钥并确认数据流向
- [ ] 完成监护人授权文本、隐私政策、AI 标识和备案责任确认
- [ ] 提供 50 个隔离压测账号与可写测试课程/提交数据

## 上线执行顺序

1. 在测试云环境部署容器并执行真实集成测试。
2. 使用迁移副本演练 SQLite -> PostgreSQL/S3，核对数量和哈希。
3. 创建唯一强密码管理员并重新录入机构 AI 密钥。
4. 执行 50 账号压测、真实 AI 成功/失败测试和恢复演练。
5. 对 `0.2.0-beta.7` 继续完成 Windows 10/11、高 DPI、自动更新和回滚矩阵验证。
6. 依次进行内部管理员、5 至 10 人、单班和全机构灰度，每阶段稳定 3 个教学日。
7. 正式迁移时冻结本地写入；云端验收失败则恢复原本地版本，不做双向同步。

详细命令见 [云端部署](CLOUD_DEPLOYMENT.md)、[迁移与回滚](CLOUD_MIGRATION.md)、[备份恢复](BACKUP_RECOVERY.md)、[Windows 发布](WINDOWS_RELEASE.md) 和 [合规清单](PILOT_COMPLIANCE.md)。

## 不在本次内测范围

- 公开机构注册、在线支付、发票、订阅和课程市场
- 公共 Web 学生端和完整离线课堂
- 多人实时协作和跨机构数据共享
- 学生电脑本地模型托管及本地 Python 服务；当前仅支持 API 云端容器可访问的 OpenAI 兼容推理服务
- 设备绑定离线许可证作为云端授权
