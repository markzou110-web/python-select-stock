# 选股胜率与盈利优化路线图

**项目**: Alpha Vision
**最后更新**: 2026-06-16
**状态说明**: ✅ 已完成 · 🔲 待实施

本文档基于对入场策略（`strategy.py`）、出场风控（`risk_engine.py` / `operation_plan.py`）、验证测量（`backtest_lab.py` / `strategy_health.py`）三层代码的系统性审查，列出提升**选股胜率**与**盈利水平**的 17 项优化点，每项附 `file:line` 证据与完成状态。

---

## 总览：优先级与完成状态

| 档位 | # | 改进项 | 状态 | 预期收益 |
|---|---|---|---|---|
| **第一优先级（修漏洞）** | 1 | REDUCE 真正部分平仓 | ✅ | 止住最大盈利泄漏 |
| | 2 | 扫描入口检查 preflight blocking | ✅ | 防坏数据产生假信号 |
| | 3 | 健康熔断用真实交易收益 | ✅ | 熔断口径与执行一致 |
| **第二优先级（抬胜率）** | 4 | tv_dual_strict 周线门槛（默认关闭） | ✅ | 过滤逆周线下跌信号 |
| | 5 | TV-ZP/tv_dual 信号分连续化 | ✅ | 候选股间有区分度 |
| | 6 | 历史胜率进综合排序分 | ✅ | 让高胜率股排在前面 |
| | 7 | 启用 REGIME_PARAMS 自适应阈值 | ✅ | 牛熊自适应收紧 |
| **第三优先级（出场盈利）** | 8 | ATR 自适应止损进实时风控 | ✅ | 止损与回测一致，只收紧不放宽 |
| | 9 | 分批止盈（阶梯卖出） | ✅ | +8% 锁一半，剩余跟踪 |
| | 10 | Sentinel 盘中风控频率提升 | ✅ | 风控独立 tick，每 30 分钟 |
| | 11 | 弱市收紧已有仓位止损 | ✅ | 弱市(bear/volatile) -9%→-6% |
| | 12 | 组合级熔断（日内亏损上限） | ✅ | 日内亏损超 -5% 暂停新开仓 |
| **第四优先级（测量验证）** | 13 | 度量口径统一（回撤/胜率/profit factor） | ✅ | 调参基础一致（修复回撤 bug） |
| | 14 | 走查前推 / 样本外测试 | ✅ | IS/OOS 拆分暴露过拟合 |
| | 15 | 回测加基准 alpha | ✅ | 区分能力与 β |
| | 16 | 止损穿越缺口建模 | ✅ | 不再低估真实亏损 |
| | 17 | failure_samples 学习闭环 | ✅ | 失败形态反哺过滤 |

**Phase 1 已完成 5 项**（#1、#2、#4、#8、#9，220 passed）。
**Phase 2 已完成 4 项**（#3、#5、#6、#11，231 passed）。
**Phase 3 已完成 3 项**（#7、#10、#12，251 passed）。
**Phase 4 已完成 4 项**（#13、#15、#16、#17，265 passed）。
**Phase 5 已完成 1 项**（#14，269 passed）。
**🎉 全部 17 / 17 项已完成。**

---

## 第一优先级：修掉"看起来在保护、实际没生效"的逻辑漏洞

这类问题最危险——系统表现得像有保护，实则没有，给人虚假的安全感。

### ✅ #1 REDUCE 真正部分平仓（最大盈利泄漏修复）

**问题**：`operation_plan.py` 发出 `action="REDUCE"` 信号，但 `_wind_control_decision`（`paper_trade.py`）对 REDUCE 只返回 `should_close=False` + 通知，**从不真正减仓**。`shares` 字段开仓时写入一次后从不递减，每条"减仓保护"提醒只是文字，仓位要么全仓扛、要么全平。

**已完成方案（无 schema 变更，拆行）**：
- `_wind_control_decision`：REDUCE + 可执行 + **当前盈利**时返回 `should_reduce=True`；亏损中降级为预警。
- `run_wind_control`：原行 shares 减半保留 OPEN（最小 100 股，对齐整手），新插入一行 CLOSED（`close_source='wind_control_partial'`）记录已落袋部分。剩余仓位继续被移动止损跟踪。
- 盈亏%口径不变（始终按价格算），不影响统计。

