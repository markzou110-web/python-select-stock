# 下个交易日实测清单（2026-09-30 提交版）

五批优化（easy-stock 三项 + 可靠性/容量/纪律修复）的代码均已落库并通过 1217 例自动化测试，
以下各项依赖真实行情/真实推送，**必须在下个交易日开盘前后人工核对**。每项给出验证方法、
预期结果与失败处置。全部通过后在本文件底部勾选。

## 一、盘前（08:30–09:25）

| # | 项 | 验证方法 | 预期 | 失败处置 |
|---|---|---|---|---|
| 1 | 四队列 worker | `ps aux \| grep "celery.*worker"` 应见 realtime / collector / scan / maintenance 四个进程；`.celery_collector_pid` 存在 | 4 个 worker + 1 个 beat | 只需重启 `./start.sh`；确认 AGENTS.md 四队列命令 |
| 2 | 盘前 Bark 第 12 行 | 08:45 Bark 持仓推送正文 | "Elder宽度(…) 行"之后追加 "涨停情绪(日期): 涨停X家/炸板Y家 最高N板 晋级率Z% 炸板率W%"；炸板率≥40% 行尾带"降暴露" | 涨停情绪行首次出现需前一日 15:06 任务已跑（见 #5）；行缺失先查 `breadth_history` 的 zt_sealed_count 是否非空 |
| 2b | 市场状态闸门 SHADOW | 盘前 Bark 第 13 行；19:15 日志 `Market state gate recorded` | 行含"10日动量±X% [正常/建议暂停新开仓]（SHADOW 观察中）"；system_setting 出现 `market_state_gate:日期` 键 | 行缺失查 daily_k 近 45 日数据；阈值 -3% 见 risk 常量 |
| 3 | A-EOD SHADOW 无新意图 | 扫描日志/ReviewCenter：此前走"尾盘受控小仓"的候选 | 显示"尾盘受控[SHADOW]"、仓位 0、cautions 含"A-EOD受控通道SHADOW中"；**不再签发 A-EOD 执行意图** | 属预期行为。若需临时恢复 5% 实仓（不建议）：`A_EOD_CONTROLLED_ENABLED=true` |

## 二、盘中

| # | 项 | 验证方法 | 预期 | 失败处置 |
|---|---|---|---|---|
| 4 | collector 增量水位 | 日志中 `collect_limit_up_leadership` 各次返回 | `total_in_pool` 稳定、`saved` 多数为 0（只有封单/炸板变化行 >0）；分钟 bar 任务 `bars` 首轮后多为 0 | 若 saved 始终=total：说明签名列被外部改动，核对 `_POOL_SIGNATURE_FIELDS` 与落库列一致 |
| 5 | 行业资金流列名归一 | 11:40 与 15:05 日志 `Sector fund flow task done` | `{"saved": 80~90, "industries": 80~90, "errors": 0}` | `industries=0` → 东财列名变体未命中：按日志上方 `unexpected columns [...]` 打印的实际列名，调整 `core/sector_fund_flow.py` 的 `_column` 关键字 |
| 6 | 15:06 涨停情绪聚合 | 日志 `Limit-up sentiment task done` | sealed/broken/max_streak 数值合理；`promotion_rate` 首日为 null（前日数据缺失，刻意 fail-open），连续运行 2 日后应有值 | 永远 null → 查 15:01 `collect_limit_up_leadership` 是否在跑（窗口 09:00-15:05） |
| 7 | 止损快盯统一口径 | 有持仓击穿时（或手工观察日志） | 快盯告警的止损价与风控 `active_stop_price` 一致（弱市 -6% 口径而非固定 -9%） | 若告警价偏差，查 `_cached_daily_atr` 指纹（`_ATR_FRONTIER_CACHE`）是否卡死 |

## 三、盘后

| # | 项 | 验证方法 | 预期 | 失败处置 |
|---|---|---|---|---|
| 8 | 15:01→15:06 链 | 15:06 日志 | 情绪聚合成功且 15:01 采集已完成（15:01 无 "Market closed" 白跑） | 15:01 白跑 → 检查 collector worker 是否被分钟 bar 占住（增量水位上线后应已缓解） |
| 9 | 龙虎榜 17:05 | 日志 `LHB records task done` + 复盘中心"龙虎榜观察"卡片 | saved>0；卡片出现记录，持仓行琥珀高亮 | 卡片空 → 查 `lhb_records` 表有行但前端 404（路由 `GET /api/review/lhb-records`） |
| 10 | 数据看门狗 19:35 | 日志 `Data freshness check` / Bark | `reference_date` = 当日、`stale=[]`；stale 非空时按条目排查对应链路 | `heartbeat_lag_sessions` 误报 → 确认 `trading_calendar` 日历加载成功（akshare 接口） |
| 11 | 半日 bar 终值化 | SQL：当日 `daily_k` 的 close/volume vs 收盘实际值 | 收盘后回拉覆盖午间半日值 | 仍为半日 → 查 18:00 同步日志是否走了"已是最新"短路（应为回拉分支） |
| 12 | 备份 | 20:30 与 21:30 日志 | 20:30 备份成功；21:30 返回 `skipped: already_backed_up_today`（幂等） | 失败应有 Bark（AlphaVision_Data 组）；无 Bark 则查 notifier |
| 13 | 净值/熔断口径 | 下次浮亏熔断触发时 | 推送含分母说明（`denominator`=100 万）；REAL 止损确认后每 30 分钟升级提醒 | — |

## 四、周六（非交易日）

| # | 项 | 验证方法 | 预期 |
|---|---|---|---|
| 14 | 每周维护 04:00 | 日志 `Weekly data maintenance done` | `purged` 各表行数合理；**`paper_trades`/`daily_k`/`scan_history` 计数不变**（业务数据零触碰） |
| 15 | 长假误报回归 | 假期中的工作日 19:35 | 看门狗不推送（交易日差口径，错过 0 个交易日） |

## 五、浏览器人工（任意时段，一次性）

- [ ] 个股页挂 2 分钟（30s 轮询）：K 线图无闪烁、滚动位置保持（SplitKLineCharts 指纹守卫）
- [ ] 结果表"导出 CSV"与复盘中心导出：Excel 打开列对齐（逗号字段被正确引号包裹）
- [ ] 复盘中心断网刷新：顶部出现"N 项加载失败"琥珀警示条 + 重试按钮
- [ ] Dashboard 停掉后端后刷新：红色"行情服务未响应"横幅 + 重试；重启后端点重试恢复

## 六、CI

- [ ] push 后 GitHub Actions 两 job（backend-tests / frontend-checks）绿；frontend lint 仍为 continue-on-error（存量 87 个 error 清理后移除）

## 验证记录

- [ ] 盘前 #1-#3
- [ ] 盘中 #4-#7
- [ ] 盘后 #8-#13
- [ ] 周六 #14-#15
- [ ] 浏览器五项
- [ ] CI 绿

验证人 / 日期：____________
