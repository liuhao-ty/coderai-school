# SQLite 到云端迁移与回滚

本流程只用于首次单机构迁移。它不会建立 SQLite 与 PostgreSQL 的双向同步。

## 原则

- 正式迁移只对冻结后的数据执行一次
- 永远从完整副本迁移，不直接操作唯一的正式本地目录
- 目标 PostgreSQL 必须没有其他机构业务数据
- 数据库数量与每个真实文件的 SHA-256 必须一致
- Windows DPAPI AI Key 不迁移
- 迁移后本地数据只读保留 30 天
- 云端失败时整体回退到本地，不回写或合并两边数据

## 迁移前准备

1. 停止本地 API 与 Web 服务。
2. 确认没有 `python`/Uvicorn 进程继续写入 SQLite。
3. 执行 `PRAGMA integrity_check` 并记录结果。
4. 复制 `workspace_data` 到独立迁移目录。
5. 对原始 SQLite 和文件目录再做一份离线备份。
6. 在测试云环境先完整演练一次。

当前云改造前备份位于：

```text
E:\CoderAI学堂-backups\pre-cloud-20260720-192149
```

正式迁移时仍应重新创建冻结时点备份，不能直接把 2026-07-20 的备份当作最新课堂数据。

将迁移副本传到 Linux：

```text
/srv/coderai/migration-source/workspace_data/coderai.db
/srv/coderai/migration-source/workspace_data/...
```

容器内用户 UID 为 `10001`，迁移目录必须可写，因为命令会创建源备份、清单、锁和只读标记：

```bash
chown -R 10001:10001 /srv/coderai/migration-source
chmod -R u+rwX,go-rwx /srv/coderai/migration-source
```

## 迁移演练

先启动 PostgreSQL、Redis 和 MinIO，不启动 API：

```bash
cd /srv/coderai/app
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml up -d postgres redis minio
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm minio-init
```

执行 dry-run：

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm \
  -v /srv/coderai/migration-source:/migration-source migrate \
  python -m backend.app.cli migrate-cloud \
  --sqlite /migration-source/workspace_data/coderai.db \
  --data-dir /migration-source/workspace_data \
  --organization-code coderai-pilot \
  --organization-name 'CoderAI 试点机构' \
  --seat-limit 50 \
  --manifest /migration-source/migration-output/dry-run.json \
  --retain-until '迁移完成后30天' \
  --dry-run
```

dry-run 会验证源数据库、路径归属、表结构和目标环境，但不会插入业务行或上传文件。它可能创建空机构记录，正式执行可继续复用。

## 正式迁移

目标机构有任何业务数据时命令会停止。只有确认这是同一次失败重试且旧数据可覆盖时才能使用 `--replace`；不要对含其他机构的数据库使用。

```bash
docker compose --env-file /srv/coderai/config/deploy.env -f deploy/docker-compose.yml run --rm \
  -v /srv/coderai/migration-source:/migration-source migrate \
  python -m backend.app.cli migrate-cloud \
  --sqlite /migration-source/workspace_data/coderai.db \
  --data-dir /migration-source/workspace_data \
  --organization-code coderai-pilot \
  --organization-name 'CoderAI 试点机构' \
  --seat-limit 50 \
  --manifest /migration-source/migration-output/cloud-migration-manifest.json \
  --retain-until '迁移完成后30天'
```

命令按以下顺序执行：

1. 在迁移目录创建 SQLite 在线备份和文件 SHA-256 清单。
2. 执行 Alembic 升级并创建/确认机构。
3. 检查目标业务库为空。
4. 上传真实文件到机构对象前缀并逐个回读校验。
5. 在单个数据库事务中插入机构数据。
6. 校验每张表数量。
7. 清空并停用 AI Key，标记为需要重新录入。
8. 写入迁移清单和 `.cloud-migrated-readonly`。

出现异常时，数据库事务回滚，已上传对象按清单逆序删除，失败信息写入 manifest。

## 迁移验收

清单必须满足：

- `status` 为 `complete`
- `ai_keys_migrated` 为 `false`
- 每张表的 `source`、`target`、`verified` 相同
- 文件数量与源目录有效引用相同
- 每个文件包含非空 `sha256`，对象回读校验通过
- 源目录存在 `.cloud-migrated-readonly`

随后执行：

1. 在 API 启动前用 `bootstrap-admin --replace-password` 替换旧管理员密码。
2. 启动云端服务并检查 `/api/health/ready`。
3. 登录管理员，核对学生、班级、课程、排课、作品和提交数量。
4. 随机抽查文字、图片、视频、PPT/PDF 和 Markdown 文件。
5. 重新录入所有机构 AI Key 并执行真实连通测试。
6. 执行机构数据导出和独立备份。
7. 完成恢复演练后再进入灰度。

## 回滚

### 迁移尚未开放用户

1. 停止 Caddy、API、Worker 和 Beat。
2. 保留失败云端数据库、对象和日志用于调查，不立即删除卷。
3. 在原本地机器使用冻结时点的只读原件恢复应用。
4. 解除迁移副本/本地目录只读标记：

```powershell
python -m backend.app.cli unfreeze-local --data-dir E:\CoderAI学堂\workspace_data
```

5. 启动云改造前基线或已验证的本地版本。
6. 确认 SQLite 完整性、文件数量、登录和课堂主流程。

### 已有云端用户写入

立即停止继续扩容和云端写入，记录切换时间。当前方案不支持把云端新增数据自动合并回 SQLite；产品负责人必须明确决定：

- 接受回退到冻结时点并单独保全云端导出；或
- 修复云端并继续使用，不执行本地回退。

不得同时恢复本地写入并继续开放云端，也不得手工拼接两个数据库。

## 30 天保留期结束

只有同时满足以下条件才能删除本地迁移副本：

- 云端稳定运行满 30 天
- 至少完成一次独立备份恢复演练
- 所有抽查文件哈希一致
- 机构负责人书面确认
- 本地副本按数据销毁流程记录并删除

迁移 manifest、表数量、哈希和脱敏运维记录应继续保留，但不能包含密码、API Key、学生作品正文或完整监护人联系方式。
