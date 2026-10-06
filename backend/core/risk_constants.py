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

# ── 股票池基础过滤 ──
# daily_k 的成交量统一为“手”；历史成交额缺失时用收盘价×成交量×100估算。
UNIVERSE_LIQUIDITY_LOOKBACK_DAYS = 5
UNIVERSE_MIN_AVG_AMOUNT_YUAN = 200_000_000.0
UNIVERSE_NEW_ONE_PRICE_MAX_DAYS = 4
MONTHLY_SECTOR_TOP_N = 5
MONTHLY_SECTOR_LEADERS_PER_SECTOR = 2

# 主要质量阈值用于执行门禁与连续质量分，不再生成字母评级。
# 价格行为与5日涨幅门槛来自2022-2026全市场逐日K线回放（8429个独立事件）。
# tv_dual 经白名单升格为可交易核心策略（与 tv_dual_strict 并列），但同等追高/否决硬门槛仍生效。
# 质量分线从70受控降至65；价格行为、否决与执行确认硬门槛保持不变。
#
# 5日涨幅采用渐进模型（消除"强势但未超涨"的逻辑死区）：
#   - SOP_A_GRADE_MAX_5D_GAIN_PCT(10%) 为软起扣点：超过后每涨1%扣 quality_score；
#   - SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT(25%) 为硬否决线：超过则不得评A。
SOP_A_GRADE_MIN_SCORE = 65.0
SOP_A_GRADE_STRATEGIES = ("tv_dual_strict", "tv_dual", "tv_zp")
SOP_A_GRADE_MIN_PRICE_ACTION_SCORE = 60.0
SOP_A_GRADE_MAX_5D_GAIN_PCT = 10.0        # 软起扣点（与 score_calibration.extension_penalty 复用，保持口径一致）
SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT = 25.0   # 硬否决线（>此值不得评A）
SOP_A_GRADE_5D_PENALTY_PER_PCT = 0.5      # 软区间(10-25%)每超1%扣 quality_score 的分值
SOP_A_GRADE_POLICY_VERSION = "kline-calibrated-v4-relaxed-score"
# 历史评级尚未形成 A>B>C 的稳定样本外单调性。ACTIVE 只能在校准报告同时通过
# Grade 单调性与 A 级政策验证后人工切换；默认不让评级本身新增实盘资格。
SOP_GRADE_EXECUTION_MODE = "SHADOW_ONLY"
SOP_A_GRADE_MIN_MATURE_SAMPLES = 30

# ── 交易门槛 v2（trade-gate-v2）──
# 背景：v1 下 ~41 项硬拦截叠加导致全年 TRADE 成熟样本仅 4 个，无法统计验证；
# 且 SOP 质量分与未来5日收益秩相关为负（-0.20 左右）。v2 把门槛收窄为
# "核心策略 + 无硬阻断 + PA READY + 站上确认价 + 收盘稳定"，弱条件全部降级为
# trade_cautions（扣分+缩仓），市场环境从一票否决改为仓位调节。
# 硬阻断仅保留：数据异常 / PA结构明确失效 / 风险>20% / 严重公告 /
# 确认价不可成交 / 板块明确退潮（强度不足不算退潮）。
# ── 信号源分层加权（signal-tier-weight-v1-shadow，2026-10-06）──
# 依据：regime_attribution 事件分层（132,681 事件三段 walk-forward，
# docs/research/WINRATE_BASELINES_AND_GATES_2026-10-06.md）——同门槛 PA≥60
# 下 A 层（MA+ZP 双确认）对 B 层（MA-only）期望优势三段稳定约 +1pt/笔
# （test 段 +0.75% vs -0.27%）。只影响 trade_opportunity_score 排序（±加分），
# 不改任何资格硬门槛；C 层（ZP-only）强年份依赖不加权。
# 转正：SHADOW 期 ≥3 个月 A/B 层实现收益差方向一致（validation_gate）。
SIGNAL_TIER_WEIGHT_ENABLED = os.getenv("SIGNAL_TIER_WEIGHT_ENABLED", "true").lower() == "true"
SIGNAL_TIER_A_BONUS = 4.0     # 双确认加分（机会分 0-100 内）
SIGNAL_TIER_B_PENALTY = 2.0   # MA-only 减分
SIGNAL_TIER_POLICY_VERSION = "signal-tier-weight-v1-shadow"

TRADE_GATE_POLICY_VERSION = "trade-gate-v2"
TRADE_GATE_V2_ENABLED = True  # 一行回滚：False 恢复 v1 全拦截行为

# ── 晋升硬前置（promotion gate，量化纪律审查 2026-09-30）──
# 任何 SHADOW/advisory 阈值转正（影响仓位/交易资格）前必须通过
# core.validation_gate.promotion_allowed()：≥3 个滚动样本外正期望窗口，
# 每窗口 ≥30 个独立样本。这是代码级强制门，替代此前的文档约定。
WALK_FORWARD_MIN_POSITIVE_WINDOWS = 3
WALK_FORWARD_MIN_SAMPLES_PER_WINDOW = 30