**证据 / 改动文件**：`backend/routers/paper_trade.py`、`backend/tests/test_paper_trade_time_stop.py`（+3 测试）

---

### ✅ #2 扫描入口检查 preflight blocking

**问题**：`build_scan_preflight`（`scan_preflight.py`）计算了 `blocking` 标志，但只在 `GET /scan/preflight` 接口暴露，`perform_market_scan`（`scanner.py`）从不检查。仪表盘显示"数据异常勿扫描"，扫描器照样跑，坏/陈旧数据静默进入信号链路。

**已完成方案**：`perform_market_scan` 拿到 engine 后调用 `build_scan_preflight`，`blocking=True` 时记录警告并返回 `[]` 中止。带 `SCAN_PREFLIGHT_ENFORCE` 开关（默认开），可回退。预检自身异常降级为告警不阻断。

**证据 / 改动文件**：`backend/core/scanner.py`、`backend/tests/test_scanner_strategy_paths.py`（+2 测试）

---

### ✅ #3 健康熔断用真实交易收益

**问题**：`strategy_health.py` 用"信号收盘价买入、5天后卖出"的**裸收益**判定策略 PAUSED/DOWNWEIGHT（`pro_workflow.py:14-17`），**不含止损、滑点、手续费**。一个带 -8% 止损实际亏钱的策略，可能在这个指标上显示 ACTIVE 继续运行。

**已完成方案（SQL 简化止损模型 + Python 端计算）**：`build_strategy_health` 取信号后未来 5 个交易日的收盘，在 Python 端用 `_apply_stop_take_model` 套用简化止损/止盈：5 日内曾跌破 -9%（`FIXED_STOP_LOSS_PCT`）→ 计为 -9%；曾涨超 +15%（`TAKE_PROFIT_PCT`）→ 计为 +15%；否则取第 5 日实际收益。SQL 按方言选 `string_agg`(PG)/`GROUP_CONCAT`(SQLite)，截止日在 Python 端算（跨库兼容）。熔断指标现反映止损保护的经济性。

**证据 / 改动文件**：`backend/core/strategy_health.py`、`backend/tests/test_strategy_health.py`（+3 测试：止损封底/止盈封顶/区间内不变）

---

## 第二优先级：入场逻辑的硬伤（直接抬升胜率）

### ✅ #4 tv_dual_strict 周线对齐门槛（默认关闭、可配置）

**问题**：周线确认只对 `consensus`/`squeeze(both)` 生效（`indicators.py:257`）。**默认生产策略 `tv_dual_strict`（`scanner.py:685`）的信号本身无任何周线门槛**——一只股票可在周线下跌结构中触发日线 BUY。

**已完成方案（默认关闭）**：`perform_market_scan` 新增 `tv_weekly_gate=False` 参数，透传到 `single_stock_task`。开启后，周线波段未走强（MA 未向上 + EMA10w≤EMA30w）的候选直接过滤，复用既有 `get_weekly_indicators`。

**证据 / 改动文件**：`backend/core/scanner.py`、`backend/tests/test_scanner_strategy_paths.py`（+3 测试）

---

### ✅ #5 TV-ZP / tv_dual 信号分连续化

**问题**：`strategy.py:1165`（TV-ZP 固定 `75 + fund_score`）、`strategy.py:1233`（tv_dual 命中双信号固定 `88`）。所有候选股原始分几乎一样，"这次突破有多强"在源头无体现。

**已完成方案（复用 check_squeeze 的 vol_ratio 模式）**：在两个评分函数作用域内一行重建 `vol_ratio = 成交量/Vol_MA20`，叠加 `signal_strength = min(vol_ratio,3.0)*系数 + min(pct_change,5)`。TV-ZP：`70 + strength`（区间 70~90）；tv_dual：双命中 `82 + strength`、单命中 `72 + strength`。**严格不改变入选门槛**，只影响候选间相对排名。

**证据 / 改动文件**：`backend/core/strategy.py`（`:1165`、`:1233`）、`backend/tests/test_scanner_strategy_paths.py`（+1 测试：量能强者分更高）

---

### ✅ #6 历史胜率进综合排序分

**问题**：`scanner.py:286-287` 把"历史胜率≥50%"当二元勾选，但最终排序（`scanner.py:1354-1361`）不包含胜率/盈亏比作为加权项。一只历史 70% 胜率的票可能排在 45% 胜率的票后面。

