"""
Alpha Vision - Risk Management Constants (统一风控参数配置)

All stop-loss, take-profit, and risk control thresholds are defined here
as the single source of truth. Any layer (backtest, real-time alerts,
wind control) should import from this module instead of hardcoding values.
"""
import os


# ── 候选证据质量闸门 ──
# OFF: 不计算；SHADOW: 只记录不改变生产权限；ENFORCED: A/B 才允许维持现有交易权限。
# 默认 SHADOW，必须通过点时验证并经人工批准后才允许切换 ENFORCED。
EVIDENCE_GATE_MODE = os.getenv("EVIDENCE_GATE_MODE", "SHADOW").upper()

# 正式 TV 买入关系：均线 B 或 TV-ZP long 任一命中。
# tv_dual_strict 仅保留用于历史兼容和对照研究，不再作为生产默认。
PRIMARY_TV_STRATEGY = "tv_dual"
MA_STRATEGY_TAKE_PROFIT_PCT = 15.0
TV_EXECUTION_POLICY_VERSION = "tv-or-tiered-v1"
TV_EXECUTION_TIER_RISK_UNITS = {"A": 1.0, "B": 0.6, "C": 0.25}
TV_MA_ONLY_MIN_PA_SCORE = 60.0
ZP_PROFIT_PROTECT_TRIGGER_PCT = 15.0

# A级结构只授予 TV 或策略命中、没有SOP否决项、价格结构合格且未明显追高的候选。
# 价格行为与5日涨幅门槛来自2022-2026全市场逐日K线回放（8429个独立事件）。
# tv_dual 经白名单升格为可交易核心策略（与 tv_dual_strict 并列），但同等追高/否决硬门槛仍生效。
# 质量分线从70受控降至65；价格行为、否决与执行确认硬门槛保持不变。
#
# 5日涨幅采用渐进模型（消除"强势但未超涨"的逻辑死区）：
#   - SOP_A_GRADE_MAX_5D_GAIN_PCT(10%) 为软起扣点：超过后每涨1%扣 quality_score；
#   - SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT(25%) 为硬否决线：超过则不得评A。
SOP_A_GRADE_MIN_SCORE = 65.0
SOP_A_GRADE_STRATEGIES = ("tv_dual_strict", "tv_dual")
SOP_A_GRADE_MIN_PRICE_ACTION_SCORE = 60.0
SOP_A_GRADE_MAX_5D_GAIN_PCT = 10.0        # 软起扣点（与 score_calibration.extension_penalty 复用，保持口径一致）
SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT = 25.0   # 硬否决线（>此值不得评A）
SOP_A_GRADE_5D_PENALTY_PER_PCT = 0.5      # 软区间(10-25%)每超1%扣 quality_score 的分值
SOP_A_GRADE_POLICY_VERSION = "kline-calibrated-v4-relaxed-score"
# 历史评级尚未形成 A>B>C 的稳定样本外单调性。ACTIVE 只能在校准报告同时通过
# Grade 单调性与 A 级政策验证后人工切换；默认不让评级本身新增实盘资格。
SOP_GRADE_EXECUTION_MODE = "SHADOW_ONLY"
SOP_A_GRADE_MIN_MATURE_SAMPLES = 30

# ── A-受控试仓 ──
# 正式A级标准保持不变；A-只为已完成价量确认的高质量B级提供小仓验证入口。
A_MINUS_TRIAL_POLICY_VERSION = "a-minus-controlled-trial-v1"
A_MINUS_TRIAL_MIN_QUALITY_SCORE = 60.0
A_MINUS_TRIAL_MIN_PRICE_ACTION_SCORE = 70.0
A_MINUS_TRIAL_MIN_RISK_REWARD = 2.0
A_MINUS_TRIAL_MAX_DAILY_RISE_PCT = 7.0
A_MINUS_TRIAL_POSITION_PCT = 5.0
A_MINUS_TRIAL_PORTFOLIO_CAP_PCT = 10.0
A_MINUS_TRIAL_MIN_MATURE_SAMPLES = 30
A_MINUS_TRIAL_PROMOTION_MIN_AVG_RETURN = 0.8
A_MINUS_TRIAL_PROMOTION_MIN_PROFIT_FACTOR = 1.3
A_MINUS_TRIAL_ROUND_TRIP_COST_PCT = 0.15

