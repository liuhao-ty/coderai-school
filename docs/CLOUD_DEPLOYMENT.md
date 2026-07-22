# 单机构云端部署手册

适用版本：`0.2.0-beta.1`
目标：境内 Linux x64、单机构、最多 50 人同时在线

## 1. 上线前条件

- 4 核、8 GB 内存、100 GB 独立数据盘的 Linux 主机
- Docker Engine 和 Docker Compose v2
- 已解析到主机的正式域名，80/443 端口可用
- 与生产主机/MinIO 独立的 S3 兼容备份位置
- 私有 Git 仓库和固定的发布提交
- 正式机构代码，建议只使用小写字母、数字和连字符
- 已完成或已确认域名实名认证、ICP备案及其他适用备案责任

生产主机建议目录：

```text
/srv/coderai/app                 私有仓库工作副本
/srv/coderai/config/deploy.env   生产环境变量，权限 600
/srv/coderai/migration-source    本地数据的迁移副本
```

## 2. 配置

```bash
cd /srv/coderai/app
cp deploy/.env.example /srv/coderai/config/deploy.env
chmod 600 /srv/coderai/config/deploy.env
python -m backend.app.cli generate-secret-key
```

将生成值写入 `CODERAI_SECRET_KEY`，并逐项替换 `deploy.env` 中所有 `replace-*` 和 `example.*` 值。

必须检查：

- `CODERAI_DOMAIN` 是正式 API 域名，不含协议前缀
- PostgreSQL、MinIO 和备份密码均为独立随机强密码
- `CODERAI_DATABASE_URL` 中的密码与 `POSTGRES_PASSWORD` 一致并正确 URL 编码
- `CODERAI_S3_*` 指向生产 MinIO
- `CODERAI_BACKUP_S3_*` 指向独立存储，不能与生产 endpoint + bucket 相同
- `CODERAI_SECRET_KEY` 是 32 字节 URL-safe Base64
- `CODERAI_CORS_ORIGINS` 不含 `*`
- `CODERAI_ALERT_WEBHOOK_URL` 使用 HTTPS；留空时不会向外发送告警
- `CODERAI_BUILD_COMMIT` 填写发布提交 SHA

检查 Compose 展开结果，确认没有空变量或占位值：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml config > /tmp/coderai-compose.yml
grep -E 'replace-|example\.|<[^>]+>' /tmp/coderai-compose.yml && exit 1 || true
```

## 3. 构建基础服务

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml build migrate
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d postgres redis minio
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm minio-init
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm migrate
```

此时只初始化 PostgreSQL、Redis、MinIO 和 Alembic 结构，不启动对外 API。

## 4. 数据初始化

### 全新机构

不要在命令行参数中直接写密码。使用临时环境变量创建唯一管理员：

```bash
read -rsp 'Initial admin password: ' CODERAI_BOOTSTRAP_PASSWORD; echo
export CODERAI_BOOTSTRAP_PASSWORD
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm \
  -e CODERAI_BOOTSTRAP_PASSWORD migrate \
  python -m backend.app.cli bootstrap-admin \
  --organization-code coderai-pilot \
  --organization-name 'CoderAI 试点机构' \
  --seat-limit 50 \
  --username pilot.admin \
  --name '试点机构管理员'
unset CODERAI_BOOTSTRAP_PASSWORD
```

### 从当前 SQLite 迁移

先按 [迁移与回滚手册](CLOUD_MIGRATION.md) 创建可写迁移副本，再运行迁移命令。迁移完成后，在 API 启动前使用 `bootstrap-admin --replace-password` 将旧管理员密码替换为新的强密码。

## 5. 启动

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml ps
```

检查内部和外部健康状态：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml exec api \
  curl -fsS http://127.0.0.1:8000/api/health/live
curl -fsS -H 'X-CoderAI-Organization-Code: coderai-pilot' https://api.example.cn/api/health/ready
curl -fsS https://api.example.cn/api/version
```

验收要求：

- `live` 返回 `ok`
- `ready` 返回 HTTP 200 且数据库、队列和对象存储全部 `ready`
- `version` 为 `0.2.0-beta.1`，`deployment_mode` 为 `cloud`
- 公网访问 `https://api.example.cn/metrics` 返回 404
- Caddy 自动取得有效证书且没有循环重定向

## 6. 首次管理员操作

1. 登录管理员账号并确认机构名称。
2. 在“模型服务”重新录入机构 AI Key；不要尝试导入 DPAPI 密文。
3. 逐服务商执行文字、图片和已开放视频的真实连通测试。
4. 发布新的隐私政策，明确云端 AI、第三方服务商、保留期限和删除方式。
5. 录入或核对监护人授权。
6. 检查课程包教师白名单、班级授权和学生状态。
7. 导出一次机构数据，确认包中不含密码、会话和 API Key。

## 7. 监控与告警

Prometheus 和 Alertmanager 默认只在 Compose 内部网络，不映射公网端口。通过 SSH 隧道或受控内网查看，不要直接暴露。

必须确认以下告警有真实接收端：

- API 不可用
- 5xx 错误率超过 1%
- 领域任务积压
- 数据盘剩余空间低于 15%
- 独立备份超过 36 小时未成功
- AI 失败/拦截短时突增

如果 `CODERAI_ALERT_WEBHOOK_URL` 为空，Alertmanager 会保留告警但不发送到外部。扩大灰度前必须配置并触发一次测试告警。

## 8. 压测

在测试机构创建 50 个专用账号，实际账号文件保存为 `tools/pilot-load-accounts.json`，不得提交 Git。

只读主流程：

```powershell
npm.cmd run test:load -- `
  --base-url https://api.example.cn `
  --accounts tools/pilot-load-accounts.json `
  --concurrency 50 `
  --rounds 3 `
  --report test-results/pilot-load-read.json
```

提交与批改流程需要在账号文件中为学生提供 `schedule_id`、为教师提供 `review_submission_id`，并显式开启写操作：

```powershell
npm.cmd run test:load -- `
  --base-url https://api.example.cn `
  --accounts tools/pilot-load-accounts.json `
  --concurrency 50 `
  --write-actions `
  --report test-results/pilot-load-write.json
```

门槛：非 AI 请求错误率低于 1%，总体 P95 低于 1000 ms。压测作品和批改必须使用测试课程，并在验收后按审批流程清理。

## 9. 发布顺序

1. 测试环境迁移和内部管理员验收。
2. 5 至 10 人灰度，稳定 3 个教学日。
3. 单班课堂，稳定 3 个教学日。
4. 不超过 50 人的全机构内测。

出现跨机构数据风险、数据库损坏、备份无法恢复或桌面更新失败时，立即停止扩容并执行 [回滚流程](CLOUD_MIGRATION.md#回滚)。

## 10. 日常命令

```bash
# 状态
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml ps

# 日志
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml logs --since 30m api worker caddy

# 应用更新（先备份并在测试环境验证）
git fetch --tags
git checkout <approved-release-tag>
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml build migrate
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm migrate
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d

# 停止应用写入，保留数据库和对象存储
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml stop caddy api worker beat
```

任何删除卷、恢复数据库或替换对象前，都必须先完成恢复演练并得到明确审批。不要使用 `docker compose down -v`。