**已完成方案（重新分配权重，腾出胜率预算）**：`calibrate_scan_scores` 新增 `historical_win_rate` 项，权重重新分配为 `percentile×0.35 + price_action×0.18 + sector×0.12 + trade_opportunity×0.25 + win_rate×0.10`（仍求和=1.0）。解析 `历史胜率` 中文键（`"58.0%"` 字符串），无数据时取中性 50。权重抽为模块级常量（`W_*`）便于调参。

**证据 / 改动文件**：`backend/core/score_calibration.py`、`backend/tests/test_score_calibration.py`（+3 测试：胜率高者分高/缺省中性/权重和=1）

---

### ✅ #7 启用 REGIME_PARAMS 自适应阈值

**问题**：`market_regime.py` 定义了牛/熊/震荡自适应参数（`REGIME_PARAMS`），`get_adaptive_params` **从未被扫描调用**。扫描只做粗暴的 CRITICAL 时 RSI+5。且扫描用的是 `core.data` 的 `OFFENSIVE/CRITICAL/DEFENSIVE` 词表，与 `MarketRegime` 的 `bull/bear/volatile` 不通——全仓库无映射。

**已完成方案**：
- `market_regime.py` 新增 `map_status_to_regime()`：`OFFENSIVE→bull`、`CRITICAL→bear`、`DEFENSIVE→volatile`，`get_adaptive_params` 内部自动调用（幂等）。
- `scanner.py` 启用自适应：`perform_market_scan` 根据 `reg_status` 调 `get_adaptive_params`，覆盖 `threshold/vol_multiplier/rsi_min/stop_loss_pct/sqz_lookback/use_bb_sqz/pine_min_signals`。带 `SCAN_REGIME_ADAPTIVE` 开关（默认开）。bear 时最严（threshold↓、vol↑、rsi↑），bull 时最松。
- 保留 OFFENSIVE 换手率放宽。

**证据 / 改动文件**：`backend/core/market_regime.py`、`backend/core/scanner.py`、`backend/tests/test_phase3_optimizations.py`（+8 测试：映射 6 + 自适应 2）

---

## 第三优先级：出场与风控的盈利提升（出场往往更决定盈利）

### ✅ #8 ATR 自适应止损进入实时风控引擎

**问题**：`compute_paper_risk_levels`（`risk_engine.py:90`）用 `entry*0.91`（-9%）算初始止损，**完全忽略 ATR**。ATR 止损逻辑已存在于 `evaluate_exit_signals`（`strategy.py:1517-1527`）但只告警。回测（`backtest_lab.py:226` 用 ATR）与实盘（不用）行为不一致，**回测高估实盘胜率**。

**已完成方案（只收紧不放宽）**：`compute_paper_risk_levels` 新增 `atr` 参数。传入时 `initial_stop = max(固定-9%, ATR自适应止损)`，clamp 到 [-15%, -5%]。**关键安全语义：ATR 只收紧、不放宽**——高波动股不会比 -9% 更宽。`_local_price_action_summary` surfacing `latest_atr`，`run_wind_control` 传入。

**证据 / 改动文件**：`backend/core/risk_engine.py`、`backend/routers/paper_trade.py`、`backend/tests/test_risk_engine.py`（+4 测试）

---

### ✅ #9 分批止盈（阶梯卖出）

**问题**：止盈是单一 +15% 目标，仅展示（`risk_engine.py:137`）。无"+8% 卖一半、剩余跟踪"逻辑。赢家要么全仓触发移动止损、要么超时。

**已完成方案**：`risk_constants.py` 新增 `FIRST_PROFIT_TAKE_PCT=8.0`、`FIRST_PROFIT_TAKE_RATIO=0.5`。`operation_plan.py` 盈利达 +8% 且未减仓时发 REDUCE，通过 remark 标记防重复触发。复用 #1 的部分平仓执行路径——盈利 +8% 先锁一半，剩余用高档移动止损跟踪。

**证据 / 改动文件**：`backend/core/risk_constants.py`、`backend/core/operation_plan.py`、`backend/tests/test_operation_plan.py`（+3 测试）

---

### ✅ #10 Sentinel 盘中风控频率提升

**问题**：`sentinel.py` `schedule_times=["14:20"]`，风控与（重）扫描耦合在同一触发块，**一天只跑一次**。若 09:35 跳空击穿止损、14:20 收回，盘中止损永不触发。

