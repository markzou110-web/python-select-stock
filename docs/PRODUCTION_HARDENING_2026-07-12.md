# 生产加固交付说明

日期：2026-07-12

## 已实现

- 可选写操作认证：设置 `ENABLE_AUTH=true` 与 `API_TOKEN` 后，POST/PUT/PATCH/DELETE 必须使用 Bearer Token 或 X-API-Key。
- HTTP安全头：CSP、禁止iframe、MIME嗅探保护和无来源策略。
- Bark执行字段统一：正文优先使用当前有效确认价、执行盈亏比和距离可交易条件。
- 安全补发：含可交易、止损或紧急处理的通知不能从截断审计正文补发，必须重新生成当前指令。
- Schema迁移台账：`schema_migrations` 记录已应用版本。
- PostgreSQL备份脚本：`backend/scripts/backup_database.py`，凭证不打印到终端。
- 前端局部容错：复盘中心改用 `Promise.allSettled`，单卡片失败不清空其他结果。
- 前端会话令牌：设置页可将API Token仅保存到当前标签页 `sessionStorage`，关闭标签页自动失效。
- 任务幂等：公告发现任务使用数据库原子任务槽，同一交易日重复调度自动跳过。
- 个股执行展示：当前计划状态、有效确认价、执行盈亏比、距离可交易和计划时间线入口。
- 系统健康：未开启写认证时明确告警。

## 启用认证

```text
ENABLE_AUTH=true
API_TOKEN=<使用高强度随机令牌>
```

前端或调用方写请求需要：

```text
Authorization: Bearer <token>
```

前端可在“系统设置 → 写操作 API Token”中保存当前会话令牌；令牌不会进入系统设置接口。

## 数据备份

```bash
cd backend
source venv_new/bin/activate
python scripts/backup_database.py
```

备份默认进入 `backend/backups/`，该目录已排除版本控制。恢复应使用 `pg_restore` 在隔离数据库先验证，再替换生产连接。

## 仍需持续验证

- 完整3～5年逐日重演仍受历史板块、资金流和公告点时数据覆盖限制；缺失数据不做事后填充。
- 执行策略当前仍为 `NOT_VALIDATED`，需积累至少30笔同版本成熟交易。
- 官方公告自动发现仍需人工/官方链接验证后才能进入催化策略。
- 剩余PostCSS moderate漏洞等待Next官方依赖修复，不使用破坏性强制降级。
- PostgreSQL为完整功能主数据库；SQLite仅用于降级测试，LATERAL/JSONB/INTERVAL等报告需要方言适配后才能完全一致。
