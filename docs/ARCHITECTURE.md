# CoderAI 学堂架构

更新时间：2026-08-23（北京时间）
目标版本：Windows 客户端 `0.2.0-beta.7`，云端 API `0.2.0-beta.5`

## 1. 部署边界

生产形态为“Windows 薄桌面端 + 单一云端 API”，不在学生电脑运行 Python sidecar 或业务 SQLite。

```text
+--------------------------+
| Windows Tauri 2 client   |
| React / Credential Store |
+------------+-------------+
             | HTTPS + organization code + session token
             v
+--------------------------+
| Caddy                    |
| TLS / headers / proxy    |
+------------+-------------+
             v
+--------------------------+
| FastAPI, two workers     |
| auth / teaching / AI     |
+-----+----------+---------+
      |          | \
      |          |  +----> Institution AI providers
      |          +-------> Redis + Celery workers
      +------------------> PostgreSQL
      +------------------> S3-compatible object storage

Operations: Prometheus + Alertmanager + independent backup storage
```

本地开发继续支持 FastAPI + SQLite + 本地文件，以便自动化测试和历史数据迁移。该模式不是生产客户端架构。

## 2. 桌面端

- `src/`：React、TypeScript、Ant Design、React Flow
- `src-tauri/`：可编译的 Tauri 2 Rust 工程
- 生产 API 地址由 `VITE_API_URL` 注入；机构代码由 `VITE_ORGANIZATION_CODE` 预置
- Tauri 只负责窗口、单实例、日志、网络提示、Windows 凭据和签名更新
- 访问令牌与刷新令牌通过显式白名单命令存入 Windows Credential Manager
- WebView 中只保留非敏感身份摘要和界面状态，生产令牌会从旧 `localStorage` 自动迁移并清除
- CSP 在正式构建时仅放行 API、更新源及受控媒体域名
- NSIS 卸载钩子清理凭据、缓存和日志

浏览器形态只用于开发、E2E 和必要的内部运维，不作为公开学生 Web 端发布。

## 3. 机构租户

- `organizations` 保存机构代码、名称、状态和席位上限
- 所有业务表通过 `organization_id` 归属机构
- 请求通过 `X-CoderAI-Organization-Code` 选择机构，会话令牌同时绑定机构
- 数据库会话应用租户过滤；令牌机构与请求机构不一致时拒绝访问
- 用户名唯一约束为 `(organization_id, lower(username))`
- 对象键以 `organizations/{organization_id}/` 为强制前缀，读取和删除再次验证归属
- 当前桌面包预置试点机构代码，学生无需输入机构代码

首轮虽然只有一个试点机构，仍保留完整的机构边界，避免以后扩展时重新迁移所有业务表。

## 4. 身份与权限

- 学生账号只能由管理员单独创建或 CSV 导入，不开放自助注册
- 教师可查看未归档学生并执行分班、排课、批改和密码重置，但不能创建学生账号
- 管理员拥有全机构教学权限及系统管理权限
- `classroom_teachers` 控制班级授权，`course_package_teachers` 控制课程包预览和排课授权
- 学生、教师和管理员使用独立角色路由，后端逐接口执行角色与资源权限校验
- 归档学生不进入教师维护和新排课范围，历史班级教学记录只读
- 登录失败 5 次锁定 15 分钟；教师令牌支持刷新、轮换、注销和设备撤销
- 云端不会生成任何历史默认管理员账号密码；首个管理员由 CLI 创建
- 学生固定初始/重置密码 `bcm123456` 暂按内测风险接受项保留

## 5. 数据层

### PostgreSQL

- SQLAlchemy 2 定义模型
- Alembic 管理云端结构，入口为 `alembic.ini`
- `backend/migrations/versions/20260720_0001_cloud_schema.py` 是首个云端基线，`20260726_0002_student_course_projects.py` 增加学生工程包作品关联，`20260727_0003_workspace_answers.py` 增加问卷式工程包答案存储，`20260815_0004_submission_files_concurrency.py` 增加提交规则和并发唯一键，`20260823_0005_submission_attachments.py` 增加独立附件及版本快照，`20260823_0006_agent_generation.py` 增加 Agent、异步任务和视频任务关联
- 云端 `CODERAI_AUTO_CREATE_SCHEMA=false`，容器启动前执行 `alembic upgrade head`
- SQLite 幂等兼容迁移只服务本地开发和历史数据读取；提交迁移会先生成独立快照，再合并历史重复提交并重新编号版本
- `task_submissions` 以“机构 + 任务 + 学生”唯一，`submission_versions` 以“机构 + 提交 + 版本号”唯一；PostgreSQL 写入使用事务级 advisory lock 串行化同一学生对同一任务的并发提交