# ── A-EOD 校准受控通道 ──
# 2022-05-12~2026-08-04 全市场点时K线回放中，严格双共振 + PA>=60
# + 5日涨幅<=10% 是唯一在开发/验证/研究三段均保持正平均收益和 PF>1 的门槛。
# 该通道只软化重复的板块/周线执行阻断，不放宽确认价、追高、涨停和结构失效门禁。
A_EOD_CONTROLLED_POLICY_VERSION = "a-eod-controlled-trial-v1"
A_EOD_MIN_QUALITY_SCORE = 60.0
A_EOD_MIN_PRICE_ACTION_SCORE = 60.0
A_EOD_MIN_RISK_REWARD = 1.5
A_EOD_MAX_5D_GAIN_PCT = 10.0
A_EOD_MAX_ENTRY_EXTENSION_PCT = 3.0
A_EOD_POSITION_PCT = 5.0
A_EOD_PORTFOLIO_CAP_PCT = 15.0
A_EOD_MAX_CONCURRENT_POSITIONS = 3

# A-EOD signal-day candidates remain non-tradable. Only a valid next-session
# price confirmation may create an execution intent, with deliberately small exposure.
A_EOD_T1_POLICY_VERSION = "a-eod-t1-confirmation-v1"
A_EOD_T1_POSITION_PCT = 2.0
A_EOD_T1_PORTFOLIO_CAP_PCT = 6.0
A_EOD_T1_MAX_POSITIONS = 3

# 价格行为只管理已入选股票的执行权限，不参与股票发现。
PA_EXECUTION_NORMAL_MIN_SCORE = 70.0
PA_EXECUTION_T1_MIN_SCORE = A_EOD_MIN_PRICE_ACTION_SCORE
PA_PULLBACK_WATCH_MIN_SCORE = 50.0

# ── 放量突破后的缩量回踩 ──
# 仅作为价格行为执行质量因子：不独立产生股票、不绕过 TV 信号与风控门禁。
PA_VOLUME_PULLBACK_RESISTANCE_LOOKBACK = 20
PA_VOLUME_PULLBACK_MAX_SESSIONS = 10
PA_VOLUME_PULLBACK_CONFIRM_MAX_SESSIONS = 5
PA_VOLUME_BREAKOUT_MIN_VOLUME_RATIO = 1.35
PA_VOLUME_BREAKOUT_MIN_BODY_RATIO = 0.45
PA_VOLUME_BREAKOUT_MIN_CLOSE_POSITION = 0.65
PA_VOLUME_BREAKOUT_BUFFER_PCT = 0.003
PA_VOLUME_PULLBACK_MAX_BREAKOUT_VOLUME_RATIO = 0.75
PA_VOLUME_PULLBACK_MAX_AVG_VOLUME_RATIO = 0.90
PA_VOLUME_PULLBACK_MAX_RHYTHM_RATIO = 0.60
PA_VOLUME_PULLBACK_SUPPORT_TOLERANCE_PCT = 0.02
PA_VOLUME_PULLBACK_CONFIRM_SCORE_DELTA = 8
PA_VOLUME_PULLBACK_FORMING_SCORE_DELTA = 3
PA_VOLUME_PULLBACK_WEAK_SCORE_DELTA = -3
PA_VOLUME_PULLBACK_INVALID_SCORE_DELTA = -10

# ── 强势例外影子验证与涨停可达性 ──
# 只生成反事实样本，不提升生产交易权限。
STRONG_EXCEPTION_MIN_OPPORTUNITY_SCORE = 60.0
STRONG_EXCEPTION_MIN_RISK_REWARD = 1.5
LIMIT_PRICE_TOLERANCE = 0.01

# 连续推送的高质量 B 级只进入影子验证，不改变生产交易权限。
PERSISTENT_B_SHADOW_LOOKBACK_DAYS = 10
PERSISTENT_B_SHADOW_MIN_PUSH_DAYS = 2
PERSISTENT_B_SHADOW_MIN_QUALITY_SCORE = 60.0
PERSISTENT_B_SHADOW_MIN_SECTOR_ALIGNMENT = 80.0
PERSISTENT_B_SHADOW_MAX_DAILY_RISE_PCT = 7.0

# 点时行情完整性：少量缺失按股票隔离，只有覆盖明显不足才降为全局研究模式。
POINT_IN_TIME_CORE_FIELD_MIN_COVERAGE = 0.995
POINT_IN_TIME_FILTER_FIELD_MIN_COVERAGE = 0.98

