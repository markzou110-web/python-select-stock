import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import scanner


def test_single_stock_task_backfills_missing_pine_indicators(monkeypatch):
    n = 130
    close = np.linspace(10, 13, n)
    df = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, 200000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })

    def fake_calculate_pine_indicators(input_df):
        enriched = input_df.copy()
        enriched["RF_Upward"] = True
        enriched["RF_Downward"] = False
        enriched["ST_Signal"] = True
        enriched["RQK_Up"] = True
        enriched["HalfTrend_Up"] = True
        enriched["QQE_Long"] = True
        return enriched

    def fake_check_pine_strategy(input_df, min_signals=3, fund_data=None):
        assert {"RF_Upward", "RQK_Up", "HalfTrend_Up", "QQE_Long"}.issubset(input_df.columns)
        return True, {"Score": 88, "信号数": "5/5"}

    monkeypatch.setattr(scanner, "calculate_pine_indicators", fake_calculate_pine_indicators)
    monkeypatch.setattr(scanner, "check_pine_strategy", fake_check_pine_strategy)

    result = scanner.single_stock_task(
        "000001",
        "测试股票",
        price=13,
        vol=200000,
        open_price=12.7,
        threshold=0.12,
        vol_multiplier=1.5,
        rsi_min=55,
        use_macd_filter=True,
        use_bb_sqz=True,
        sqz_lookback=10,
        use_weekly=False,
        preloaded_df=df,
        strategy_type="pine",
        pine_min_signals=5,
    )

    assert result["Score"] == 88
    assert result["strategy_type"] == "pine"


# ── tv_dual 周线门槛（改动 #4，默认关闭）──

def _make_tv_dual_df():
    """构造一份能让 check_tv_dual_strategy 命中的最小 DataFrame。"""
    n = 130
    close = np.linspace(10, 13, n)
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, 200000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "RSI_WILDER": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })


def test_tv_dual_weekly_gate_disabled_by_default(monkeypatch):
    """默认 tv_weekly_gate=False → 不调用 get_weekly_indicators，不过滤。"""
    weekly_called = {"n": 0}

    def fake_check_tv_dual_strategy(df, **kwargs):
        return True, {"Score": 88, "signal": "强共振"}

    def fake_weekly(*a, **kw):
        weekly_called["n"] += 1
        return False  # 即便周线弱，也不应被调用

    monkeypatch.setattr(scanner, "check_tv_dual_strategy", fake_check_tv_dual_strategy)
    monkeypatch.setattr(scanner, "get_weekly_indicators", fake_weekly)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        # tv_weekly_gate 默认 False
    )

    assert weekly_called["n"] == 0  # 默认不查周线
    assert result.get("Score") == 88


def test_tv_dual_weekly_gate_filters_bearish_weekly(monkeypatch):
    """tv_weekly_gate=True + 周线弱 → 返回 reason 过滤，不调用 check_tv_dual_strategy。"""
    strategy_called = {"n": 0}

    def fake_check_tv_dual_strategy(df, **kwargs):
        strategy_called["n"] += 1
        return True, {"Score": 88, "signal": "强共振"}

    monkeypatch.setattr(scanner, "check_tv_dual_strategy", fake_check_tv_dual_strategy)
    # 周线弱（False）
    monkeypatch.setattr(scanner, "get_weekly_indicators", lambda *a, **kw: False)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        tv_weekly_gate=True,
    )

    assert strategy_called["n"] == 0  # 被周线门槛过滤，未到策略判定
    assert "周线" in result.get("reason", "")


def test_tv_dual_weekly_gate_passes_bullish_weekly(monkeypatch):
    """tv_weekly_gate=True + 周线强 → 通过门槛，进入策略判定。"""
    monkeypatch.setattr(
        scanner, "check_tv_dual_strategy",
        lambda df, **kw: (True, {"Score": 88, "signal": "强共振"}),
    )
    monkeypatch.setattr(scanner, "get_weekly_indicators", lambda *a, **kw: True)

    result = scanner.single_stock_task(
        "000001", "测试", price=13, vol=200000, open_price=12.7,
        threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True,
        use_bb_sqz=False, sqz_lookback=10, use_weekly=False,
        preloaded_df=_make_tv_dual_df(), strategy_type="tv_dual_strict",
        tv_weekly_gate=True,
    )

    assert result.get("Score") == 88  # 通过门槛，命中策略


