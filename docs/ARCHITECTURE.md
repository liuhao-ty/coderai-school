# 技术架构

## 运行结构

```text
React + TypeScript + Vite
          |
          | /api + 身份请求头
          v
FastAPI + SQLAlchemy
          |
          +-- SQLite: workspace_data/coderai.db
          +-- 文件: workspace_data/assets|projects|outputs|curriculum
          +-- 云端 AI Provider API
```

Tauri 2 配置位于 `src-tauri/`，但当前目录只有 `tauri.conf.json`，还没有 `Cargo.toml` 和 Rust 入口。开发阶段使用浏览器前端与本地 FastAPI 服务运行，桌面壳与 Python sidecar 托管属于 P2。

当前运行时不依赖 ComfyUI 或 `comfyui-skill`。界面由 React、Ant Design 和 CSS 渲染；未来若使用 ComfyUI，应通过模型适配器或工作流执行接口接入，不进入应用壳和布局依赖。

## 主要目录

```text
backend/app/       FastAPI 接口、认证、模型、服务商和业务逻辑
backend/app/privacy.py  隐私政策、监护人授权、个人数据导出与删除
backend/app/licensing.py  Ed25519 许可证验证、设备安装码与席位门禁
backend/app/plugins.py  签名插件安装、完整性验证、声明式运行时与权限门禁
backend/app/readiness.py  管理员内部验收接口；产品控制台无 P0 测试页面
backend/app/curriculum.py  课程资料校验、LibreOffice 发现与 PPTX 转 PDF
backend/app/school_stages.py  学龄枚举、旧值映射、课程包多选解析与展示标签
src/App.tsx        应用壳、身份切换和全局数据编排
src/features/      学生、教师、课程、作品、工作流、批改等领域模块
src/lib/           API 客户端、错误处理、格式化和纯领域函数
src/components/    跨领域共享界面组件
src/store/         Zustand 统一业务状态、加载状态和请求序列保护
src-tauri/         Windows 桌面壳配置
tests/             使用临时数据库的后端冒烟测试
workspace_data/    正式数据库、作品、素材、验收证据、缓存、日志和插件
docs/              项目状态与架构说明
```

## 身份边界

