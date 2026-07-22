# CoderAI 学堂项目状态

更新时间：2026-07-22（北京时间）
目标版本：`0.2.0-beta.1`
发布范围：单机构、免费封闭内测、最多 50 人同时在线、Windows、必须联网

## 结论

云端内测所需的主体代码已经完成：机构租户、PostgreSQL/Alembic、S3 对象存储、Redis/Celery、服务器密钥保护、Tauri 桌面工程、CI、监控、备份、迁移、隐私保留和灰度更新均已落入仓库。

项目当前仍是“代码与本机验证阶段”，不是“已经上线”。部署入口已切换为固定公网 IPv4 + Let's Encrypt 短期 IP 证书，不再依赖域名；正式发布仍依赖服务器连接信息、独立备份位置、Windows 代码签名证书、机构真实 AI 密钥和合规确认。

正式 `workspace_data` 和 SQLite 未执行云迁移，继续保持原样。迁移前备份位于 `E:\CoderAI学堂-backups\pre-cloud-20260720-192149`，云改造前 Git 基线为 `1a3848a`。

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
- 严格 CSP、最小能力清单、NSIS 元数据、图标和卸载清理
- 生产构建通过 `VITE_API_URL` 连接唯一 HTTPS API，不包含 Python sidecar
- Tauri 更新公钥已提交；私钥材料保存在仓库外 `E:\CoderAI学堂-secrets`

### 部署与运维

- Docker Compose：Caddy、FastAPI、Worker、Beat、PostgreSQL、Redis、MinIO
- Prometheus、PostgreSQL/Redis/Node exporter 和告警规则
- 可选 HTTPS Webhook 告警，未配置时不发送到失效占位地址
- PostgreSQL 与对象文件独立备份、30 个日备份、12 个月备份
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
- 文字、图片、视频、编程助手、工作流和作品库
- 个人/班级排课、课程资料、作品提交、版本和反馈
- Markdown 编辑、图片预览下载、归档和回收站

### 教师端

- 获授权班级、未归档学生、课程包、排课、提交和安全记录
- 课程只读备课、PPT PDF 预览、Markdown 查看下载
- 学员/班级排课、冲突提示、取消、批改和历史只读
- 学生分班、批量转班和固定密码重置

### 管理员端

- 职员账号、学生开户、学生维护和完整教学管理
- 课程包、课程、三类资料、作者和教师白名单
- 多服务商、多模型、能力路由、审核和用量
- 隐私、账号安全、机构数据导出、插件与许可证

## 验证状态

2026-07-22 本机最终回归结果：

- Ruff 通过；80 项后端测试完成，其中 79 项通过，1 项云容器集成测试因本机没有 Docker/WSL 跳过
- 前端生产构建通过；Playwright 19 项桌面 E2E 全部通过
- Rust 格式检查、Clippy `-D warnings` 和 1 项 Tauri 单元测试通过
- `pip-audit` 未发现已知漏洞，`npm audit --audit-level=high` 为 0 个漏洞
- LibreOffice 真实 PPTX 转 PDF、教师 PDF 预览和原件下载阻断测试通过
- 未签名 Windows 可执行文件和 NSIS 安装包构建成功；静默安装、启动 8 秒及卸载清理闭环通过

本机未执行正式签名、SmartScreen、真实自动更新、50 账号压测和机构真实 AI 调用。云容器集成测试由远端 CI 或后续 Linux 测试环境执行。

本机可执行的门禁：

```powershell
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

本机限制：没有 Docker 或 WSL，因此 PostgreSQL/Redis/MinIO 集成测试只能由 GitHub Actions 或 Linux 测试环境执行。没有正式 PFX，因此只能完成未签名 Tauri/NSIS 冒烟，不能验证 Authenticode、SmartScreen 和正式更新。

## 上线前外部阻塞

- [ ] 创建私有 Git 远端并保护 `main`、`release/**` 和正式标签
- [ ] 准备境内 Linux x64 主机、100 GB 独立数据盘和安全组
- [ ] 提供固定公网 IPv4、运维邮箱，并确认 80/443 安全组
- [ ] 在真实 ECS 验证 Let's Encrypt IP 证书首次签发、12 小时续期和 Caddy 重载
- [ ] 准备与生产数据盘独立的对象备份存储
- [ ] 采购 Windows Authenticode 代码签名证书
- [ ] 配置公网 IP 下的签名更新产物存储
- [ ] 提供机构真实 AI 服务商密钥并确认数据流向
- [ ] 完成监护人授权文本、隐私政策、AI 标识和备案责任确认
- [ ] 提供 50 个隔离压测账号与可写测试课程/提交数据

## 上线执行顺序

1. 在测试云环境部署容器并执行真实集成测试。
2. 使用迁移副本演练 SQLite -> PostgreSQL/S3，核对数量和哈希。
3. 创建唯一强密码管理员并重新录入机构 AI 密钥。
4. 执行 50 账号压测、真实 AI 成功/失败测试和恢复演练。
5. 构建并签名 `0.2.0-beta.1`，在 Windows 10/11 和高 DPI 环境验证。
6. 依次进行内部管理员、5 至 10 人、单班和全机构灰度，每阶段稳定 3 个教学日。
7. 正式迁移时冻结本地写入；云端验收失败则恢复原本地版本，不做双向同步。

详细命令见 [云端部署](CLOUD_DEPLOYMENT.md)、[迁移与回滚](CLOUD_MIGRATION.md)、[备份恢复](BACKUP_RECOVERY.md)、[Windows 发布](WINDOWS_RELEASE.md) 和 [合规清单](PILOT_COMPLIANCE.md)。

## 不在本次内测范围

- 公开机构注册、在线支付、发票、订阅和课程市场
- 公共 Web 学生端和完整离线课堂
- 多人实时协作和跨机构数据共享
- 本地大模型及学生电脑本地 Python 服务
- 设备绑定离线许可证作为云端授权