**已完成方案（风控独立 tick）**：把 `run_wind_control` 从扫描触发块中拆出，新增 `_should_run_wind_control()`：在 A 股交易时段内，距上次风控超过 `wind_control_interval_minutes`（默认 **30 分钟**）则触发。扫描频率不变（仍按 schedule_times）。首次在交易时段内也触发。即使风控出错也更新时间戳避免高频重试淹没日志。

**证据 / 改动文件**：`backend/core/sentinel.py`、`backend/tests/test_phase3_optimizations.py`（+4 测试：默认间隔/首次触发/节流/非交易时段）

---

### ✅ #11 弱市收紧已有仓位止损

**问题**：regime 影响入场和仓位（RETREAT→仓位上限 10%，`decision_layer.py:108-127`），但**已有仓位的止损梯不变**。牛市建仓的票，市场翻空后仍用 -9%。

**已完成方案（只收紧不放宽）**：`compute_paper_risk_levels` 新增 `market_regime` 参数。当 regime 为弱市（`bear`/`volatile`，见 `WEAK_REGIMES`）时，初始止损从 -9% 收紧到 **-6%**（`WEAK_REGIME_STOP_RATIO`），用 `max()` 锁定"只收紧不放宽"。`run_wind_control` 把已加载的 `regime.get('regime')` 传入。新增常量 `WEAK_REGIME_STOP_PCT=-6.0`。

**证据 / 改动文件**：`backend/core/risk_engine.py`、`backend/core/risk_constants.py`、`backend/routers/paper_trade.py`、`backend/tests/test_risk_engine.py`（+4 测试：bear/volatile 收紧、bull/无 regime 不变）

---

### ✅ #12 组合级熔断（日内亏损上限）

**问题**：`portfolio_risk.py` 只在加仓前检查（持仓数/计划风险%），`total_plan_risk_pct>6%` 只阻新单，**无日内亏损上限、无已实现亏损追踪、无组合熔断**。

**已完成方案（日内亏损硬熔断）**：
- `portfolio_risk.py` 新增 `evaluate_daily_loss_circuit_breaker()`：统计当日 CLOSED 实现盈亏（按 `(close-entry)*shares` 近似），亏损占比超过 `daily_loss_limit_pct`（默认 **-5%**）时返回 `status="halt"`。`DEFAULT_RISK_BUDGET` 新增 `daily_loss_limit_pct` 键。
- `add_paper_trade` 接入：熔断触发时硬阻止新开仓（返回 `status="halt"`），**与 warning 不同，不可被 force 绕过**。
- 盈利日永不熔断；可自定义更紧的熔断线。

**证据 / 改动文件**：`backend/core/portfolio_risk.py`、`backend/routers/paper_trade.py`、`backend/tests/test_phase3_optimizations.py`（+5 测试：无平仓/限内/超限/盈利/自定义线）

---

## 第四优先级：测量与验证（无法度量就无法改进）

### ✅ #13 度量口径统一（回撤 / 胜率 / profit factor）

**问题**：三个模块三套定义——回撤（复利/账户权益/累加和，其中 `paper_trade.py` 的累加和数学无效）；profit_factor（毛额，但 cap 99 vs 9.9）；win_rate（0% 计不计亏不一致）。6+ 前端组件依赖各自字段名/符号。

**已完成方案（统一算法，保留输出契约）**：`analytics.py` 新增 3 个规范 helper（`compute_equity_curve_drawdown` 复利权益回撤 / `compute_profit_factor` 毛额可配 cap / `compute_win_rate`）。各模块改调 helper，但**保留字段名/符号/上限**（向后兼容，不改前端）。唯一实质修复：`paper_trade.py` 回撤从无效的 `pl_pct` 累加和改为复利权益曲线（数值更准）。

**证据 / 改动文件**：`backend/core/analytics.py`、`backend/core/backtest_lab.py`、`backend/routers/paper_trade.py`、`backend/tests/test_metric_unification.py`（+8 测试）

---

### ✅ #14 走查前推 / 样本外测试

**问题**：`batch_experiment.py` 在全样本跑，报告的是**样本内**指标——阈值就是用这段历史调出来的，经典样本内偏差。报告 60% 胜率样本外可能 50%。