### 对象存储

- 课程封面、PPT 原件/PDF 预览、Markdown、作品、提交附件、Agent 临时文件、视频和审核文件存入 S3 兼容存储
- 数据库只保存 `object://bucket/key` 内部引用
- 前端继续使用原有鉴权文件接口，不接触本地路径、对象密钥或真实存储地址
- 上传记录 SHA-256；迁移、备份和恢复演练再次校验内容

### Redis 与 Celery

- PPTX 转 PDF、文字/图片/Agent 生成、工作流执行、视频轮询和每日数据保留扫描由 Celery 处理；本地开发使用 FastAPI `BackgroundTasks` 回退
- 任务携带机构 ID 和机构代码，并在 Worker 中恢复租户上下文
- 任务启用延迟确认、Worker 丢失重投、单任务预取和有限并发
- LibreOffice 仅安装在云端应用/Worker 镜像，不要求学生电脑安装

## 6. AI 服务与密钥

- 服务商、模型和能力路由按机构保存
- 云端 API Key 使用 AES-GCM 加密，`CODERAI_SECRET_KEY` 为当前服务器主密钥
- 轮换时临时提供 `CODERAI_SECRET_KEY_PREVIOUS`，执行 `rotate-secrets` 后移除旧密钥
- Windows DPAPI 密文不能在 Linux 解密，迁移命令清空并停用旧服务商，要求管理员重新录入
- 后端统一处理超时、限流、余额不足、审核拒绝和服务能力缺失
- 文字与 Agent 默认单次服务商超时为 120 秒，图片默认为 180 秒；客户端只提交短请求并轮询持久化任务状态
- 用量、服务商、能力、状态和班级快照写入机构内日志，不记录明文密钥或生成正文
- `local_openai_compatible` 可连接 API 容器能够访问的 Ollama、LM Studio 或私有 OpenAI 兼容推理地址，API Key 可为空；它不会在学生电脑或云端自动安装模型
- MiniMax 视频生成由内置受信任适配器处理任务提交、查询和结果保存，不再以 `planned` 占位
- 即梦通过火山方舟 Seedance 异步任务协议提交和查询，成品先写入机构对象存储，再通过鉴权接口预览
- `AgentConversation`、`AgentMessage` 和滚动摘要只在单个会话内形成记忆；模型显式选择受管理员白名单控制，工具建议必须由学生确认且再次经过课堂工具权限校验
- Agent 对话文字不进入作品库；临时图片和视频默认保留 7 天，学生确认“保存到我的作品”后才创建正式作品
- 可安装插件 v1 只运行签名后的声明式文字工具，不执行第三方 Python、JavaScript、EXE 或安装脚本；新模型和视频协议必须实现受信任后端适配器，详见 `docs/PLUGIN_PROTOCOL.md`

## 7. 课程与作品

- 新课程层级为“课程包 -> 课程 -> PPT、工程包.md、成果包.md”
- 三类资料均可为空；有至少一门课程即可发布课程包
- 教师可在预览抽屉中全屏播放转换后的 PDF 并退出全屏，但不能下载 PPTX/PDF；Markdown 可预览和下载
- 学生端只获得工程包；PPT/PDF 和成果包不返回学生课程响应，直连接口同样拒绝
- 工程包在线保存到学生在该课程下的唯一文字作品，源码与 GFM 预览同步显示，管理员原始 Markdown 保持只读，作品可复用现有提交与批改链路
- 管理员为每门课程配置允许提交的扩展名和 1 至 20 MB 单文件上限；学生可以选择本机文件，服务端流式写入临时文件并校验扩展名、文件头、UTF-8 文本和 ZIP 结构后转入受管对象存储
- 本机文件直接创建 `SubmissionAttachment` 并关联提交版本，不创建 `Project`、不进入学生或教师作品库；图片附件继续进入内容安全复核
- 提交版本固化标题、Markdown、文件引用和元数据、逾期状态、评分及反馈；历史页和鉴权文件接口不读取作品当前内容替代快照
- 普通项目响应不返回本地路径或对象存储键，学生通过鉴权项目文件接口查看或下载已保存文件
- 课程包授权与作者相互独立，作者可选教师或管理员
- 排课以课程为原子，支持学员和班级目标；已有提交保存评分规则快照
- 图片、视频和工作流结果通过统一作品与审核链路保存