- `users` 同时承载学生、教师和管理员账号；用户名全局唯一，密码只保存 PBKDF2 随机盐强哈希。学生是机构全局账号，只能由管理员单独创建或 CSV 批量导入，不绑定创建教师。
- `users.age_level` 保存 `primary_lower`、`primary_upper`、`secondary`，分别对应小学低龄、小学高龄、初中高中。学生 AI 请求以已登录账号值为准，不信任前端传入的学龄。
- 学生自行注册永久关闭，`POST /api/auth/student-register` 固定返回 `410 STUDENT_SELF_REGISTRATION_DISABLED`。学生使用管理员分配的用户名和密码登录，请求使用持久化随机 `X-CoderAI-Student-Token`，数据库只保存令牌摘要。
- 教师和管理员请求使用 `X-CoderAI-Teacher-Token`。访问令牌有效 2 小时，配套刷新会话有效 7 天；会话绑定具体职员账号、角色和设备并支持撤销。
- 管理员可以创建、导入、编辑、停用、归档、恢复和导出学生账号，并维护教师账号与角色。教师可以读取全部学生的教学必要字段、为学生分班或批量转班、将密码重置为 `bcm123456`，但不能创建或维护学生账号资料。
- `classroom_teachers` 记录班级与授课教师的多对多授权、分配人和分配时间。教师创建班级时自动绑定自己，可为本人获授权班级增删共同授课教师，但不能移除自己；管理员可以管理全部授权并允许班级暂不分配教师。
- 有班级的课程、任务、素材、作品、提交、工作流、视频、用量和审核记录按班级授权过滤。教师被移除后立即失去该班级当前及历史数据访问权；学生转班只更新当前班级，不改写历史记录的班级快照。
- 无班级教学资源继续按 `owner_teacher_id` 创建者归属过滤；`owner_teacher_id` 同时保留为创建审计字段，不再代表有班级资源的唯一访问边界。学生只能读取和修改自己的作品、提交和运行记录。
- 管理员拥有全机构教学数据访问和教学管理能力，同时负责全局模型服务、账号、隐私政策、运维备份、扩展和许可证。`/admin/*` 中系统总览与教学总览独立展示。
- `/admin/accounts` 是管理员唯一学生开户入口，按“教师与管理员”“学生开户”“修改登录密码”拆分；`/admin/students` 只负责学生搜索、导出、资料、班级、密码、启停和归档恢复。两个路由继续保持原地址兼容，不改变后端权限。
- `/admin/security` 只显示“登录设备”和“操作审计”；管理员改密迁入 `/admin/accounts`。教师 `/teacher/account` 继续显示本人改密，并复用相同的设备与审计子页。
- 学生密码重置固定为 `bcm123456`，撤销旧会话且不强制首次修改；管理员生成的职员临时密码仍强制首次修改。教师自行设置密码只要求 8 位，弱密码可用但每次凭据登录提示；管理员继续执行 10 位及字母、数字、特殊字符规则。
- 账号停用、学生归档和隐私删除都会撤销该账号全部旧会话。
- `auth_login_attempts` 持久化教师和学生登录失败窗口及锁定时间，服务重启不会清除暴力尝试状态。
- `teacher_audit_logs` 保存高风险教师操作的操作者账号、姓名、用户 ID、会话、目标、脱敏摘要和北京时间，并由受保护接口只读查询。
- 隐私政策可强制监护人授权和关闭云端 AI；文字、图片、视频和工作流入口统一执行当前政策。
- 自定义插件必须是 Ed25519 签名 ZIP 包；安装和每次运行都会重新验证发布者、公钥签名、应用兼容版本、文件清单与 SHA-256。声明式 v1 只开放 `ai.text` 和 `projects.write`，不执行插件代码，也不开放任意网络、文件、进程、数据库或环境变量访问。
- 插件包上限为 5 MB、解压后上限为 20 MB；旧版 JSON 课程包兼容接口上限为 2 MB。新课程包使用包含清单和真实资料的 ZIP，上传上限为 200 MB、解压后上限为 600 MB；导入层拒绝路径穿越、符号链接、重复或额外文件、危险内容、异常字段和压缩炸弹，受管素材继续执行文件签名与 SHA-256 校验。
- 旧课程、旧任务和共享素材可设为无班级私有资源或指定班级资源；前者按创建者过滤，后者按 `classroom_teachers` 授权共享。新课程包由管理员全局维护，`course_package_teachers` 以课程包为粒度执行严格教师白名单；教师必须同时具备课程包权限和相应的个人创建者或班级权限，才能创建或管理排课。
- 教学接口通过职员身份、班级授权和资源快照联合校验；学生账号维护、模型、职员账号、系统设置、隐私政策、运维、备份、插件、许可证和内部验收接口通过 `require_admin` 保护。
- 前端使用 HashRouter 区分 `/student/*`、`/teacher/*` 和 `/admin/*`；本地持久化只用于恢复界面身份，后端仍对每个请求强制校验令牌、角色和数据归属。
- 工作台将 `body`、`.shell` 和 `.workspaceMain` 固定在 `100vh`：侧栏和顶部栏不参与主页面滚动，侧栏菜单可独立滚动且底部隐私/退出操作固定，`.content` 是唯一主纵向滚动容器。路由切换会重置内容滚动位置并聚焦页面标题。
- 顶部栏只显示当前登录身份，不显示时间或 AI 状态；模型可用/待配置标签只由管理员“模型服务”页面头部渲染，系统总览仍保留模型统计。
- 课程内容写权限只属于管理员。现有、新建和 ZIP 导入课程包默认不授权教师；管理员可在已发布课程包上分配多名启用教师。教师只读取本人获授权的已发布课程包，并可按全机构未归档学生或本人获授权班级排课；教师不能修改课程规则、资料或独立创建旧课堂任务。
- 课程包使用 `school_stages_json` 保存一到三个“适用学龄”，新建默认全选。该值当前是备课与排课参考元数据，不追溯阻断已有排课或历史学习权限。
- 课程包作者通过 `author_user_id` 关联一个教师或管理员账号，创建和换人时只允许选择启用账号。旧自由文本作者会幂等回填为原创建者或管理员；ZIP 导入以执行导入的管理员为作者。作者署名不自动赋予 `course_package_teachers` 课程查看或排课权限。
- 撤销课程包权限不会取消已有排课或阻断学生学习、提交和反馈。原教师仍可按班级或个人创建者范围读取必要历史字段，但不能访问课程正文、资料、调整/取消排课或继续批改；个人排课由管理员接管，班级排课可由同时具备班级与课程包权限的教师接管。
- 教师学生列表排除归档账号。归档学生的历史作品与提交只在原授权班级中只读可见，评分、反馈、作品生命周期、安全记录和用量记录不再开放修改或浏览。