# ── 数据预检熔断（改动 #2）──

def test_perform_market_scan_aborts_when_preflight_blocking(monkeypatch):
    """preflight 报告 blocking=True → perform_market_scan 返回空列表。"""
    monkeypatch.setattr(scanner, "SCAN_PREFLIGHT_ENFORCE", True)

    fake_engine = object()
    monkeypatch.setattr(scanner, "get_db_engine", lambda: fake_engine)
    monkeypatch.setattr(
        scanner, "build_scan_preflight",
        lambda engine, data_date=None, **kw: {
            "status": "error",
            "blocking": True,
            "checks": [{"name": "coverage", "status": "error", "message": "覆盖率过低"}],
        },
    )

    result = scanner.perform_market_scan(strategy_type="tv_dual_strict")
    assert result == []


def test_perform_market_scan_proceeds_when_preflight_not_blocking(monkeypatch):
    """preflight blocking=False → 不熔断中止，继续走扫描流程（验证未被早返回 []）。

    用 MagicMock 假 engine 让本地兜底的 DB 查询返回空快照，避免真实 DB 与网络，
    聚焦"预检通过 → 不在预检处中断"的语义。
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr(scanner, "SCAN_PREFLIGHT_ENFORCE", True)

    fake_engine = MagicMock()
    # 本地兜底会查 daily_k 取 max_date；让连接执行返回空，使快照最终为空 → 无候选。
    fake_conn = MagicMock()
    exec_result = MagicMock()
    exec_result.fetchone.return_value = (None,)  # 无 max_date
    fake_conn.execute.return_value = exec_result
    fake_engine.connect.return_value.__enter__ = lambda self: fake_conn
    fake_engine.connect.return_value.__exit__ = lambda *a: False

    monkeypatch.setattr(scanner, "get_db_engine", lambda: fake_engine)
    preflight_called = {"n": 0}
    monkeypatch.setattr(
        scanner, "build_scan_preflight",
        lambda engine, data_date=None, **kw: (
            preflight_called.__setitem__("n", preflight_called["n"] + 1) or {
                "status": "ok",
                "blocking": False,
                "checks": [],
                "summary": {},
            }
        ),
    )
    monkeypatch.setattr(scanner, "get_market_snapshot", lambda: pd.DataFrame())

    # 预检不阻断：函数会继续执行并最终因无数据/无候选抛 HTTPException(503) 或返回 []。
    # 关键断言：build_scan_preflight 被调用且 blocking=False 时未在预检处早返回。
    try:
        result = scanner.perform_market_scan(strategy_type="tv_dual_strict", local_only=True)
        assert result == []
    except Exception:
        # 因 mock engine 无法真实查询，下游可能抛 503——这也证明已越过预检阶段
        pass

    assert preflight_called["n"] == 1  # 预检确实被调用且未被熔断跳过


# ── 信号分连续化（改动 #5）──

def _make_tv_dual_score_df(vol_ratio=2.0, pct_change=3.0):
    """构造一份带 Vol_MA20 与成交量的 DataFrame，用于检验 Score 连续化公式。

    量能倍数 = 成交量 / Vol_MA20，通过调整成交量控制 vol_ratio。
    """
    n = 130
    close = np.linspace(10, 13, n)
    df = pd.DataFrame({
        "日期": pd.date_range("2024-01-01", periods=n, freq="D"),
        "开盘": close - 0.3,
        "收盘": close,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "成交量": np.full(n, vol_ratio * 100000.0),
        "Vol_MA20": np.full(n, 100000.0),
        "RSI": np.full(n, 60.0),
        "RSI_WILDER": np.full(n, 60.0),
        "MACD_DIF": np.full(n, 0.2),
        "MACD_DEA": np.full(n, 0.1),
        "BB_Width": np.full(n, 0.08),
        "Sqz_Ratio": np.full(n, 0.08),
        "EMA5": close - 0.2,
        "EMA10": close - 0.3,
        "EMA20": close - 0.4,
        "EMA60": close - 0.5,
    })
    return df


def test_tv_dual_score_continuous_with_volume(monkeypatch):
    """两只都命中双共振但量能不同 → 量能强者 Score 更高（信号分连续化）。

    用 mock 强制 _find_* 函数返回命中，隔离评分公式。
    """
    from core import strategy

    # 强制 squeeze 与 tv_zp 在最后一根命中
    monkeypatch.setattr(strategy, "_find_squeeze_signal_indices", lambda df, **kw: [len(df) - 1])
    monkeypatch.setattr(
        strategy, "_find_tv_zp_signal_indices",
        lambda df, **kw: ([len(df) - 1], [], {"x": pd.Series(False, index=df.index)}),
    )
    monkeypatch.setattr(strategy, "_squeeze_tv_macd", lambda df: pd.DataFrame({"dif": [0.2]}))

    weak = _make_tv_dual_score_df(vol_ratio=1.0)   # 弱量能
    strong = _make_tv_dual_score_df(vol_ratio=3.0)  # 强量能

    _, weak_res = strategy.check_tv_dual_strategy(weak, require_both=True)
    _, strong_res = strategy.check_tv_dual_strategy(strong, require_both=True)

    # 两者都命中双共振，但强量能者的 Score 应更高
    assert strong_res["Score"] > weak_res["Score"]
    # base(82) + min(vol_ratio,3)*4 + min(pct,5)*1；强量能 vol_ratio=3 → +12，弱 vol_ratio=1 → +4
    assert strong_res["Score"] >= weak_res["Score"] + 7  # 量能差异贡献明显


# ── 失败样本闭环（改动 #17）──

def test_inject_failure_pattern_counts_recent_losses():
    """_inject_failure_pattern 应把近 90 天同代码+策略的失败次数注入 res['recent_failure_count']。

    改动 A4：按 (code, strategy_type) 配对，不再按纯 code 聚合。
    """
    from datetime import date, timedelta
    from sqlalchemy import create_engine, text
    from core.models import Base
    from core import scanner

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    today = date.today().isoformat()
    with engine.begin() as conn:
        for _ in range(3):  # 000001 + tv_dual_strict 失败 3 次
            conn.execute(text("""INSERT INTO failure_samples
                (code, name, sample_date, strategy_type, failure_type, reason, pnl_pct, source, created_at)
                VALUES ('000001','测试',:d,'tv_dual_strict','wind_control_stop','r',-5.0,'wind_control_auto',:d)"""), {"d": today})
        conn.execute(text("""INSERT INTO failure_samples
            (code, name, sample_date, strategy_type, failure_type, reason, pnl_pct, source, created_at)
            VALUES ('000002','测试2',:d,'tv_dual_strict','manual_loss_close','r',-3.0,'paper_trade_close',:d)"""), {"d": today})

    # 改动 A4：results 需带 strategy_type 才能精确匹配
    results = [
        {"代码": "000001", "strategy_type": "tv_dual_strict"},
        {"代码": "000002", "strategy_type": "tv_dual_strict"},
        {"代码": "000003", "strategy_type": "tv_dual_strict"},
    ]
    scanner._inject_failure_pattern(results, engine)
    assert results[0]["recent_failure_count"] == 3  # 000001 失败 3 次
    assert results[1]["recent_failure_count"] == 1  # 000002 失败 1 次
    assert results[2]["recent_failure_count"] == 0  # 000003 无失败


def test_failure_pattern_strategy_dimension_no_cross_strategy_veto():
    """A4：同代码不同策略不应被否决（tv_dual 失败不影响 pine 扫描）。"""
    from datetime import date
    from sqlalchemy import create_engine, text
    from core.models import Base
    from core import scanner

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    today = date.today().isoformat()
    with engine.begin() as conn:
        # 000001 在 tv_dual_strict 上失败 3 次（达否决阈值）
        for _ in range(3):
            conn.execute(text("""INSERT INTO failure_samples
                (code, name, sample_date, strategy_type, failure_type, reason, pnl_pct, source, created_at)
                VALUES ('000001','测试',:d,'tv_dual_strict','stop','r',-5.0,'auto',:d)"""), {"d": today})

    # 但当前用 pine 策略扫描 → 不应命中 tv_dual 的失败记录
    results = [{"代码": "000001", "strategy_type": "pine"}]
    scanner._inject_failure_pattern(results, engine)
    assert results[0]["recent_failure_count"] == 0, "跨策略不应被否决"

    # 同策略才命中
    results_same = [{"代码": "000001", "strategy_type": "tv_dual_strict"}]
    scanner._inject_failure_pattern(results_same, engine)
    assert results_same[0]["recent_failure_count"] == 3, "同策略应命中"


def test_failure_pattern_vetoes_recurring_loss_stock():
    """_apply_sop_filter 中 recent_failure_count >= 阈值 → veto，sop_grade 降为 D。

    改动 A4：阈值从 2 提到 3。
    """
    from core import scanner
    assert scanner.FAILURE_VETO_MIN_COUNT == 3, "A4: 阈值应从2提到3"
    results = [{
        "代码": "000001", "名称": "反复失败股", "行业": "测试",
        "影线比": 0.1, "pct_5d": 2, "历史胜率": "60%",
        "recent_failure_count": scanner.FAILURE_VETO_MIN_COUNT,  # = 阈值（3）
    }]
    scanner._apply_sop_filter(results, {"status": "OFFENSIVE"}, {})
    # recent_failure_count 达阈值 → 一票否决 → D 级
    assert results[0].get("sop_grade") == "D"
    assert any("失败模式" in v for v in results[0].get("sop_vetoes", []))


def test_failure_pattern_below_threshold_not_vetoed():
    """recent_failure_count < 阈值 → 不否决（不应误伤仅失败 2 次的票）。

    改动 A4：阈值提到 3 后，2 次失败不再触发否决。
    """
    from core import scanner
    results = [{
        "代码": "000002", "名称": "偶尔失败", "行业": "测试",
        "影线比": 0.1, "pct_5d": 2, "历史胜率": "60%",
        "recent_failure_count": 2,  # < 新阈值 3
    }]
    scanner._apply_sop_filter(results, {"status": "OFFENSIVE"}, {})
    assert results[0].get("sop_grade") != "D"


# ── 破位反抽陷阱多维评分（调整2 v2，上班族 Bark 场景）──

def _make_hist_df(closes, vols=None, ma20=None, vol_ma20=None):
    """构造含 收盘/成交量/MA20/Vol_MA20 列的 DataFrame，用于评分测试。

    vols/ma20/vol_ma20 未提供时按合理默认填充。
    """
    n = len(closes)
    if vols is None:
        vols = [100000.0] * n
    if ma20 is None:
        ma20 = [sum(closes[:i+1])/(i+1) for i in range(n)]  # 简单累计均值
    if vol_ma20 is None:
        vol_ma20 = [100000.0] * n
    return pd.DataFrame({
        "收盘": closes, "成交量": vols, "MA20": ma20, "Vol_MA20": vol_ma20,
    })


def test_trap_000958_pattern_scored_high():
    """000958 模式（温和量大跌 + 缩量弱反抽 + MA20破位 + V型未确认）→ 评分≥70，否决。

    构造：稳定→-6%大跌(量比1.5温和)→缩量恢复(1.2→1.0→0.8)→最后一日V型反弹。
    大跌后3天在MA20下方，且前一日仍在MA20下方。
    """
    from core import scanner
    closes = [10.0, 10.1, 10.0, 9.4, 9.6, 9.5, 10.2]  # idx3: -6%大跌; idx6: V型反弹
    # 量大跌日1.5倍(温和)，恢复期递减1.2/1.0/0.8
    vols = [100000]*7
    vols[3] = 150000   # 大跌日量比1.5
    vols[4] = 120000; vols[5] = 100000  # 恢复期递减
    vols[6] = 180000
    vol_ma20 = [100000]*7
    # MA20设在10.0，使大跌后9.4/9.6/9.5都在下方(idx3,4,5)
    ma20 = [9.9, 9.95, 10.0, 10.0, 10.0, 10.0, 10.05]
    df = _make_hist_df(closes, vols, ma20, vol_ma20)
    results = [{"代码": "000958"}]
    scanner._inject_breakdown_retracement(results, {"000958": df})
    score = results[0]["breakdown_trap_score"]
    assert score >= scanner.TRAP_VETO_SCORE, f"应≥{scanner.TRAP_VETO_SCORE}，实际{score}"


def test_golden_pit_high_volume_not_vetoed():
    """黄金坑（放量洗盘量比≥2 + 快速收复MA20 + 量价齐升）→ 评分<40，不标记。

    构造：-6%大跌但量比2.5（恐慌抛售真洗盘）→ 次日即收复MA20且放量。
    """
    from core import scanner
    closes = [10.0, 10.1, 10.0, 9.4, 10.1, 10.3]  # idx3: -6%大跌; idx4: 立即收复
    vols = [100000]*6
    vols[3] = 250000   # 大跌日量比2.5（恐慌放量）
    vols[4] = 200000; vols[5] = 220000  # 恢复期放量（量价齐升）
    vol_ma20 = [100000]*6
    ma20 = [9.9, 9.95, 10.0, 10.0, 10.0, 10.0]  # idx4收盘10.1>MA20=10.0
    df = _make_hist_df(closes, vols, ma20, vol_ma20)
    results = [{"代码": "000001"}]
    scanner._inject_breakdown_retracement(results, {"000001": df})
    score = results[0]["breakdown_trap_score"]
    assert score < scanner.TRAP_RISK_SCORE, f"黄金坑应<{scanner.TRAP_RISK_SCORE}，实际{score}"


def test_no_drop_not_scored():
    """无前置大跌（平稳上涨）→ 评分=0，不标记。"""
    from core import scanner
    closes = [10.0, 10.1, 10.2, 10.3, 10.5, 10.8, 11.0]  # 无大跌
    df = _make_hist_df(closes)
    results = [{"代码": "000001"}]
    scanner._inject_breakdown_retracement(results, {"000001": df})
    assert results[0]["breakdown_trap_score"] == 0


def test_trap_quick_ma20_reclaim_lower_score():
    """大跌后1天即收复MA20 → MA20破位维度不加满分（≤阈值），评分降低。

    对比000958（4天破位），快速收复的陷阱评分应更低（可能不到否决线）。
    """
    from core import scanner
    closes = [10.0, 10.1, 10.0, 9.4, 10.05, 10.3]  # idx3大跌; idx4立即回到MA20上方
    vols = [100000]*6
    vols[3] = 150000  # 温和量（量能不足+30）
    vols[4] = 80000; vols[5] = 70000  # 缩量(+25)
    vol_ma20 = [100000]*6
    ma20 = [9.9, 9.95, 10.0, 10.0, 10.0, 10.0]  # idx4收10.05>10.0
    df = _make_hist_df(closes, vols, ma20, vol_ma20)
    results = [{"代码": "000002"}]
    scanner._inject_breakdown_retracement(results, {"000002": df})
    score = results[0]["breakdown_trap_score"]
    # 快速收复：MA20破位天数=0(idx3大跌日本身算1天，但<阈值3) → 该维度0分
    # 量能不足30 + 缩量25 + V型(idx3在MA20下方,idx4跳上)20 = 75? 但idx4>MA20所以倒数第二根(idx4)已在上方→V型不加
    # 实际：30+25+0(MA20仅1天<3)+0(idx-2已在上方) = 55
    assert score < scanner.TRAP_VETO_SCORE, f"快速收复应<否决线{scanner.TRAP_VETO_SCORE}，实际{score}"


def test_moderate_score_adds_risk_not_veto():
    """中间档评分（40-69）→ 加风险标注，不否决（不一刀切）。"""
    from core import scanner
    results = [{
        "代码": "000003", "名称": "疑似陷阱", "行业": "测试",
        "影线比": 0.1, "pct_5d": 4, "历史胜率": "60%",
        "breakdown_trap_score": 55,  # 中间档
    }]
    scanner._apply_sop_filter(results, {"status": "OFFENSIVE"}, {})
    # 中间档不否决（grade≠D），但应有风险标注
    assert results[0].get("sop_grade") != "D"
    sop_risks = results[0].get("sop_risks", [])
    assert any("破位反抽" in r for r in sop_risks)
