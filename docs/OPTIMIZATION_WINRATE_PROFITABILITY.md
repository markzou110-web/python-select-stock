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
| | 7 | 启用 REGIME_PARAMS 自适应阈值 | 🔲 | 牛熊自适应收紧 |
| **第三优先级（出场盈利）** | 8 | ATR 自适应止损进实时风控 | ✅ | 止损与回测一致，只收紧不放宽 |
| | 9 | 分批止盈（阶梯卖出） | ✅ | +8% 锁一半，剩余跟踪 |
| | 10 | Sentinel 盘中风控频率提升 | 🔲 | 防盘中缺口击穿止损 |
| | 11 | 弱市收紧已有仓位止损 | ✅ | 弱市(bear/volatile) -9%→-6% |
| | 12 | 组合级熔断（日内亏损上限） | 🔲 | 防系统性回撤 |
| **第四优先级（测量验证）** | 13 | 度量口径统一（回撤/胜率/profit factor） | 🔲 | 调参基础一致 |
| | 14 | 走查前推 / 样本外测试 | 🔲 | 暴露过拟合 |
| | 15 | 回测加基准 alpha | 🔲 | 区分能力与 β |
| | 16 | 止损穿越缺口建模 | 🔲 | 不再低估真实亏损 |
| | 17 | failure_samples 学习闭环 | 🔲 | 失败形态反哺过滤 |

**Phase 1 已完成 5 项**（#1、#2、#4、#8、#9，220 passed）。
**Phase 2 已完成 4 项**（#3、#5、#6、#11，231 passed）。
**累计完成 9 / 17 项**，剩余 8 项（#7、#10、#12、#13-17）留作后续迭代。

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

### 🔲 #7 启用 REGIME_PARAMS 自适应阈值

**问题**：`market_regime.py:20-66` 定义了牛/熊/震荡自适应参数，`get_adaptive_params`（`:152`）**从未被扫描调用**。扫描只做粗暴的 CRITICAL 时 RSI+5（`scanner.py:742-748`）。

**建议**：把写好的自适应阈值接到扫描路径，熊市自动收紧条件。

**证据**：`backend/core/market_regime.py:20-66,152`、`backend/core/scanner.py:742-748`

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

### 🔲 #10 Sentinel 盘中风控频率提升

**问题**：`sentinel.py:803` `schedule_times=["14:20"]`，风控每天只跑一次（`:868-871`）。若 09:35 跳空击穿止损、14:20 收回，盘中止损**永不触发**。

**建议**：把风控频率提到盘中每 30 分钟，或加价格流触发。

**证据**：`backend/core/sentinel.py:803,866-871`

---

### ✅ #11 弱市收紧已有仓位止损

**问题**：regime 影响入场和仓位（RETREAT→仓位上限 10%，`decision_layer.py:108-127`），但**已有仓位的止损梯不变**。牛市建仓的票，市场翻空后仍用 -9%。

**已完成方案（只收紧不放宽）**：`compute_paper_risk_levels` 新增 `market_regime` 参数。当 regime 为弱市（`bear`/`volatile`，见 `WEAK_REGIMES`）时，初始止损从 -9% 收紧到 **-6%**（`WEAK_REGIME_STOP_RATIO`），用 `max()` 锁定"只收紧不放宽"。`run_wind_control` 把已加载的 `regime.get('regime')` 传入。新增常量 `WEAK_REGIME_STOP_PCT=-6.0`。

**证据 / 改动文件**：`backend/core/risk_engine.py`、`backend/core/risk_constants.py`、`backend/routers/paper_trade.py`、`backend/tests/test_risk_engine.py`（+4 测试：bear/volatile 收紧、bull/无 regime 不变）

---

### 🔲 #12 组合级熔断（日内亏损上限）

**问题**：`portfolio_risk.py:8-84` 只在加仓前检查，`total_plan_risk_pct>6%` 只阻新单（`:67-68`），不强制减现有敞口。无相关度检查，无日内亏损上限。

**建议**：加日内亏损上限熔断 + 持仓相关度约束。

**证据**：`backend/core/portfolio_risk.py:8-84`

---

## 第四优先级：测量与验证（无法度量就无法改进）

### 🔲 #13 度量口径统一（回撤 / 胜率 / profit factor）