## 数据边界

- SQLite 保存用户、课程、任务、作品和调用记录等元数据。
- 商业许可证采用 `CODERAI-LIC1` Ed25519 签名格式，信任公钥固定在应用内；设备安装码由 Windows `MachineGuid` 不可逆哈希生成，社区版默认提供 30 个在读学生席位。
- 许可证失效后禁止安装、启用和运行商业插件，但管理员始终可以停用、卸载插件和撤销发布者信任，避免过期状态阻止安全清理。
- AI 服务商由管理员全局维护；密钥使用当前 Windows 用户的 DPAPI 加密，数据库只保存 `dpapi:` 密文，启动时自动迁移旧明文记录。
- 图片、视频、文档等真实文件保存在 `workspace_data/` 子目录。
- `course_packages`、`curriculum_courses`、`course_materials` 和 `course_schedules` 保存新课程层级、三个可选资料槽位和排课记录；`course_packages.school_stages_json` 保存结构化适用学龄，`age_range` 仅保留为旧包导入导出兼容快照；`author_user_id` 关联署名教师/管理员，`course_package_teachers` 保存课程包、教师、分配管理员和北京时间的联合主键授权；旧 `courses`、`lessons`、`tasks` 保留为历史兼容数据。
- PPTX 原件及 LibreOffice 生成的 PDF 位于 `workspace_data/curriculum/{package_id}/{course_id}/`。PDF 预览接口使用不带下载文件名的受鉴权二进制响应，前端在内存中恢复 `application/pdf` Blob；教师和学生均不能调用 PPTX/PDF 下载接口。
- 单门课程是排课原子，按课程包批排会生成显式的多条 `course_schedules`。排课同步生成兼容提交系统的任务记录；课程规则更新会同步未归档排课任务，提交时保存评分规则快照和逾期状态。
- 图片作品预览按 `file_status` 分流：本地受管文件通过鉴权的 `/api/projects/{id}/file` 读取为 Blob，并在切换作品或关闭抽屉时释放对象 URL；远程 HTTPS 图片直接作为预览源，但界面不渲染、复制或输出原始 URL 文本。下载按钮对本地文件使用 Blob，对远程文件使用临时隐藏链接，浏览器不支持跨域下载时回退为新窗口打开。
- 删除数据库记录时只自动删除软件素材库内的上传文件，不删除教师登记的外部路径文件。
- 数据库升级由 `backend/app/db.py` 中的幂等兼容迁移完成，结构迁移前先在 `workspace_data/migration-backups/` 创建 SQLite 与课程素材备份。学龄、课程包作者和教师授权迁移分别使用 `coderai-before-school-stages.db`、`coderai-before-course-package-authors.db` 和 `coderai-before-course-package-teachers.db` 快照，重复启动不重复备份或产生重复关系；`junior/senior` 只分别映射到小学低龄/小学高龄，不会自动产生初中高中归类。
- 旧学生缺少有效用户名或密码时会被停用、归档并清除旧邀请码，但保留其历史作品和提交；已有完整账号继续保留。`usage_logs` 与 `moderation_logs` 增加 `classroom_id` 快照，旧记录能从作品推断时回填，否则维持创建者可见。
- 班级存在学生或教学历史时禁止删除，避免历史权限、作品和提交失去班级归属。
- 完整备份保留账号密码哈希以维持可登录账号，但清空教师和学生会话；隐私导出明确排除 `password_hash`、令牌摘要和其他认证秘密。
- `privacy_policies` 保存版本化政策，`guardian_consents` 保存授权与撤回历史；监护人联系方式使用当前 Windows 用户的 DPAPI 加密。
- 学生个人数据可按身份隔离导出；教师删除学生前必须完成快照预检和二次确认，数据库失败时回滚已移动的受管文件。
- `retention_days` 当前是政策声明字段，自动到期扫描和审批清理仍是补充治理任务。
- 视频任务使用 `submitted`、`processing`、`success`、`failed`、`timed_out`、`download_failed`、`expired` 和 `canceled` 状态；同记录最多重试 3 次，成功结果必须落到本地文件后才标记为可用作品文件。
- `provider_acceptance_runs` 只保存服务商、能力、模型、耗时、标准化错误场景、失败来源、上游 HTTP 状态码和脱敏结果摘要，不保存 API Key、上游响应正文、提示词正文或生成正文；公开接口仅返回白名单诊断字段。
- AI 调用失败的 `usage_logs` 保存标准错误码与脱敏摘要，不再写入上游 HTTP 响应正文或原始网络异常文本。
- P0 失败证据保存在 `workspace_data/acceptance/evidence/`，数据库只记录受控相对路径、原文件名、大小和 SHA-256；下载要求教师会话并在返回前重新校验路径与哈希。
- 证据只接受 PNG、JPEG、PDF、JSON 和 TXT，最大 5 MB；文本证据自动检测疑似密钥与访问令牌，图片和 PDF 仍要求教师上传前人工脱敏。
- P0 脱敏报告不包含证据文件、受控路径、API Key、提示词或生成正文；`acceptance/` 与其他受管文件一起进入完整备份。
- `app_settings.p0_provider_acceptance_scope` 保存教师确认的本期服务商能力范围；首版范围必须至少覆盖文字和图片，视频为选配，范围更新进入教师审计。
- `app_settings.p0_failure_evidence_policy` 保存历史失败证据链策略、豁免原因、确认时间和管理员会话；豁免不改变 `failure_scenarios_complete`，报告继续显示未验证场景。
- 学生日常生成按有序能力路由执行自动故障切换；P0 真实测试可用 `provider_id` 锁定具体配置并禁用切换，保证成功或失败记录准确归属到实际被测服务。
- 服务商 P0 只有在范围已确认、范围内全部能力有软件真实成功调用，并且范围内每个服务商都具备网络、超时、限流、额度不足、服务不可用和内容拦截证据时才完成。
- 课堂验收确认保存在 `app_settings`，并绑定当前学生、课程、任务、批改、隐私政策和监护人授权证据的 SHA-256 快照；相关数据变化会使旧确认自动失效。