# ── 活阈值验证状态表（2026-09-30 盘点；行为保留，验证补齐前禁止加严/扩权）──
# PROMOTED=已过验证；PREREGISTERED=门槛已预注册未通过；PENDING=未经本库验证；
# EXTERNAL=外部依据（书籍/经验），方向为风控收紧。
# SECTOR_FUND_OUTFLOW_5D_YI=-10.0          PENDING（零依据直接砍资格，最优先补验证）
# REGIME_POSITION_MULTIPLIER DEFENSIVE/CRIT PREREGISTERED（gates_ready 未 PASS 即生效）
# AMP20_GATE 6% 分界                        PENDING（同段样本事后挑分界）
# TREND_PHASE_POSITION_MULTIPLIER           PENDING（同上）
# A_EOD 受控通道                            SHADOW（2026-09-30 降级，E3 未通过）
# OPPORTUNITY_GATE_MIN_SCORE=60             PENDING（自标 DIAGNOSTIC_ONLY 却是活门）
# BREADTH_DOWNGRADE_DEFENSIVE/CRITICAL      EXTERNAL（经验值联动 regime）
# MAX_CONSECUTIVE_LOSSES / MONTHLY_RISK_PCT EXTERNAL（书籍法则，纯风控收紧）
VALIDATION_STATUS_NOTE = "见 core/validation_gate.py PROMOTION_REGISTRY 与 promotion_allowed"

# 市场环境 → 仓位乘数（替换 market_blocked 一票否决）。
# CRITICAL 默认 0（禁止新仓），是否允许极强结构验证仓由 CRITICAL_TRIAL_ENABLED 决定。
REGIME_POSITION_MULTIPLIER = {"OFFENSIVE": 1.0, "DEFENSIVE": 0.5, "CRITICAL": 0.0}

# ── 连错熔断（loss-streak-breaker-v1，《交易之路》：连错3次必须休息）──
# 依据：模拟盘存在 9 笔亏损>=10% 的执行漏洞。最近连续亏损达 MAX_CONSECUTIVE_LOSSES
# 且最后一笔平仓在冷却期内时，暂停签发新 execution_intents（只挡新增指令，不影响
# 已签发意图的流转与平仓）。一行回滚：ENABLED=false。
LOSS_STREAK_BREAKER_ENABLED = os.getenv("LOSS_STREAK_BREAKER_ENABLED", "true").lower() == "true"
MAX_CONSECUTIVE_LOSSES = 3
LOSS_STREAK_COOLDOWN_DAYS = 1
LOSS_STREAK_POLICY_VERSION = "loss-streak-breaker-v1"

# ── 月度风险熔断（monthly-risk-breaker-v1，Elder《以交易为生》6% 法则）──
# 当月已实现净亏损 + 当前持仓资金风险，占虚拟总资金比例达到 MONTHLY_RISK_LIMIT_PCT
# 时，本月剩余时间硬熔断新开仓（不可 force 绕过）。与日内/浮亏熔断（快）互补：
# 日内熔断管"今天"，本熔断管"这个月"，防"亏钱后加大头寸救交易"的月度累积失控。
# 账户净值暂以 DEFAULT_RISK_BUDGET.virtual_total_capital 近似（paper_trading 无资金
# 字段，同 portfolio_risk 既有口径）。一行回滚：ENABLED=false。
MONTHLY_RISK_BREAKER_ENABLED = os.getenv("MONTHLY_RISK_BREAKER_ENABLED", "true").lower() == "true"
MONTHLY_RISK_LIMIT_PCT = 6.0
MONTHLY_RISK_POLICY_VERSION = "monthly-risk-breaker-v1"

# ── 波动率(振幅)闸门（amp20-gate-v1，《交易之路》波动率规则 + 本库90天分层验证）──
# 近90天点内样本：20日均振幅>=6% 的信号 5日 -3.50%/胜率35.5%，而 2-4% 档 +0.40%。
# 高波动=情绪过热/派发特征，仓位乘数下调（只缩不放，fail-open，数据缺失不惩罚）。
AMP20_GATE_ENABLED = os.getenv("AMP20_GATE_ENABLED", "true").lower() == "true"
AMP20_HIGH_THRESHOLD_PCT = 6.0
AMP20_GATE_MULTIPLIER = 0.5
AMP_GATE_POLICY_VERSION = "amp20-gate-v1"
# 强势股振幅骤降 → 阴跌预警（软约束：进 trade_cautions，走既有 ×0.5 通道）
AMP_COLLAPSE_DROP_RATIO = 0.6
AMP_COLLAPSE_NEAR_HIGH_PCT = 0.9
# 低振幅股骤增 → 变盘前兆观察（软约束）
AMP_SPIKE_RATIO = 1.8
AMP_SPIKE_BASE_MAX_PCT = 2.5
# 中途半端买点：现价高出突破触发价超过该比例视为追价（软约束）
ENTRY_CHASE_MAX_EXTENSION_PCT = 3.0