# V 型强修复识别：只改变市场解释和观察入口，不直接提升交易权限。
V_REPAIR_MIN_ADVANCE_RATIO = 70.0
V_REPAIR_MIN_STRONG_RATIO = 8.0
V_REPAIR_MIN_BREADTH_IMPROVEMENT = 20.0

# ── 成长板块结构性强修复 ──
# 全市场仍在 EMA20 下方时，创业板/科创板可能先于宽基指数形成独立修复。
# 该状态只把对应板块的市场评分恢复到 DEFENSIVE，不绕过交易确认与追高限制。
STRUCTURAL_REPAIR_MIN_SEGMENT_COUNT = 100
STRUCTURAL_REPAIR_MIN_ADVANCE_RATIO = 70.0
STRUCTURAL_REPAIR_MIN_STRONG_RATIO = 15.0
STRUCTURAL_REPAIR_MIN_AVG_RETURN_PCT = 1.5
STRUCTURAL_REPAIR_MAX_WEAK_RATIO = 3.0

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

# ── 保本移动止损 (Breakeven Trailing) ──
# 改动 #14：原保本机制门槛是 +5%，利润区间 [0%, 5%) 没有任何止损上移，
# 导致股票从 +4% 回撤到 -9% 会损失 13%、浮盈全部回吐。这里补一个更早触发的
# 保本档：浮盈达 +3% 即把止损上移到成本线附近（-0.5%），守住绝大部分本金。
# 档位优先级：保本移动(3%) < 保本保护(5%) < 移动风控(10%) < 强盈利收紧(20%)。
# 因 active_stop = max(candidates)，更高档自动覆盖低档，符合"只收紧不放宽"语义。
BREAKEVEN_TRIGGER_PCT = 3.0       # 浮盈达 +3% 触发保本移动
BREAKEVEN_FLOOR_PCT = -0.5        # 保本底线：成本 -0.5%（略低于成本，避免被分时噪音扫出）

# ── 持仓追高保护 (Position Spike Protection) ──
# 改动 #13 持仓侧：已持有的票当日冲高(涨幅>7%)但已从高点回落时，收紧止损锁定脉冲利润，
# 避免"冲高 → 全回吐"。只在 pl_pct（当前浮盈）满足且已确认回落（high>curr*1.01）时触发。
POSITION_SPIKE_PCT = 7.0          # 当前浮盈 >7% 视为脉冲冲高
POSITION_SPIKE_TRAIL_RATIO = 0.97 # 冲高回撤时止损收紧到现价 -3%

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

# ── 强势股减仓豁免 (Strong Stock Scale-out Exemption) ──
# 改动 B3：机械 +8% 减半仓会砍掉主升浪牛股的进攻性。当板块处于主升早期且
# 个股收盘强势时，首笔止盈线从 +8% 上抬到 +12%，让利润多跑一段。
STRONG_PROFIT_TAKE_PCT = 12.0         # 强势股首笔止盈线
STRONG_SECTOR_PHASES = ("SECTOR_EARLY", "SECTOR_CONFIRM")  # 触发豁免的板块阶段
STRONG_CLOSE_POSITION_THRESHOLD = 0.6  # 收盘强势度阈值（close_position >= 此值才算强势）

# ── 时间止损 (Time Stop) ──
# 分层时间风控：先预警，再复核，最后才升级为确认平仓
TIME_STOP_DAYS = 5                 # legacy baseline, keep for compatibility
TIME_STOP_WARNING_DAYS = 5         # 5 个交易日未盈利：预警
TIME_STOP_REVIEW_DAYS = 7          # 7 个交易日未盈利：复核/减仓候选
TIME_STOP_FORCE_DAYS = 10          # 10 个交易日仍未盈利：确认平仓候选
TIME_STOP_REVIEW_LOSS_PCT = -2.0   # 复核档亏损加重阈值
# 改动 B4：盈利豁免阈值。原逻辑 pl_pct > 0 就完全跳过时间止损，导致 +0.5% 横盘
# 20 天的僵尸仓无人管（占用仓位上限、消耗机会成本）。改为 pl_pct > 此值才豁免，
# 2% 以下都算"未达预期"，仍受时间止损约束。
TIME_STOP_PROFIT_EXEMPT_PCT = 2.0
# 改动 B4：微盈震荡仓的复核减仓比例（review 档 pl_pct∈(0,2%] 时减此比例）
TIME_STOP_REVIEW_REDUCE_RATIO = 1.0 / 3.0

