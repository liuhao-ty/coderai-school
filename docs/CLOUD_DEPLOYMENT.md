# 单机构公网 IP 云端部署手册

适用版本：`0.2.0-beta.5`
目标：境内 Linux x64、单机构、最多 50 人同时在线

## 1. 上线前条件

- 4 核、8 GB 内存、100 GB 独立数据盘的 Linux 主机
- Docker Engine 和 Docker Compose v2
- 固定公网 IPv4（建议绑定 EIP），80/443 端口可用
- 与生产主机/MinIO 独立的 S3 兼容备份位置；仅封闭内测可显式禁用并接受风险
- 私有 Git 仓库和固定的发布提交
- 正式机构代码，建议只使用小写字母、数字和连字符
- 可接收证书通知的运维邮箱
- 已向云厂商或合规顾问确认公网 IP 直连模式下仍适用的网络服务、未成年人和生成式 AI 责任

本方案不使用域名。公网入口使用 Let's Encrypt `shortlived` IP 地址证书，证书有效期约 160 小时，由 systemd 每 12 小时检查和续期。80 端口必须持续可从公网访问，以完成 HTTP-01 验证。

参考：

- <https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/>
- <https://letsencrypt.org/2026/03/11/shorter-certs-certbot/>

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
install -d -m 0755 /srv/coderai/acme /srv/coderai/updates
install -d -o 10001 -g 10001 -m 0755 /srv/coderai/metrics
python -m backend.app.cli generate-secret-key
```

将生成值写入 `CODERAI_SECRET_KEY`，并逐项替换 `deploy.env` 中所有 `replace-*`、`example.*` 和示例 IP 值。

必须检查：

- `CODERAI_PUBLIC_IP` 是 ECS 固定公网 IPv4，不含协议或端口
- `CODERAI_CERTBOT_EMAIL` 是有效运维邮箱
- PostgreSQL、MinIO 和备份密码均为独立随机强密码
- `CODERAI_DATABASE_URL` 中的密码与 `POSTGRES_PASSWORD` 一致并正确 URL 编码
- `CODERAI_S3_*` 指向生产 MinIO
- `CODERAI_BACKUP_ENABLED=true` 时，`CODERAI_BACKUP_S3_*` 必须指向独立存储，不能与生产 endpoint + bucket 相同
- 暂无独立存储的封闭内测可设置 `CODERAI_BACKUP_ENABLED=false` 并留空 `CODERAI_BACKUP_S3_*`；此状态会持续产生备份禁用告警，不得宣称为生产安全环境
- `CODERAI_SECRET_KEY` 是 32 字节 URL-safe Base64
- `CODERAI_CORS_ORIGINS` 不含 `*`
- `CODERAI_ALERT_WEBHOOK_URL` 使用 HTTPS；留空时不会向外发送告警
- `CODERAI_BUILD_COMMIT` 填写发布提交 SHA
- `CODERAI_APT_MIRROR` 仅用于服务器构建时替换 Debian 镜像主机；网络正常时留空，阿里云境内构建可设为 `https://mirrors.aliyun.com`
- `CODERAI_PIP_INDEX_URL` 仅用于服务器构建 Python 依赖；网络正常时留空，阿里云境内构建可设为 `https://mirrors.aliyun.com/pypi/simple/`
- `CODERAI_MAX_CONCURRENT_READS_PER_WORKER` 按单 worker 数据库容量设置；当前低配试点使用 `6`
- `CODERAI_MINIMUM_CLIENT_VERSION` 使用已发布且可更新的最低客户端版本，禁止填写尚未上传的版本

检查 Compose 展开结果，确认没有空变量或占位值：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml config > /tmp/coderai-compose.yml
grep -E 'replace-|example\.|203\.0\.113\.10|<[^>]+>' /tmp/coderai-compose.yml && exit 1 || true
```

当 `CODERAI_BACKUP_ENABLED=false` 时，上述占位值检查不应包含已留空的 `CODERAI_BACKUP_S3_*`。上线独立存储后必须改回 `true`、手工执行首次备份并完成恢复演练。

## 3. 首次签发公网 IP 证书

证书签发前确保 Caddy 尚未启动且 80 端口没有其他进程监听：

```bash
cd /srv/coderai/app
chmod +x deploy/bootstrap-ip-certificate.sh deploy/renew-ip-certificate.sh deploy/install-ip-certificate-timer.sh
./deploy/bootstrap-ip-certificate.sh /srv/coderai/config/deploy.env
test -s /etc/letsencrypt/live/coderai-ip/fullchain.pem
test -s /etc/letsencrypt/live/coderai-ip/privkey.pem
```

脚本固定使用支持 IP 证书的 Certbot `5.4.0`，请求 `shortlived` profile。签发失败时不要改用 HTTP 或关闭客户端证书验证，应先检查安全组、80 端口和公网 IP 是否一致。

## 4. 构建基础服务

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml build migrate
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d postgres redis minio
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm minio-init
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm migrate
```

