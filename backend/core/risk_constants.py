"""
Alpha Vision - Risk Management Constants (统一风控参数配置)

All stop-loss, take-profit, and risk control thresholds are defined here
as the single source of truth. Any layer (backtest, real-time alerts,
wind control) should import from this module instead of hardcoding values.
"""

# ── 固定止损 (Absolute Stop Loss) ──
# 跌破买入成本的百分比即触发硬性止损
FIXED_STOP_LOSS_PCT = -9.0          # -9% (e.g. entry * 0.91)
FIXED_STOP_LOSS_RATIO = 1.0 + FIXED_STOP_LOSS_PCT / 100.0  # 0.91

# ── 弱市止损收紧 (Regime-aware Stop Tightening) ──
# 当大盘处于弱市（bear/volatile）时，已有持仓的初始止损从 -9% 收紧到 -6%，
# 降低系统性回撤期的单笔风险。只在 compute_paper_risk_levels 中生效，只收紧不放宽。
WEAK_REGIME_STOP_PCT = -6.0
WEAK_REGIME_STOP_RATIO = 1.0 + WEAK_REGIME_STOP_PCT / 100.0  # 0.94
# 触发收紧的 regime 值（与 market_regime.py 的 regime 字段对齐）
WEAK_REGIMES = ("bear", "volatile")

# ── ATR 自适应止损 (ATR Adaptive Stop Loss) ──
# 基于个股波动率动态计算的止损倍数
ATR_STOP_MULTIPLIER = 2.0
# ATR 止损幅度上下限保护（防止低波动过紧，高波动过宽）
ATR_STOP_MIN_PCT = -5.0             # 最紧止损不超过 -5%
ATR_STOP_MAX_PCT = -15.0            # 最宽止损不超过 -15%

# ── 移动止盈 (Trailing Stop) ──
# 从持仓期最高价回撤的百分比
TRAILING_STOP_PCT = -8.0            # 从高点回落 8% 触发
TRAILING_STOP_RATIO = 1.0 + TRAILING_STOP_PCT / 100.0  # 0.92

# ── 阶梯移动止盈 (Tiered Trailing Stop for evaluate_exit_signals) ──
# 盈利超过 20%: 允许从最高点回落 5%
TIER_HIGH_PROFIT_PCT = 20.0
TIER_HIGH_TRAIL_RATIO = 0.95  # 5% trail
# 盈利超过 10%: 允许从最高点回落 8%
TIER_MID_PROFIT_PCT = 10.0
TIER_MID_TRAIL_RATIO = 0.92  # 8% trail

# ── 保本机制 (Capital Protection) ──
# 曾经盈利超过此值后启动保本保护
CAPITAL_PROTECT_THRESHOLD_PCT = 5.0
# 保本底线：跌回至成本线 +1% 以内则触发
CAPITAL_PROTECT_FLOOR_PCT = 1.0

# ── 分级预警 (Tiered Early-Warning for Bark users) ──
# 上班族无法盯盘，依赖 Bark 推送。原逻辑只在跌破 -9% 止损线才预警，
# 导致 -3%~-9% 的恶化过程完全静默。分级预警在恶化早期就提醒：
#   -3% 轻度（留意）→ -5% 中度（建议减仓）→ -9% 紧急（止损线，已有逻辑）
EARLY_WARN_MILD_PCT = -3.0       # 轻度预警阈值
EARLY_WARN_MODERATE_PCT = -5.0   # 中度预警阈值
# 每级每天最多推一次（防 30 分钟一次的风控循环刷屏）。进程重启后重置。
EARLY_WARN_TIER_COOLDOWN_DAYS = 1

# ── 盘中急跌感知 (Intraday Plunge Detection) ──
# 改动 B1：风控 tick 间隔 30 分钟，急跌行情下可能错过盘中击穿止损线又反弹的
# 场景（上班族对此完全无感）。修复方案：
# 1. run_wind_control 读取当日最低价(low)，止损判定用 min(curr_price, low)，
#    只要盘中任一时刻击穿过止损线就触发，不被反弹掩盖。
# 2. 当任一持仓 stop_buffer 过小（贴近止损线）或弱市时，风控间隔从常规 30
#    分钟降到紧迫 10 分钟，缩短感知延迟。
WIND_CONTROL_INTERVAL_URGENT_MINUTES = 10
# 触发紧迫模式的 stop_buffer 阈值（当前价距止损线 < 此值则视为紧迫）
URGENT_STOP_BUFFER_PCT = 2.0

# ── 止盈目标 (Take Profit Target) ──
# 前端展示 & 推送消息中的固定止盈目标
TAKE_PROFIT_PCT = 15.0
TAKE_PROFIT_RATIO = 1.0 + TAKE_PROFIT_PCT / 100.0  # 1.15

# ── 分批止盈 (Scale-out / First Profit Take) ──
# 盈利达到此幅度时，先减仓锁定一半利润，剩余仓位继续用高档移动止损跟踪，
# 实现"让利润奔跑 + 分批落袋"。通过 remark 标记避免重复触发。
FIRST_PROFIT_TAKE_PCT = 8.0           # 盈利 +8% 触发首笔减仓
FIRST_PROFIT_TAKE_RATIO = 0.5         # 减仓 50%
# 重复减仓防护标记（写入 paper_trading.remark，含此标记则不再触发首笔止盈）
FIRST_PROFIT_TAKE_MARK = "已首笔止盈减仓50%"

# ── 时间止损 (Time Stop) ──
# 分层时间风控：先预警，再复核，最后才升级为确认平仓
TIME_STOP_DAYS = 5                 # legacy baseline, keep for compatibility
TIME_STOP_WARNING_DAYS = 5         # 5 个交易日未盈利：预警
TIME_STOP_REVIEW_DAYS = 7          # 7 个交易日未盈利：复核/减仓候选
TIME_STOP_FORCE_DAYS = 10          # 10 个交易日仍未盈利：确认平仓候选
TIME_STOP_REVIEW_LOSS_PCT = -2.0   # 复核档亏损加重阈值

# ── 回测引擎参数 (Backtest Engine Defaults) ──
BACKTEST_MAX_HOLD_DAYS = 10         # 最大持有天数 (从 5 改为 10，更适合均线粘合中线策略)
BACKTEST_TRAILING_ATR_MULT = 2.2    # ATR 移动止盈倍数
BACKTEST_RISK_PER_TRADE = 0.02      # 单笔交易最大风险占总资金比例 (2%)

# ── 量能见顶检测 (Volume Climax) ──
VOLUME_CLIMAX_MULTIPLIER = 3.0      # 成交量超过 MA20 的倍数阈值