# ── 涨停情绪退潮观察（zt-sentiment-watch-v1，借鉴 easy-stock 超短情绪周期）──
# 趋势回调策略不参与连板，但炸板率（炸板家数/(涨停+炸板)）是全市场风险偏好的
# 领先温度计：>40% 即情绪退潮特征，所有策略的胜率环境都会被拖累。当前仅作
# 盘前 Bark 功课行的"降暴露"提示（advisory），不改仓位/资格——与 AMP20 闸门
# 不同，本阈值未经本库点内样本回测验证，先观察积累数据再决定是否入策略。
# 数据源：limit_up_events（collect_limit_up_leadership 盘中逐分钟落库）。
ZT_EBB_BROKEN_RATE_PCT = 40.0
ZT_SENTIMENT_POLICY_VERSION = "zt-sentiment-watch-v1"

# ── 道氏趋势阶段仓位约束（trend-phase-gate-v1）──
# 道氏三阶段视角：衰竭段(CLIMAX/上轨过冲/楔形)与加速段属于公众参与后期/派发特征，
# 高位追价期望为负。近 90 天点内样本：PA>=80 5日 -5.56%、SOP A 级 -7.47%、
# 强多头趋势K -1.67%，而内包K/回踩企稳层为正。命中下列 pa_trend_phase 时仓位乘数
# 下调（只缩不放，不禁止、不改 trade_eligible/trade_bucket）。
# pa_trend_phase 缺失或其它阶段不惩罚（fail-open）。一行回滚：ENABLED=False。
TREND_PHASE_POSITION_MULTIPLIER_ENABLED = os.getenv(
    "TREND_PHASE_POSITION_MULTIPLIER_ENABLED", "true"
).lower() == "true"
TREND_PHASE_POSITION_MULTIPLIER = {
    "衰竭段": 0.5,
    "加速段": 0.5,
}
TREND_PHASE_GATE_POLICY_VERSION = "trend-phase-gate-v1"

# 市场风控禁新仓时，推荐推送不再静默：降级为"参考版"（照常列出策略候选+被挡原因+
# 市场概况，明确标注不可下单）。只影响 Bark 文案，不改变 trade_eligible/trade_bucket
# 与执行侧闸门。一行回滚：False 恢复"无可交易候选即不推送"。
REGIME_REFERENCE_PUSH_ENABLED = True
REFERENCE_PUSH_MAX_STOCKS = 5

# 板块资金结构降权（"势不对时形态失效"的板块级实现）：本可通过全部闸门的候选，
# 若其行业主力资金5日净流出超过阈值（亿元），降级为观察。数据缺失（东财接口降级）
# 时不降权（fail-open）。一行回滚：False 关闭该降权。
SECTOR_FUND_OUTFLOW_DEMOTE_ENABLED = True
SECTOR_FUND_OUTFLOW_5D_YI = -10.0

# CRITICAL 验证仓：默认关闭，须先通过 /api/review/trade-gate-readiness 的
# 独立样本验证（每市场状态≥30成熟样本、PF>1.2）再人工开启。
CRITICAL_TRIAL_ENABLED = False
CRITICAL_TRIAL_POLICY_VERSION = "critical-trial-v1"
CRITICAL_TRIAL_POSITION_PCT = 2.5
CRITICAL_TRIAL_PORTFOLIO_CAP_PCT = 6.0
CRITICAL_TRIAL_MAX_POSITIONS = 3
CRITICAL_TRIAL_MIN_QUALITY_SCORE = 75.0

# 门槛就绪度报告的验证标准（用户确认的上线门槛）。
TRADE_GATE_MIN_MATURE_SAMPLES_PER_REGIME = 30
TRADE_GATE_MIN_PROFIT_FACTOR = 1.2
TRADE_GATE_MIN_SCORE_CORRELATION = 0.0

# 决策层硬编码阈值常量化（行为不变）。
OPPORTUNITY_GATE_MIN_SCORE = 60.0
SENTIMENT_STAGE_CAPS = {
    "ICE": 15, "RETREAT": 10, "V_REPAIR": 30, "DIVERGENCE": 40,
    "CLIMAX": 50, "ADVANCE": 70, "REPAIR": 40, "REPAIR_CRITICAL": 30,
}