## 8. 隐私与保留

- 隐私政策版本化，支持监护人授权和云端 AI 数据处理开关
- 联系方式与 AI API Key 使用同一云端密钥保护接口加密，但用途与数据表隔离
- 默认保留 365 天；扫描只生成预览或待审批请求，不直接删除
- 个人导出、删除和到期保留覆盖提交附件、Agent 会话、消息、异步任务和临时文件；共享作品文件仍按引用关系保护
- 管理员可设置例外、审批请求并输入精确确认文本执行
- 删除前对象进入隔离区；数据库失败时恢复对象，成功后再清除隔离副本
- 审计记录在到期处理时脱敏，而不是保留可识别个人信息

## 9. 备份、恢复与监控

- 生产 PostgreSQL 与对象存储每日备份到独立 S3 位置
- 保留 30 个日备份和 12 个月度备份
- 数据库 dump、每个对象和清单都进行 SHA-256 校验
- 恢复演练写入临时数据库并逐对象校验，不覆盖生产
- 机构管理员导出仅包含本机构脱敏业务数据，且不具备直接恢复能力
- `/api/health/live` 检查进程，`/api/health/ready` 检查数据库、Redis、对象存储和机构
- `/api/version` 返回版本、最低客户端版本、渠道、提交和部署模式
- `/metrics` 只在内部网络由 Prometheus 抓取，Caddy 对公网返回 404
- 每个 API worker 通过可配置并发闸门限制 GET/HEAD 请求，避免旧客户端突发读取耗尽数据库连接池
- 本机已覆盖 10 名不同学生同时写入和同一学生重复并发提交；云端可用人数仍需在真实 PostgreSQL、对象存储、网关和目标 ECS 规格下压测确认
- 桌面请求携带客户端版本；API 结构化日志记录版本，Caddy 访问日志删除授权、Cookie 和师生会话令牌
- Alertmanager 可通过 `CODERAI_ALERT_WEBHOOK_URL` 接入 HTTPS Webhook
- 无域名部署使用固定公网 IPv4 和 Let's Encrypt `shortlived` IP 证书；systemd 每 12 小时续期并让 Caddy 重载
- Caddy 在同一 HTTPS IP 的 `/desktop-updates/` 提供 Tauri 签名更新文件

## 10. 发布与回滚

- `main` 的 `1a3848a` 是云端改造前回滚基线
- 云端 API `0.2.0-beta.4` 和 Windows 客户端 `0.2.0-beta.6` 以提交 `f9db63b` 形成可审查发布快照
- GitHub Actions 执行 Ruff、依赖审计、后端测试、前端构建、Tauri 测试、E2E、容器集成和 Windows 冒烟构建
- 正式工作流从 GitHub Secrets 导入 Authenticode PFX 和 Tauri 更新签名私钥
- 回滚数据库前先停止写入并执行恢复演练；客户端问题通过从回滚提交构建更高补丁版本发布，避免签名更新降级问题

## 11. 主要目录

```text
backend/app/                 FastAPI、租户、存储、队列、隐私和服务层
backend/migrations/          Alembic 迁移
deploy/                      Docker、Caddy、监控、备份和恢复工具
src/                         React 前端
src-tauri/                   Tauri Rust 桌面工程
tests/                       API、租户、迁移和 E2E 测试
tools/                       构建、更新清单、PPT 和压测工具
.github/workflows/           CI 与 Windows 签名发布
docs/                        上线与运维文档
```