# ── 混合退出策略：信号反转卖出 ──
# 在保留 -9%止损 / +8%减仓 / 10天时间止损 的基础上，新增"趋势反转清仓"退出。
# 当 price_action_regime 判定为以下状态时，视为趋势反转 → 清仓剩余仓位。
# 默认关闭，通过 DB key 'signal_reverse_sell_enabled' 运行时覆盖（热改）。
SIGNAL_REVERSE_SELL_ENABLED = False  # 默认关闭，安全上线
SIGNAL_REVERSE_REGIMES = ("空头趋势", "向下破位")  # price_action_regime 命中即视为趋势反转

# ── 回测引擎参数 (Backtest Engine Defaults) ──
BACKTEST_MAX_HOLD_DAYS = 10         # 最大持有天数 (从 5 改为 10，更适合均线粘合中线策略)
BACKTEST_TRAILING_ATR_MULT = 2.2    # ATR 移动止盈倍数
BACKTEST_RISK_PER_TRADE = 0.02      # 单笔交易最大风险占总资金比例 (2%)
# 改动：回测止损必须与实盘 FIXED_STOP_LOSS_PCT 一致，否则历史胜率反映的不是真实交易规则。
# 历史代码硬编码 -8.0，而实盘止损是 -9.0，导致回测胜率系统性失真（更早止损=更多假亏损）。
BACKTEST_STOP_LOSS_PCT = FIXED_STOP_LOSS_PCT  # -9.0，与实盘硬止损同源

# ── 事件驱动首次回踩试仓 ──
# 仅在官方重大催化后的首次健康回踩完成量价确认时开放；这是仓位上限，不是绕过价格风控。
EVENT_TRIAL_MIN_RISK_REWARD = 2.0
EVENT_TRIAL_MAX_DAILY_PCT = 6.0
EVENT_TRIAL_POSITION_PCT = 3
EVENT_TRIAL_WEAK_POSITION_PCT = 2

# 单笔组合风险预算：仓位比例 × 结构止损距离不得高于总资金的 1%。
MAX_PORTFOLIO_RISK_PER_TRADE_PCT = 1.0

# ── 量能见顶检测 (Volume Climax) ──
VOLUME_CLIMAX_MULTIPLIER = 3.0      # 成交量超过 MA20 的倍数阈值

# ── 市场宽度降级 (Breadth-based Regime Downgrade) ──
# get_market_regime 只看宽基指数 vs EMA20 的趋势，对"指数被权重股托住但个股
# 大面积跌停"的结构性行情失明（如 2026-06-23：上证仅 -1.37% 但 39 家跌停）。
# 当跌停家数达到阈值时，强制把指数判定的 OFFENSIVE 降级，联动收紧扫描/降仓。
# 降级规则（只降不升）：
#   跌停 >= BREADTH_DOWNGRADE_CRITICAL  → CRITICAL（极端恐慌，空仓防守）
#   跌停 >= BREADTH_DOWNGRADE_DEFENSIVE → DEFENSIVE（跌停潮，减仓观望）
# 取值依据：A 股常态跌停 5~15 家，>20 家即为结构恶化，>40 家为恐慌扩散。
BREADTH_DOWNGRADE_DEFENSIVE = 20    # 跌停家数 >= 此值：OFFENSIVE → DEFENSIVE
BREADTH_DOWNGRADE_CRITICAL = 40     # 跌停家数 >= 此值：直接 → CRITICAL

# ── 行情数据新鲜度 (Snapshot Freshness for Bark push) ──
# 解决"Bark 推送用过期数据/被封后断数据"两大痛点：
# 1. P0：所有实时源失败时，退回最近一次成功的过期快照而非空 DF，避免推送空白。
# 2. P1：在 Bark 推送 body 标注行情时间 + 源，让用户一眼看出这价是几秒/几分钟前的。
# stale 兜底虽牺牲实时性，但比"断数据"对上班族盯盘更友好（有价可看 > 没价报错）。
STALE_SNAPSHOT_WARN = "过期快照(可能滞后)"  # stale 兜底分支的 source 标记，P1 据此加 ⚠️
FRESHNESS_WARN_THRESHOLD_MIN = 5    # 行情滞后超过此分钟数，Bark 推送前置 ⚠️ 提醒

# ── TradingView 信号预热窗口 ──
# 图表、单股分析和全市场扫描必须使用同一段历史预热数据。TV-ZP 的
# Alternate Signal 是有状态计算，历史起点不同会导致同一天的 long 标记不一致。
TV_SIGNAL_WARMUP_DAYS = 1000
