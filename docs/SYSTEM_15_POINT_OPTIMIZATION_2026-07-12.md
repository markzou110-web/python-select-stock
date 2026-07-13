# 系统15点功能优化交付说明

日期：2026-07-12

## 完成映射

1. 历史逐日验证：新增点时候选重放及60/120/250日滚动接口，严格隔离未来收益。历史资金流/板块成分缺失的日期不会伪造。
2. 影子执行策略：回放器并行计算生产策略、硬+等待门禁、仅硬门禁，不影响Bark生产指令。
3. 冻结计划状态机：统一输出 GENERATED_PLAN、FROZEN_ACTIVE、FROZEN_TRIGGERED、EXTENDED_WAIT_PULLBACK、NEW_CONFIRMATION_ACTIVE、INVALIDATED，并给出唯一有效确认价。
4. 风险收益口径：统一输出 planned_rr、current_rr、space_rr、execution_rr，Bark/前端应以 execution_rr 为执行口径。
5. 分层健康度：`/api/system/strategy-evidence` 分开返回研究候选健康度和执行策略历史证据。
6. 组合回测：历史回放输出风险仓位组合收益和最大回撤；仓位受1%单笔资本风险封顶。
7. 相关性与容量：`/api/system/portfolio-stress` 输出高相关持仓、成交容量代理和连续两日跌停压力损失。
8. 自动事件采集：交易日16:30自动发现官方公告关键词；发现项默认未验证，人工验证前不能生成事件买点。
9. Bark投递闭环：复用既有重试和通知审计，新增失败审计显式补发接口及系统健康送达率。
10. 前端验证页面：复盘中心接入执行策略回放状态、成熟样本、胜率和平均收益。
11. 距离可交易：每个扫描结果新增 `distance_to_trade`，最多展示4个下一步条件和失效价。
12. 计划时间线：`/api/review/execution-plan-timeline/{code}` 展示确认价、止损、目标和生命周期变化。
13. 依赖安全：Axios 1.16.0、Next.js及eslint-config-next 16.2.10；高危漏洞清零。剩余2个moderate为Next内置PostCSS，强制修复会错误降级Next 9，不执行破坏性修复。
14. 运行文件治理：补充 venv_new、PID、重启PID和本地URL忽略规则。
15. 统一运行监控：系统健康增加近7日通知送达率、通知量和后台任务失败次数；保留行情、扫描、数据库和Bark检查。

## 关键接口

```text
GET  /api/review/execution-policy-replay?days=120
GET  /api/review/execution-policy-replay/rolling?windows=60,120,250
GET  /api/review/execution-plan-timeline/{code}
GET  /api/system/strategy-evidence?days=120
GET  /api/system/portfolio-stress?lookback=60
POST /api/system/event-catalysts/discover
GET  /api/system/notification-audits
POST /api/system/notification-audits/{id}/retry
GET  /api/system/health
```

## 安全边界

- 没有修改上游选股公式。
- 影子策略不会产生“指令：可交易”。
- 自动发现公告默认未验证。
- 通知补发必须显式调用，成功记录不会重复补发。
- 回测证据不足时仍返回 NOT_VALIDATED，不包装成有效。