## 开发命令

```powershell
npm.cmd run dev       # 同时启动 API 和前端
npm.cmd test          # 独立数据库后端测试
npm.cmd run build     # 前端生产构建
npm.cmd run test:ppt-conversion # 隔离环境真实 PPTX 转 PDF
npm.cmd run check     # 测试并构建
npm.cmd run check:full # 测试、构建并运行 Chrome E2E
```

## 当前接口分组

- `/api/auth/*`：教师和学生登录、改密、刷新及注销；学生自注册端点仅返回关闭状态
- `/api/accounts/*`：管理员维护教师账号与角色
- `/api/students/*`：教师读取、分班和重置密码；管理员额外创建、导入导出、编辑、启停及归档恢复
- `/api/classrooms/*`：班级管理、全局班级选项与授课教师多对多授权
- `/api/teachers/options`：教师和管理员读取可参与授课的启用教师选项
- `/api/course-authors/options`：管理员读取可用于课程包署名的教师和管理员账号选项
- `/api/text|image|video/*`：AI 生成能力
- `/api/workflows/*`：模板、运行和历史
- `/api/projects/*`：学生作品
- `/api/course-packages/*`：管理员课程包、包内课程、发布、归档、排序、教师白名单及 ZIP 导入导出；教师列表和详情按白名单过滤
- `/api/curriculum-courses/*`：课程规则、可选资料槽位、Markdown/PDF 预览和受控下载
- `/api/course-schedules/*`：按学员/班级排课、批排、时间调整、取消和排课提交
- `/api/courses|lessons|classes/tasks/*`：旧课程、课时和课堂任务历史兼容；教师不再通过这些接口创建独立任务
- `/api/assets/*`：课堂素材
- `/api/settings/*`：管理员维护服务商、模型目录、能力路由与系统设置
- `/api/moderation/*`：教师查看获授权班级审核数据；全局审核规则只允许管理员修改
- `/api/system/*`：管理员执行备份恢复、运维和许可证管理
- `/api/plugins/*`：管理员维护发布者、签名插件和声明式工具
- `/api/readiness/*`：管理员内部验收接口，产品导航不暴露程序测试页面
- `/api/privacy/*`：管理员维护全局政策；教师仅处理获授权班级学生的数据治理操作