**已完成方案**：`batch_experiment.py` 新增 `run_walk_forward_experiment`（不改原 `run_batch_experiment`，向后兼容）：按 `train_ratio`（默认 0.7）把日期拆成 train(IS)/test(OOS)，**先在全量上算指标再按时间切**（保证 test 段指标窗口完整），分别回测对比。每只股返回 `in_sample`/`out_of_sample`/`overfit_gap`（OOS胜率-IS胜率，正值=稳健、负值=过拟合）。`overfit_gap < -10` 触发 `overfit_warning`。`backtest.py` 新增 `POST /api/backtest/walk-forward` 端点。

**证据 / 改动文件**：`backend/core/batch_experiment.py`、`backend/routers/backtest.py`、`backend/tests/test_batch_experiment.py`（+4 测试）

---

### ✅ #15 回测加基准 alpha

**问题**：`backtest_lab.py` grep `benchmark|沪深300|alpha|cagr` **零匹配**。`total_return` 是绝对值，大盘 +40% 时的 +30% 被误读为"好"。

**已完成方案**：`run_single_stock_backtest` 新增 `bench_df` 参数。若提供沪深300（`get_index_hist("000300")`，24h 缓存），summary 新增 `benchmark_return`/`alpha`（策略收益-基准收益）/`cagr`（按 244 交易日/年化）。`backtest.py /single` 端点按相同日期窗取基准传入，try/except 容错（拉取失败字段为 None，前端兜底）。

**证据 / 改动文件**：`backend/core/backtest_lab.py`、`backend/routers/backtest.py`、`backend/tests/test_backtest_lab.py`（+2 测试）

---

### ✅ #16 止损穿越缺口建模

**问题**：`backtest_lab.py:219-220` 止损用 `day_low <= 线` 触发但**永远按止损线成交**，跳空穿越时高估收益。

**已完成方案**：若当日开盘已击穿止损线（缺口穿越），按**当日开盘**成交（更差）；否则按止损线。`exit_price = min(stop_line, day_open) if day_open <= stop_line else stop_line`。更真实地反映滑点。

**证据 / 改动文件**：`backend/core/backtest_lab.py:219-220`、`backend/tests/test_backtest_lab.py`（+1 测试）

---

### ✅ #17 failure_samples 学习闭环

**问题**：`paper_trade.py` 只在手动亏损平仓时写 failure_samples，自动止损不写；且**无代码读回调整评分/过滤**（仅显示计数）。反复失败的形态不抑制相似新信号。

**已完成方案（扩写入 + 扫描否决）**：
- **扩写入**：`run_wind_control` 自动止损平仓也写 failure_samples（`failure_type=wind_control_stop`），丰富语料。
- **扫描否决**：`scanner.py` 扫描完成后预查近 `FAILURE_LOOKBACK_DAYS`(90) 天同代码失败次数，注入 `recent_failure_count`；`_apply_sop_filter` 中 `>= FAILURE_VETO_MIN_COUNT`(2) 次的候选一票否决降为 D 级。`_apply_sop_filter` 不改签名（数据经 res dict 传入，避免改 7 个测试）。

**证据 / 改动文件**：`backend/routers/paper_trade.py`、`backend/core/scanner.py`、`backend/tests/test_scanner_strategy_paths.py`（+3 测试）

---

## Phase 1 实施详情（已完成）

**范围**：5 项（#1、#2、#4、#8、#9），按"止血 + 抬胜率"组合，投入产出比最高。

**验证**：`pytest tests/ -q` → **220 passed, 0 failed**（原 205 → 新增 15 个测试）

**涉及文件**：

| 文件 | 改动 |
|---|---|
| `backend/core/risk_engine.py` | #8 ATR 初始止损 |
| `backend/core/risk_constants.py` | #9 分批止盈常量 |
| `backend/core/operation_plan.py` | #9 首笔止盈发 REDUCE + `already_reduced` 参数 |
| `backend/routers/paper_trade.py` | #1 REDUCE 拆行执行 + #8 ATR surfacing |
| `backend/core/scanner.py` | #2 preflight 熔断 + #4 tv_weekly_gate |
| `backend/tests/test_risk_engine.py` | +4 个 ATR 测试 |
| `backend/tests/test_operation_plan.py` | +3 个分批止盈测试 |
| `backend/tests/test_paper_trade_time_stop.py` | +3 个 REDUCE 测试 |
| `backend/tests/test_scanner_strategy_paths.py` | +5 个（周线门槛 3 + preflight 2） |

