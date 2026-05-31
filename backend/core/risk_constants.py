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

# ── 止盈目标 (Take Profit Target) ──
# 前端展示 & 推送消息中的固定止盈目标
TAKE_PROFIT_PCT = 15.0
TAKE_PROFIT_RATIO = 1.0 + TAKE_PROFIT_PCT / 100.0  # 1.15

# ── 时间止损 (Time Stop) ──
# 持仓超过 N 天且未盈利，自动平仓
TIME_STOP_DAYS = 5

# ── 回测引擎参数 (Backtest Engine Defaults) ──
BACKTEST_MAX_HOLD_DAYS = 10         # 最大持有天数 (从 5 改为 10，更适合均线粘合中线策略)
BACKTEST_TRAILING_ATR_MULT = 2.2    # ATR 移动止盈倍数
BACKTEST_RISK_PER_TRADE = 0.02      # 单笔交易最大风险占总资金比例 (2%)

# ── 量能见顶检测 (Volume Climax) ──
VOLUME_CLIMAX_MULTIPLIER = 3.0      # 成交量超过 MA20 的倍数阈值