**问题**：三个模块三套定义——
- 回撤：`backtest_lab.py:264`（权益曲线）/ `analytics.py:116`（复利）/ `paper_trade.py:996`（`pl_pct` 简单累加和，数学无效）
- profit_factor：`backtest_lab.py:311`（毛利/毛亏，封顶 99）/ `paper_trade.py:1014`（封顶 9.9）
- win_rate：`performance_metrics.py:17`（0% 计亏）vs `backtest_lab.py:307`（仅<0 计亏）

**建议**：统一为一个标准实现（复利权益曲线 + 毛额 profit factor），所有模块调用同一 helper。

**证据**：`backend/core/backtest_lab.py:264,311`、`backend/core/analytics.py:116`、`backend/routers/paper_trade.py:996,1014`、`backend/core/performance_metrics.py:17`

---

### 🔲 #14 走查前推 / 样本外测试

**问题**：grep `walk.?forward|out.of.sample|样本外` 在应用代码**零匹配**。`batch_experiment.py` 在全样本跑；`strategy_health.py` 用滚动 120 日，但阈值就是用这段历史调出来的——经典样本内偏差。报告的 60% 胜率样本外可能是 50%。

**建议**：在 `batch_experiment.py` 加滚动 90 训练/30 测试拆分。

**证据**：`backend/core/batch_experiment.py:9-61`、`backend/core/strategy_health.py:25`

---

### 🔲 #15 回测加基准 alpha

**问题**：`backtest_lab.py` grep `benchmark|沪深300|alpha|beta|cagr` **零匹配**。`total_return` 是绝对值，大盘 +40% 时的 +30% 被误读为"好"。

**建议**：加沪深300基准和 alpha/CAGR。

**证据**：`backend/core/backtest_lab.py:313-333`

---

### 🔲 #16 止损穿越缺口建模

**问题**：`backtest_lab.py:219-220` 止损用 `day_low <= 线` 触发但按 `entry*(1+stop_ratio)` 精确成交，跳空穿越时实际成交价远低。系统性低估亏损。

**建议**：缺口穿越时按当日开盘成交。

**证据**：`backend/core/backtest_lab.py:219-220`

---

### 🔲 #17 failure_samples 学习闭环

**问题**：`paper_trade.py:1183` 写入失败样本，但**无代码读回调整评分/阈值/过滤**（仅在 `ops_summary.py` 显示计数）。反复失败的形态不会抑制相似新信号。

**建议**：把 failure_samples 接入 `_apply_sop_filter` 或评分校准。

**证据**：`backend/routers/paper_trade.py:1183-1192`、`backend/core/ops_summary.py`

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

## 待验证项与剩余风险（Phase 1 + 2）

1. **#1 拆行的前端展示**：部分平仓后某只股票会出现多行（OPEN 剩余 + CLOSED 已减仓）。需人工核验前端列表渲染是否正确区分（`list_paper_trades` 按 id 取行天然支持，但前端展示需确认）。
2. **#8 ATR 对低波动股收紧止损**：可能让低波动股更早止损出场（预期行为，风险更可控）；若需放宽可调 `ATR_STOP_MIN_PCT`。
3. **#4 周线门槛默认关闭**：不影响现有信号数量；开启后需下一交易日实际扫描验证过滤效果。
4. **#5/#6 改变排序权重**：会让部分股票排名变化（预期效果——高胜率/强突破股上升）。需人工抽查扫描结果排序是否符合预期。
5. **#3 简化止损模型是近似**：不等于回测引擎的精确模拟（无滑点/移动止损），但已反映止损保护的经济性，远好于裸收益。
6. **#11 弱市收紧止损**：会让 bear/volatile 市中已有仓位更易止损（预期，风险更可控）。
7. **实时盯盘/风控最终验证**：改动了实时风控逻辑（止损/减仓/止盈/弱市收紧），**必须等下一交易日开盘**，通过 `POST /api/paper-trade/wind-control` 实际触发并核对推送内容与 DB 记录（尤其 `close_source='wind_control_partial'` 的新行 + 弱市止损收紧日志）。

---

## Phase 3 规划（待实施）

剩余 8 项按建议优先级：

1. **中 ROI**：#7 自适应阈值 · #10 盘中风控 · #12 组合熔断
2. **基础设施**（改动大，需谨慎）：#13 度量统一 · #14 走查前推 · #15 基准 alpha · #16 缺口建模 · #17 失败样本闭环