**新增可配置常量**（便于回退 / 调参）：
- `risk_constants.py`: `FIRST_PROFIT_TAKE_PCT`（8.0）、`FIRST_PROFIT_TAKE_RATIO`（0.5）、`FIRST_PROFIT_TAKE_MARK`
- `scanner.py`: `SCAN_PREFLIGHT_ENFORCE`（True）、`perform_market_scan(tv_weekly_gate=False)`

---

## Phase 2 实施详情（已完成）

**范围**：4 项高 ROI（#3、#5、#6、#11），按"抬胜率"组合。

**验证**：`pytest tests/ -q` → **231 passed, 0 failed**（Phase 1 基线 220 → 新增 11 个测试）

**涉及文件**：

| 文件 | 改动 |
|---|---|
| `backend/core/strategy_health.py` | #3 简化止损模型（Python 端 `_apply_stop_take_model` + 跨库 SQL） |
| `backend/core/strategy.py` | #5 TV-ZP/tv_dual 信号分连续化（`vol_ratio` + `pct_change`） |
| `backend/core/score_calibration.py` | #6 历史胜率进排序（权重重分配 + `_parse_win_rate`） |
| `backend/core/risk_engine.py` | #11 弱市止损收紧（`market_regime` 参数） |
| `backend/core/risk_constants.py` | #11 `WEAK_REGIME_STOP_PCT`（-6.0）、`WEAK_REGIMES` |
| `backend/routers/paper_trade.py` | #11 传 `market_regime` 给风控引擎 |
| `backend/tests/test_strategy_health.py` | +3 测试（止损封底/止盈封顶/区间内不变） |
| `backend/tests/test_score_calibration.py` | +3 测试（胜率进排序） |
| `backend/tests/test_scanner_strategy_paths.py` | +1 测试（信号分连续化） |
| `backend/tests/test_risk_engine.py` | +4 测试（弱市止损收紧） |

**新增可配置常量**（便于回退 / 调参）：
- `score_calibration.py`: `W_STRATEGY_PERCENTILE`（0.35）、`W_PRICE_ACTION`（0.18）、`W_SECTOR_ALIGNMENT`（0.12）、`W_TRADE_OPPORTUNITY`（0.25）、`W_HISTORICAL_WIN_RATE`（0.10）
- `risk_constants.py`: `WEAK_REGIME_STOP_PCT`（-6.0）、`WEAK_REGIMES`（"bear","volatile"）
- `compute_paper_risk_levels(market_regime=None)`：新增参数

---

## Phase 3 实施详情（已完成）

**范围**：3 项中 ROI（#7、#10、#12），按"风控加固 + 牛熊自适应"组合。

**验证**：`pytest tests/ -q` → **251 passed, 0 failed**（Phase 2 基线 231 → 新增 17 个测试）

**涉及文件**：

| 文件 | 改动 |
|---|---|
| `backend/core/market_regime.py` | #7 `map_status_to_regime` + `get_adaptive_params` 自动映射 |
| `backend/core/scanner.py` | #7 `SCAN_REGIME_ADAPTIVE` 开关 + 自适应参数应用 |
| `backend/core/sentinel.py` | #10 风控独立 tick（`wind_control_interval_minutes`、`_should_run_wind_control`） |
| `backend/core/portfolio_risk.py` | #12 `evaluate_daily_loss_circuit_breaker` + `daily_loss_limit_pct` |
| `backend/routers/paper_trade.py` | #12 add_paper_trade 接入日内亏损熔断（硬阻止，不可 force） |
| `backend/tests/test_phase3_optimizations.py` | +17 测试（#7 映射/自适应 8 + #10 频率 4 + #12 熔断 5） |

**新增可配置常量**（便于回退 / 调参）：
- `scanner.py`: `SCAN_REGIME_ADAPTIVE`（True）
- `sentinel.py`: `wind_control_interval_minutes`（30）
- `portfolio_risk.py`: `DEFAULT_RISK_BUDGET["daily_loss_limit_pct"]`（5.0）

---

## 待验证项与剩余风险（Phase 1 + 2）

