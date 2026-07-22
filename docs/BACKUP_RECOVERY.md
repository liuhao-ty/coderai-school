# 备份与恢复演练

## 目标

- PostgreSQL 和对象文件每天备份到独立 S3 位置
- 保留最近 30 个日备份和 12 个月度备份
- 当月首次成功备份自动形成月备份，不依赖每月 1 日必须在线
- 每次备份校验数据库 dump 和每个对象的 SHA-256
- 上线前及之后每月执行一次隔离恢复演练

生产对象存储与备份存储的 endpoint + bucket 组合必须不同。备份位置不能与生产数据盘共享故障域。

## 自动备份

Compose 中的 `backup` 服务每 24 小时执行：

1. `pg_dump --format=custom`
2. 枚举生产对象并下载计算 SHA-256
3. 上传数据库、对象和 `manifest.json` 到日/月前缀
4. 清理过期日/月前缀
5. 写入 Node exporter textfile 指标

查看状态：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml logs --since 48h backup
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml exec node-exporter \
  cat /metrics/coderai_backup.prom
```

手工触发一次备份：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm backup
```

成功输出至少包含 `prefixes`、`database_sha256`、`object_count` 和 `bytes`。如果生产与备份存储相同，任务会直接失败。

## 隔离恢复演练

选择已存在的快照，例如 `daily/2026-07-20`：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm backup \
  python /app/deploy/restore_drill.py daily/2026-07-20 \
  --confirm RUN-ISOLATED-RESTORE-DRILL
```

演练会：

- 创建临时数据库 `coderai_restore_drill`
- 下载并校验数据库 SHA-256
- 使用 `pg_restore` 恢复临时数据库
- 检查应用表数量
- 下载并校验清单中的每个对象
- 无论成功失败都删除临时数据库

通过输出示例：

```json
{"status":"passed","snapshot":"daily/2026-07-20","tables":40,"objects":128}
```

每次演练记录日期、快照、表数、对象数、耗时和操作者，不记录数据库连接串或对象密钥。

## 灾难恢复准备

恢复生产前必须满足：

- 已停止 Caddy、API、Worker 和 Beat，确认没有写入
- 已保全当前损坏环境的磁盘、日志和数据库快照
- 选定快照已通过隔离恢复演练
- 已明确恢复点之后的数据损失范围
- 双人复核数据库 URL、生产 bucket 和备份前缀
- 已获得平台运维负责人批准

不要通过机构管理员界面执行平台恢复。云端界面只提供本机构脱敏导出。

## PostgreSQL 恢复

以下是运维步骤模板，必须先在隔离环境演练并替换实际变量。

1. 停止写入：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml stop caddy api worker beat backup
```

2. 从备份对象存储下载 `SNAPSHOT/database/coderai.dump` 和 `SNAPSHOT/manifest.json`。
3. 计算 dump SHA-256，与 manifest 完全一致。
4. 创建新的空 PostgreSQL 数据库，不覆盖原损坏库。
5. 恢复到新库：

```bash
pg_restore --no-owner --no-acl --exit-on-error --dbname "$NEW_DATABASE_URL" coderai.dump
```

6. 针对新库执行 Alembic 状态检查和 `/api/health/ready` 预检查。
7. 只有新库验证通过后，才切换 `CODERAI_DATABASE_URL`。

保留原数据库，直到恢复后的系统通过课堂主流程和完整对象校验。

## 对象存储恢复

快照对象位于：

```text
SNAPSHOT/objects/<原生产对象键>
```

恢复到全新的生产 bucket，不能先清空原 bucket。使用 S3/MinIO 工具将 `SNAPSHOT/objects/` 下内容复制到新 bucket 根目录，保持 `organizations/{id}/...` 键不变。

恢复后必须：

- 对象数量与 manifest 一致
- 每个对象大小一致
- 每个对象 SHA-256 一致
- bucket 禁止匿名访问
- API 使用机构鉴权接口可读取抽查文件
- 跨机构对象引用仍返回拒绝

验证通过后再切换 `CODERAI_S3_BUCKET`。旧 bucket 保持只读，直到恢复验收完成。

## 恢复验收

- `/api/health/live` 和 `/api/health/ready` 通过
- 机构、用户、班级、课程、排课、作品和提交数量符合 manifest
- 随机抽查至少 10 个文本/图片/视频/课程资料文件
- 管理员、教师和学生登录及权限隔离正常
- PPT PDF 可预览，Markdown 可下载
- 作品提交与批改形成新记录
- AI Key 可解密并完成真实调用；若主密钥丢失，必须重新录入 Key
- 备份服务对恢复后的新目标成功生成新快照

## 主密钥恢复

`CODERAI_SECRET_KEY` 不在数据库和备份包中，必须由独立密钥管理流程保管。恢复时：

- 有当前主密钥：正常注入后验证 AI 服务商
- 正在轮换：同时注入 `CODERAI_SECRET_KEY_PREVIOUS`，执行 `rotate-secrets`
- 主密钥丢失：数据库中的 API Key 无法恢复，清空后由机构管理员重新录入

不得将主密钥写入 Git、备份 manifest、工单或聊天记录。

## 演练频率

- 首次上线前：至少一次完整恢复演练
- 每月：至少一次日备份或月备份演练
- 数据库大版本、对象存储迁移、密钥轮换后：额外演练
- 连续两次演练失败：停止扩大灰度，直到恢复链路修复