此时只初始化 PostgreSQL、Redis、MinIO 和 Alembic 结构，不启动对外 API。

## 5. 数据初始化

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

## 6. 启动

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml ps
./deploy/install-ip-certificate-timer.sh
systemctl start coderai-cert-renew.service
systemctl status coderai-cert-renew.timer --no-pager
```

检查内部和外部健康状态：

```bash
set -a
. /srv/coderai/config/deploy.env
set +a
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml exec api \
  curl -fsS http://127.0.0.1:8000/api/health/live
curl -fsS -H 'X-CoderAI-Organization-Code: coderai-pilot' "https://$CODERAI_PUBLIC_IP/api/health/ready"
curl -fsS "https://$CODERAI_PUBLIC_IP/api/version"
curl -fsS "https://$CODERAI_PUBLIC_IP/desktop-updates/latest.json" || true
```

验收要求：

- `live` 返回 `ok`
- `ready` 返回 HTTP 200 且数据库、队列和对象存储全部 `ready`
- `version` 为 `0.2.0-beta.5`，`deployment_mode` 为 `cloud`
- 公网访问 `https://<公网IP>/metrics` 返回 404
- TLS 证书 SAN 包含当前公网 IP，系统和 WebView 均能建立受信任连接
- `coderai-cert-renew.timer` 已启用，且 `coderai_tls_certificate_valid_beyond_48h` 为 `1`
- `/desktop-updates/` 只提供签名更新文件，不暴露目录外内容

## 7. 首次管理员操作

1. 登录管理员账号并确认机构名称。
2. 在“模型服务”重新录入机构 AI Key；不要尝试导入 DPAPI 密文。
3. 逐服务商执行文字、图片和已开放视频的真实连通测试。
4. 发布新的隐私政策，明确云端 AI、第三方服务商、保留期限和删除方式。
5. 录入或核对监护人授权。
6. 检查课程包教师白名单、班级授权和学生状态。
7. 导出一次机构数据，确认包中不含密码、会话和 API Key。

## 8. 监控与告警

Prometheus 和 Alertmanager 默认只在 Compose 内部网络，不映射公网端口。通过 SSH 隧道或受控内网查看，不要直接暴露。

必须确认以下告警有真实接收端：

- API 不可用
- 5xx 错误率超过 1%
- 领域任务积压
- 数据盘剩余空间低于 15%
- 独立备份超过 36 小时未成功
- 公网 IP 证书剩余有效期不足 48 小时
- AI 失败/拦截短时突增

如果 `CODERAI_ALERT_WEBHOOK_URL` 为空，Alertmanager 会保留告警但不发送到外部。扩大灰度前必须配置并触发一次测试告警。

## 9. 压测

在测试机构创建 50 个专用账号，实际账号文件保存为 `tools/pilot-load-accounts.json`，不得提交 Git。

只读主流程：

```powershell
npm.cmd run test:load -- `
  --base-url https://203.0.113.10 `
  --accounts tools/pilot-load-accounts.json `
  --concurrency 50 `
  --rounds 3 `
  --report test-results/pilot-load-read.json
```

提交与批改流程需要在账号文件中为学生提供 `schedule_id`、为教师提供 `review_submission_id`，并显式开启写操作：

```powershell
npm.cmd run test:load -- `
  --base-url https://203.0.113.10 `
  --accounts tools/pilot-load-accounts.json `
  --concurrency 50 `
  --write-actions `
  --report test-results/pilot-load-write.json
```

门槛：非 AI 请求错误率低于 1%，总体 P95 低于 1000 ms。压测作品和批改必须使用测试课程，并在验收后按审批流程清理。

## 10. 桌面更新文件

生产客户端使用以下地址：

```text
API URL:          https://<公网IP>
Updater endpoint: https://<公网IP>/desktop-updates/latest.json
Asset base URL:   https://<公网IP>/desktop-updates
```

将签名后的 `latest.json`、NSIS 更新安装包（当前 Tauri 2 为 `-setup.exe`，兼容旧版 `.nsis.zip`）和 `.sig` 上传到 `/srv/coderai/updates`。Caddy 会移除 `/desktop-updates/` 前缀后读取该目录，`latest.json` 禁止缓存。

## 11. 发布顺序

1. 测试环境迁移和内部管理员验收。
2. 5 至 10 人灰度，稳定 3 个教学日。
3. 单班课堂，稳定 3 个教学日。
4. 不超过 50 人的全机构内测。

出现跨机构数据风险、数据库损坏、备份无法恢复或桌面更新失败时，立即停止扩容并执行 [回滚流程](CLOUD_MIGRATION.md#回滚)。

## 12. 日常命令

```bash
# 状态
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml ps

# 日志
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml logs --since 30m api worker caddy

# IP 证书续期状态
systemctl status coderai-cert-renew.timer --no-pager
journalctl -u coderai-cert-renew.service --since '2 days ago' --no-pager

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