# v2 软条件标记：阻断文本命中任一子串时降级为 trade_cautions（不拦截TRADE）。
# 硬阻断（数据异常/结构失效/风险>20%/严重公告/不可成交/板块明确退潮）不在此列。
TRADE_GATE_V2_SOFT_CONDITION_MARKERS = (
    "ZP单信号",                      # 单信号类：由 TV 分层风险单元(0.25)缩仓而非禁入
    "MA单信号仅进攻市场",            # 非进攻市场：由 regime 仓位乘数调节
    "板块强度",                      # 强度不足≠明确退潮（50-70 与 <50 一并降级扣分）
    "个股适配不足",
    "强板块后排",
    "板块联动",                      # 含 revival 的"板块联动不足"
    "周线",                          # 周线中性/交易区间
    "震荡观察胜率偏低",
    "等待更优买点",                  # 结构风险 16-20% 档
    "量能未确认",                    # 尚未完全放量（收盘稳定与站上确认价仍为硬条件）
    "大市值低换手", "高市值换手不足", "小市值弹性票", "超大市值换手不足",
    "资金流数据缺失", "主力资金流出",
    "资本事件",                      # 定增/解禁/减持（严重公告类仍走地雷预警硬阻断）
    "涨幅偏高且质量未确认",
    "当日强度不足", "冲高回落风险", "未站稳历史/今日确认价", "交易区间上沿不追价",  # revival 确认类
    "筹码峰迁移不利",
)
# 任一软条件存在时，把基础仓位降为一半；条件数量继续通过 final_trade_score 排序，
# 避免多个相关提醒重复乘法把仓位压到不可执行。
TRADE_CAUTION_POSITION_MULTIPLIER = 0.5
# v2 下共振不再作为 TRADE 硬合取项，转为机会分加分。
TRADE_GATE_RESONANCE_BONUS = 4.0

# ── 受控试仓 ──
# 仅为已完成价量确认的高质量候选提供小仓验证入口。
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

# ── 尾盘校准受控通道 ──
# 2022-05-12~2026-08-04 全市场点时K线回放中，严格双共振 + PA>=60
# + 5日涨幅<=10% 是唯一在开发/验证/研究三段均保持正平均收益和 PF>1 的门槛。
# 该通道只软化重复的板块/周线执行阻断，不放宽确认价、追高、涨停和结构失效门禁。
#
# 2026-09-30 降级 SHADOW（量化纪律审查 4-3）：该门槛是从多个 gate 配置里按
# validation 段收益最大化挑出的"唯一幸存者"，且自家 E3 前推走查自认未执行
# （a_grade_kline_replay e3_blockers: E3_NOT_REACHED）——多重检验幸存偏差未
# 排除。SHADOW 模式保留资格判定与打标（继续积累点内对照样本），但仓位归零、
# 不签发执行意图；E3（≥3 个滚动样本外正期望窗口）通过后置回 true。
A_EOD_CONTROLLED_ENABLED = os.getenv("A_EOD_CONTROLLED_ENABLED", "false").lower() == "true"
A_EOD_CONTROLLED_POLICY_VERSION = "a-eod-controlled-trial-v1-shadow"
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
PA_LONG_LOWER_WICK_MIN_REBOUND_PCT = 4.0

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

# ── 书中规则回测对照（不直接改变生产持仓）──
BACKTEST_STAGED_INITIAL_RATIO = 0.5
BACKTEST_STAGED_ADD_MAX_DAYS = 2
BACKTEST_HALF_PEAK_TRIGGER_PCT = 10.0
BACKTEST_HALF_PEAK_RETAIN_RATIO = 0.5

# 涨停次日若收盘涨幅不超过 1%，视为没有顺势确认，短期仓优先退出。
LIMIT_UP_NEXT_DAY_MIN_FOLLOW_THROUGH_PCT = 1.0

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

# ── Price Action v9 结构阈值 ──
# 回撤深度只参与质量分层；H2/L2 本身按第二次恢复趋势的价格尝试定义。
PA_SECOND_ENTRY_NOISE_PCT = 0.0005
PA_FOLLOW_THROUGH_FAIL_ATR = 0.10
PA_FOLLOW_THROUGH_STRONG_ATR = 0.50
PA_FOLLOW_THROUGH_FAILED_SCORE_DELTA = -12
PA_FOLLOW_THROUGH_WEAK_SCORE_DELTA = -4
PA_FOLLOW_THROUGH_STRONG_SCORE_DELTA = 8
PA_SR_ZONE_ATR = 0.35
PA_SR_ZONE_PRICE_PCT = 0.005
PA_MTR_PRIOR_MOVE_ATR = 2.0
PA_MTR_PRIOR_MOVE_PCT = 0.03
PA_MTR_RETEST_ATR = 1.0
PA_CLIMAX_EXTENSION_ATR = 2.8
PA_CLIMAX_RANGE_MULTIPLIER = 1.8
PA_INTRADAY_GAP_THRESHOLD_PCT = 0.3