1. **#1 拆行的前端展示**：部分平仓后某只股票会出现多行（OPEN 剩余 + CLOSED 已减仓）。需人工核验前端列表渲染是否正确区分（`list_paper_trades` 按 id 取行天然支持，但前端展示需确认）。
2. **#8 ATR 对低波动股收紧止损**：可能让低波动股更早止损出场（预期行为，风险更可控）；若需放宽可调 `ATR_STOP_MIN_PCT`。
3. **#4 周线门槛默认关闭**：不影响现有信号数量；开启后需下一交易日实际扫描验证过滤效果。
4. **#5/#6 改变排序权重**：会让部分股票排名变化（预期效果——高胜率/强突破股上升）。需人工抽查扫描结果排序是否符合预期。
5. **#3 简化止损模型是近似**：不等于回测引擎的精确模拟（无滑点/移动止损），但已反映止损保护的经济性，远好于裸收益。
6. **#11 弱市收紧止损**：会让 bear/volatile 市中已有仓位更易止损（预期，风险更可控）。
7. **实时盯盘/风控最终验证**：改动了实时风控逻辑（止损/减仓/止盈/弱市收紧），**必须等下一交易日开盘**，通过 `POST /api/paper-trade/wind-control` 实际触发并核对推送内容与 DB 记录（尤其 `close_source='wind_control_partial'` 的新行 + 弱市止损收紧日志）。

---

## Phase 4 实施详情（已完成）

**范围**：4 项测量验证基础设施（#13、#15、#16、#17）。#14 走查前推留后续（需重写批量实验）。

**验证**：`pytest tests/ -q` → **265 passed, 0 failed**（Phase 3 基线 251 → 新增 14 个测试）

**涉及文件**：

| 文件 | 改动 |
|---|---|
| `backend/core/analytics.py` | #13 新增 3 个规范 helper + 回撤复用 helper |
| `backend/core/backtest_lab.py` | #13 profit_factor/win_rate 改 helper · #15 bench_df alpha/CAGR · #16 缺口建模 |
| `backend/routers/paper_trade.py` | #13 回撤修复（累加和→复利）+ profit_factor 改 helper · #17 自动止损写 failure_samples |
| `backend/routers/backtest.py` | #15 取沪深300 + 传入 bench_df |
| `backend/core/scanner.py` | #17 失败模式预查注入 + SOP 否决 |
| `backend/tests/test_metric_unification.py` | +8 测试（helper 4 + 回撤 2 + profit_factor 2） |
| `backend/tests/test_backtest_lab.py` | +3 测试（alpha 2 + 缺口 1） |
| `backend/tests/test_scanner_strategy_paths.py` | +3 测试（失败模式注入/否决/不误伤） |

**新增可配置常量**（便于回退 / 调参）：
- `backtest_lab.py`: `run_single_stock_backtest(bench_df=None)` 新增参数
- `scanner.py`: `FAILURE_LOOKBACK_DAYS`（90）、`FAILURE_VETO_MIN_COUNT`（2）

---

## Phase 5 实施详情（已完成 — 17/17 全部完成）

**范围**：#14 走查前推 / 样本外测试（最后一项）。

**验证**：`pytest tests/ -q` → **269 passed, 0 failed**（Phase 4 基线 265 → 新增 4 个测试）

**涉及文件**：

| 文件 | 改动 |
|---|---|
| `backend/core/batch_experiment.py` | 新增 `run_walk_forward_experiment` + `_split_df_by_ratio`（不改原函数） |
| `backend/routers/backtest.py` | 新增 `POST /api/backtest/walk-forward` 端点 |
| `backend/tests/test_batch_experiment.py` | +4 测试（拆分顺序/数据不足/返回结构/过拟合标记） |

**新增可配置常量**：`DEFAULT_TRAIN_RATIO`（0.7）、`OVERFIT_WARNING_GAP`（10.0）

---

## 🎉 全部 17 项优化已完成

| 阶段 | 提交 | 项数 | 测试 |
|---|---|---|---|
| 数据同步 + Phase 1 | `3acfabb98` | 6 | 220 |
| Phase 2 | `647fe473f` | 4 | 231 |
| Phase 3 | `9bb6b8fb9` | 3 | 251 |
| Phase 4 | `c8c9a883f` | 4 | 265 |
| Phase 5 | （本次） | 1 | 269 |

所有改动均通过全量回归（269 passed），不破坏现有功能。实时风控相关改动（止损/减仓/止盈/弱市收紧/日内熔断/盘中高频检查）需在下一交易日开盘最终验证。
