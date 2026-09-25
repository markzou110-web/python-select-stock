"""
Unit tests for the backtest simulation engine in core/strategy.py.
"""

import pytest
import numpy as np
import pandas as pd
import sys
import os
from copy import deepcopy

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.strategy import (
    _simulate_backtest,
    _apply_tv_zp_expiry_and_alternate,
    check_pine_strategy,
    check_tv_dual_strategy,
    check_tv_reversal_watch,
    get_signal_details,
    calculate_pine_win_rate,
    calculate_historical_win_rate,
    calculate_tv_dual_win_rate,
    classify_tv_execution_tier,
    evaluate_exit_signals,
)
from core.scanner import _apply_sop_filter, _apply_trade_execution_profile


def test_tv_execution_tier_prioritizes_same_day_dual_and_limits_zp_only():
    assert classify_tv_execution_tier([150], [150]) == {
        "tier": "A",
        "label": "MA+ZP同日强共振",
        "risk_unit": 1.0,
        "auto_execute": True,
        "same_day_dual": True,
    }
    assert classify_tv_execution_tier([150], [])['tier'] == "B"
    assert classify_tv_execution_tier([], [150]) == {
        "tier": "C",
        "label": "ZP单信号研究层",
        "risk_unit": 0.25,
        "auto_execute": False,
        "same_day_dual": False,
    }


def test_tv_execution_tier_does_not_promote_different_day_window_to_a():
    tier = classify_tv_execution_tier([150], [149])

    assert tier["tier"] == "B"
    assert tier["same_day_dual"] is False


def test_tv_execution_profile_softens_zp_only_and_gates_ma_only():
    zp_only = {
        "strategy_type": "tv_dual",
        "tv_execution_tier": "C",
        "Score": 80,
        "price_action_score": 80,
        "effective_market_regime": "OFFENSIVE",
    }
    _apply_trade_execution_profile(zp_only)
    assert "ZP单信号仅研究观察，降低仓位优先级" in zp_only["trade_cautions"]
    assert zp_only["trade_eligible"] is False

    ma_only = {
        "strategy_type": "tv_dual",
        "tv_execution_tier": "B",
        "Score": 80,
        "price_action_score": 55,
        "effective_market_regime": "DEFENSIVE",
    }
    _apply_trade_execution_profile(ma_only)
    assert "MA单信号价格行为分<60，只观察" in ma_only["trade_blockers"]
    assert "MA单信号仅进攻市场允许执行" in ma_only["trade_cautions"]


def test_source_aware_tv_exit_signals_follow_declared_next_open_rules(monkeypatch):
    from core import strategy

    dates = pd.date_range("2026-01-01", periods=130, freq="B")
    frame = pd.DataFrame({
        "日期": dates,
        "开盘": np.full(130, 10.5),
        "最高": np.full(130, 10.8),
        "最低": np.full(130, 10.2),
        "收盘": np.full(130, 10.5),
        "成交量": np.full(130, 100000.0),
        "EMA5": np.full(130, 10.6),
        "EMA20": np.full(130, 10.7),
    })
    monkeypatch.setattr(
        strategy,
        "_find_tv_zp_signal_indices",
        lambda _df: ([], [len(_df) - 1], {}),
    )

    zp_short = evaluate_exit_signals(
        frame,
        entry_price=10.0,
        high_since_entry=11.0,
        signal_sources=["zp"],
    )
    assert zp_short[0]["reason"] == "TV-ZP short收盘确认"
    assert "下一交易日开盘退出" in zp_short[0]["suggestion"]

    ma_target = evaluate_exit_signals(
        frame,
        entry_price=10.0,
        high_since_entry=11.6,
        signal_sources=["ma"],
    )
    assert ma_target[0]["reason"] == "均线策略达到+15%目标"


def test_zp_profit_protection_requires_fifteen_percent_activation(monkeypatch):
    from core import strategy

    dates = pd.date_range("2026-01-01", periods=130, freq="B")
    frame = pd.DataFrame({
        "日期": dates,
        "开盘": np.full(130, 10.5),
        "最高": np.full(130, 10.8),
        "最低": np.full(130, 10.2),
        "收盘": np.full(130, 10.5),
        "成交量": np.full(130, 100000.0),
        "EMA5": np.full(130, 10.6),
        "EMA20": np.full(130, 10.7),
    })
    monkeypatch.setattr(strategy, "_find_tv_zp_signal_indices", lambda _df: ([], [], {}))

    assert evaluate_exit_signals(
        frame,
        entry_price=10.0,
        high_since_entry=11.0,
        signal_sources=["zp"],
    ) == []

    protected = evaluate_exit_signals(
        frame,
        entry_price=10.0,
        high_since_entry=11.6,
        signal_sources=["zp"],
    )
    assert protected[0]["reason"] == "TV-ZP盈利保护触发EMA20破位"
    assert "下一交易日开盘退出" in protected[0]["suggestion"]


def test_exit_signal_warns_when_limit_up_next_day_has_no_follow_through():
    frame = pd.DataFrame({
        "开盘": [9.9, 10.2, 11.0],
        "最高": [10.1, 11.0, 11.05],
        "最低": [9.8, 10.2, 10.7],
        "收盘": [10.0, 11.0, 10.8],
        "成交量": [100_000, 300_000, 220_000],
        "Vol_MA20": [100_000, 100_000, 100_000],
        "EMA5": [9.9, 10.2, 10.7],
        "EMA20": [9.8, 10.0, 10.4],
    })

    alerts = evaluate_exit_signals(
        frame,
        entry_price=9.5,
        high_since_entry=11.05,
        code="600001",
        signal_sources=["ma"],
    )

    assert alerts[0]["reason"] == "涨停次日未能顺势走高"
    assert "短期仓" in alerts[0]["suggestion"]


def test_tv_reversal_watch_requires_raw_long_daily_b_and_missing_weekly(monkeypatch):
    from core import strategy

    rows = 130
    close = np.linspace(10, 13, rows)
    df = pd.DataFrame({
        "开盘": close - 0.2,
        "最高": close + 0.1,
        "最低": close - 0.1,
        "收盘": close,
        "成交量": np.full(rows, 200000.0),
        "Vol_MA20": np.full(rows, 100000.0),
        "RSI_WILDER": np.full(rows, 65.0),
    })
    raw = pd.Series(False, index=df.index)
    raw.iloc[-1] = True
    monkeypatch.setattr(
        strategy,
        "_find_tv_zp_signal_indices",
        lambda _df: ([], [], {
            "leading_long": raw,
            "volume_confirm": raw,
            "qqe_long": raw,
        }),
    )
    monkeypatch.setattr(
        strategy,
        "_find_squeeze_signal_indices",
        lambda _df, **kwargs: [] if kwargs["use_weekly_filter"] else [df.index[-1]],
    )

    match, stats = check_tv_reversal_watch(df)

    assert match is True
    assert stats["tv_reversal_watch_only"] is True
    assert stats["trade_eligible"] is False
    assert stats["trade_bucket"] == "OBSERVE"
    assert "周线尚未转强" in stats["reason"]


class TestSimulateBacktestBasic:
    """Test basic backtest engine functionality."""

    def test_empty_signals_returns_zero(self):
        """No signals should return all-zero stats."""
        close = np.array([10.0] * 20)
        high = np.array([10.5] * 20)
        low = np.array([9.5] * 20)
        result = _simulate_backtest(close, high, low, signal_indices=[])
        assert result["signal_count"] == 0
        assert result["win_rate"] == 0
        assert result["confidence"] == 0
        assert result["adjusted_win_rate"] == 0
        assert result["adjusted_win_rate_method"] == "wilson_lower_99"
        assert result["expectancy"] == 0
        assert result["sample_warning"] == "无历史信号"
        assert result["vol_skipped"] == 0
        assert result["time_stopped"] == 0

    def test_basic_profitable_trade(self):
        """A clear uptrend should produce positive returns."""
        n = 50
        prices = np.linspace(10, 15, n)  # steady uptrend
        high = prices * 1.01
        low = prices * 0.99
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], max_hold_days=10
        )
        assert result["signal_count"] == 1
        assert result["avg_return"] > 0

    def test_stop_loss_triggered(self):
        """A sharp drop should trigger stop loss."""
        n = 30
        prices = np.array([10.0] * 10 + [8.0] * 20)  # drop at day 10
        high = prices + 0.1
        low = prices - 0.1
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], stop_loss_pct=-8.0, max_hold_days=15
        )
        assert result["stop_loss_hits"] >= 1

    def test_trailing_stop_triggers(self):
        """Price rises then falls back - trailing stop should trigger."""
        n = 30
        prices = np.concatenate([
            np.linspace(10, 13, 10),   # rise
            np.linspace(13, 10, 20),   # fall back
        ])
        high = prices + 0.2
        low = prices - 0.2
        atr = np.full(n, 0.3)
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3], atr_vals=atr,
            use_trailing_stop=True, trailing_multiplier=2.0,
            max_hold_days=20
        )
        # Should have exited before max_hold_days
        assert result["avg_hold_days"] < 20

    def test_max_hold_days_exit(self):
        """Flat price should exit at max_hold_days."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.05
        low = prices - 0.05
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5], max_hold_days=5,
            use_trailing_stop=False
        )
        assert result["avg_hold_days"] == 5

    def test_next_day_open_entry_removes_look_ahead_bias(self):
        """传入 open_vals 时入场价用次日开盘（真实 T+1 执行），而非信号日收盘（前视偏差）。

        构造：信号日(idx=0)收盘 10，次日(idx=1)开盘 +8%（非涨停，可成交）。
        - 不传 open_vals（旧行为）：入场价=10
        - 传 open_vals（真实）：入场价=10.8（次日开盘）
        后续价格平稳，退出价相同 → 两者的 exit_return 应不同（入场价不同）。
        """
        n = 8
        close = np.array([10.0, 10.8, 10.8, 10.8, 10.8, 10.8, 10.8, 10.8])
        openp = np.array([10.0, 10.8, 10.8, 10.8, 10.8, 10.8, 10.8, 10.8])  # 次日 +8% 高开（未涨停）
        high = close + 0.05
        low = close - 0.05

        # 旧行为：信号日收盘入场 = 10
        legacy = _simulate_backtest(close, high, low, signal_indices=[0], max_hold_days=5, use_trailing_stop=False)
        # T+1 真实入场 = 次日开盘 = 10.8
        t1 = _simulate_backtest(close, high, low, signal_indices=[0], max_hold_days=5, use_trailing_stop=False, open_vals=openp)

        # 旧入场 10，退出 ~10.8 → 正收益；T+1 入场 10.8，退出 10.8 → ~0 收益
        assert legacy["avg_return"] > t1["avg_return"]
        # T+1 入场后退出价仍是后续收盘，持仓窗口从 idx+1 起算
        assert t1["signal_count"] == 1

    def test_limit_up_signals_are_skipped(self):
        """涨停信号买不到，不应计入回测（否则高估胜率）。

        信号日(idx=0)收盘 10，次日开盘 +10%（≥9.5% 涨停阈值）→ 实盘封板买不到 → 跳过。
        """
        close = np.array([10.0, 11.0, 11.0, 11.0, 11.0, 11.0])
        openp = np.array([10.0, 11.0, 11.0, 11.0, 11.0, 11.0])  # 次日 +10% 涨停开盘
        high = close + 0.05
        low = close - 0.05

        result = _simulate_backtest(close, high, low, signal_indices=[0], max_hold_days=3, use_trailing_stop=False, open_vals=openp)
        assert result["signal_count"] == 0  # 涨停跳过，无成交
        assert result["limit_up_skipped"] == 1


class TestBacktestWinRateReliability:
    """Test sample-size reliability fields for historical win-rate stats."""

    def test_single_trade_win_rate_is_discounted_and_profit_factor_capped(self):
        prices = np.array([10.0, 12.0, 12.0])
        high = prices + 0.1
        low = prices - 0.1

        result = _simulate_backtest(
            close_vals=prices,
            high_vals=high,
            low_vals=low,
            signal_indices=[0],
            max_hold_days=1,
            stop_loss_pct=-50.0,
            use_trailing_stop=False,
        )

        assert result["signal_count"] == 1
        assert result["win_rate"] == 100.0
        assert result["confidence"] == 0.15
        assert result["adjusted_win_rate"] == 13.1
        assert result["adjusted_win_rate_method"] == "wilson_lower_99"
        assert result["profit_factor"] == 3.0
        assert result["sample_warning"] == "仅1笔交易，胜率仅供参考"

    def test_five_trade_sample_gets_partial_confidence(self):
        prices = np.linspace(10.0, 16.0, 12)
        high = prices + 0.1
        low = prices - 0.1

        result = _simulate_backtest(
            close_vals=prices,
            high_vals=high,
            low_vals=low,
            signal_indices=[0, 1, 2, 3, 4],
            max_hold_days=1,
            stop_loss_pct=-50.0,
            use_trailing_stop=False,
        )

        assert result["signal_count"] == 5
        assert result["win_rate"] == 100.0
        assert result["confidence"] == 0.4
        assert result["adjusted_win_rate"] == 42.9
        assert result["adjusted_win_rate_method"] == "wilson_lower_99"
        assert result["sample_warning"] == "样本偏少(5笔)，胜率可信度一般"

    def test_expectancy_can_be_negative_despite_mixed_win_rate(self):
        prices = np.array([10.0, 11.0, 10.0, 8.0, 8.0])
        high = prices + 0.1
        low = prices - 0.1

        result = _simulate_backtest(
            close_vals=prices,
            high_vals=high,
            low_vals=low,
            signal_indices=[0, 2],
            max_hold_days=1,
            stop_loss_pct=-50.0,
            use_trailing_stop=False,
        )

        assert result["signal_count"] == 2
        assert result["win_rate"] == 50.0
        assert result["expectancy"] < 0


class TestSopUsesAdjustedBacktestStats:
    """SOP quality scoring should use reliability-adjusted backtest stats."""

    def _base_result(self):
        return {
            "代码": "000001",
            "名称": "测试股",
            "行业": "测试行业",
            "Score": 120,
            "raw_score": 120,
            "历史胜率": "100%",
            "影线比": 0.1,
            "mkt_cap_yi": 100,
            "ROE": 15,
            "净利YOY": 30,
            "pa_structure_score": 100,
            "sector_alignment_score": 100,
            "涨幅%": 1.0,
            "回测统计": {
                "adjusted_win_rate": 15.0,
                "profit_factor": 3.0,
                "expectancy": 1.0,
            },
        }

    def test_sop_uses_adjusted_win_rate_dimension(self):
        rows = [self._base_result()]

        _apply_sop_filter(rows, {"status": "OFFENSIVE"}, {})

        assert rows[0]["sop_quality_dimensions"]["win_rate"] == 15.0

    def test_negative_expectancy_penalizes_quality_score(self):
        positive = self._base_result()
        negative = deepcopy(positive)
        negative["回测统计"]["expectancy"] = -1.0
        rows = [positive, negative]

        _apply_sop_filter(rows, {"status": "OFFENSIVE"}, {})

        assert negative["sop_quality_score"] == pytest.approx(positive["sop_quality_score"] - 10)
        assert any("期望收益为负" in risk for risk in negative["sop_risks"])


class TestDynamicCommission:
    """Test A-share realistic tax/commission model."""

    def test_friction_reduces_returns(self):
        """Commission + stamp tax should reduce raw returns."""
        n = 30
        prices = np.linspace(10, 10.5, n)  # mild uptrend
        high = prices + 0.1
        low = prices - 0.1
        
        # Without vol constraint
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3], max_hold_days=5, use_trailing_stop=False
        )
        # Return should be positive but slightly reduced by friction
        assert result["signal_count"] == 1
        # The friction should be small but nonzero
        raw_return = (prices[8] - prices[3]) / prices[3] * 100
        assert result["avg_return"] < raw_return  # friction eaten some profit


class TestVolumeConstraint:
    """Test volume constraint (vol_cap_pct) logic."""

    def test_volume_skipped_on_thin_liquidity(self):
        """Extremely low volume should cause skip."""
        n = 30
        prices = np.array([100.0] * n)
        high = prices + 1
        low = prices - 1
        vol = np.array([10.0] * n)  # very low volume → turnover ~1000
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5, 10, 15],
            vol_vals=vol, vol_cap_pct=0.05,
            max_hold_days=5, use_trailing_stop=False
        )
        assert result["vol_skipped"] >= 1

    def test_no_skip_on_normal_volume(self):
        """Normal volume should not cause skip."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.5
        low = prices - 0.5
        vol = np.array([1000000.0] * n)  # plenty of volume
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[5],
            vol_vals=vol, vol_cap_pct=0.05,
            max_hold_days=5, use_trailing_stop=False
        )
        assert result["vol_skipped"] == 0
        assert result["signal_count"] == 1


class TestTimeStop:
    """Test time-based stop loss."""

    def test_time_stop_triggers_on_flat(self):
        """Flat price for N days should trigger time stop."""
        n = 30
        prices = np.array([10.0] * n)
        high = prices + 0.01
        low = prices - 0.01
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3],
            time_stop_days=5, max_hold_days=15,
            use_trailing_stop=False
        )
        assert result["time_stopped"] >= 1
        assert result["avg_hold_days"] == 5

    def test_time_stop_does_not_trigger_on_profit(self):
        """Profitable position should NOT be time-stopped."""
        n = 30
        prices = np.linspace(10, 12, n)  # uptrend
        high = prices + 0.1
        low = prices - 0.1
        
        result = _simulate_backtest(
            close_vals=prices, high_vals=high, low_vals=low,
            signal_indices=[3],
            time_stop_days=5, max_hold_days=15,
            use_trailing_stop=False
        )
        assert result["time_stopped"] == 0


class TestStrategySignalAlignment:
    """策略实时筛选与回测条件一致性。"""

    def _base_df(self, n=140):
        close = np.linspace(10, 14, n)
        df = pd.DataFrame({
            "开盘": close - 0.5,
            "收盘": close,
            "最高": close + 0.1,
            "最低": close - 0.2,
            "成交量": np.full(n, 200000.0),
            "Vol_MA20": np.full(n, 100000.0),
            "RSI": np.full(n, 60.0),
            "MACD_DIF": np.full(n, 0.2),
            "MACD_DEA": np.full(n, 0.1),
            "ATR": np.full(n, 0.3),
            "BB_Width": np.full(n, 0.08),
            "Sqz_Ratio": np.full(n, 0.08),
            "EMA5": close - 0.4,
            "EMA10": close - 0.5,
            "EMA20": close - 0.6,
            "EMA60": close - 0.7,
            "RS": np.full(n, 1.1),
            "RS_MA50": np.full(n, 1.0),
        })
        return df

    def test_pine_strategy_counts_five_indicators_and_volume_filter(self):
        df = self._base_df()
        df["RF_Upward"] = True
        df["RF_Downward"] = False
        df["ST_Signal"] = True
        df["RQK_Up"] = True
        df["HalfTrend_Up"] = True
        df["QQE_Long"] = True

        match, stats = check_pine_strategy(df, min_signals=5)

        assert match is True
        assert stats["信号数"] == "5/5"

    def test_pine_backtest_uses_live_filters(self):
        df = self._base_df()
        df["RF_Upward"] = True
        df["RF_Downward"] = False
        df["ST_Signal"] = True
        df["RQK_Up"] = True
        df["HalfTrend_Up"] = True
        df["QQE_Long"] = True
        df.loc[:, "成交量"] = 110000.0

        bt = calculate_pine_win_rate(df, min_signals=5)

        assert bt["signal_count"] == 0

    def test_squeeze_backtest_respects_runtime_volume_multiplier(self):
        df = self._base_df(160)
        close = np.r_[np.full(130, 10.0), np.linspace(10.8, 12.0, 30)]
        df.loc[:, "收盘"] = close
        df.loc[:, "开盘"] = close - 0.3
        df.loc[:, "最高"] = close + 0.2
        df.loc[:, "最低"] = close - 0.2
        df.loc[:, "EMA5"] = close - 0.2
        df.loc[:, "EMA10"] = close - 0.25
        df.loc[:, "EMA20"] = close - 0.3
        df.loc[:, "EMA60"] = close - 0.4
        df.loc[:, "成交量"] = 160000.0

        loose = calculate_historical_win_rate(df, vol_multiplier=1.5, use_bb_sqz=False)
        strict = calculate_historical_win_rate(df, vol_multiplier=2.0, use_bb_sqz=False)

        assert loose["signal_count"] > 0
        assert strict["signal_count"] == 0

    def test_tv_dual_strategy_accepts_recent_ma_signal_without_extra_filters(self):
        df = self._base_df(160)
        close = np.full(160, 10.0)
        close[-4:] = [9.8, 9.7, 9.6, 10.8]
        df.loc[:, "收盘"] = close
        df.loc[:, "开盘"] = close - 0.2
        df.loc[:, "最高"] = close + 0.2
        df.loc[:, "最低"] = close - 0.2
        df.loc[:, "EMA5"] = close - 0.1
        df.loc[:, "EMA10"] = close - 0.15
        df.loc[:, "EMA20"] = close - 0.2
        df.loc[:, "EMA60"] = close - 0.3
        df.loc[:, "RSI_WILDER"] = 60.0
        df.loc[:, "成交量"] = 200000.0

        match, stats = check_tv_dual_strategy(df)

        assert match is True
        assert stats["tv_ma_signal"] == "B共振"
        assert stats["signal_sources"] == ["ma"]
        assert "EMA20破位次日开盘" in stats["trade_exit_policy"]

        strict_match, strict_stats = check_tv_dual_strategy(df, require_both=True)

        assert strict_match is False
        assert strict_stats["tv_ma_signal"] == "B共振"
        assert strict_stats["tv_zp_signal"] == "无"


class TestTvDualStrictBacktestFallback:
    """tv_dual_strict 历史胜率回测：严格配对样本不足时退回"任意单信号"并集，避免胜率恒为0。

    回归 bug：require_both=True 时直接套用实时筛选的 3 天配对窗口到 4 年历史，
    导致绝大多数股票 0 信号、历史胜率恒为 0%。
    """

    def _df_with_signals(self, n=200):
        close = np.linspace(10, 14, n)
        df = pd.DataFrame({
            "开盘": close - 0.3,
            "收盘": close,
            "最高": close + 0.2,
            "最低": close - 0.2,
            "成交量": np.full(n, 200000.0),
            "ATR": np.full(n, 0.3),
        })
        return df

    def test_strict_falls_back_to_union_when_paired_samples_too_few(self, monkeypatch):
        """均线信号与 ZP 信号相隔很远（无 3 天内配对）→ 退回并集，得到非零胜率。"""
        import core.strategy as strategy

        # MA 在 50/120，ZP 在 80/150 —— 全部相隔 >=30 天，3 天窗口内 0 配对
        monkeypatch.setattr(strategy, "_find_squeeze_signal_indices", lambda *a, **k: [50, 120])
        monkeypatch.setattr(strategy, "_find_tv_zp_signal_indices", lambda *a, **k: ([80, 150], [], {}))

        df = self._df_with_signals()
        result = calculate_tv_dual_win_rate(df, require_both=True)

        # 退回并集后应有 4 个信号，胜率非"无历史信号"
        assert result["signal_count"] == 4
        assert "任意单信号" in result["sample_warning"]

    def test_strict_uses_paired_when_enough_samples(self, monkeypatch):
        """均线与 ZP 在 3 天内多次配对（>=5）→ 直接用配对样本，不触发退回。"""
        import core.strategy as strategy

        # 构造 6 组 3 天内配对（MA 与 ZP 相隔 1 天）
        pairs = [(20 + i * 20, 21 + i * 20) for i in range(6)]
        ma = [p[0] for p in pairs]
        zp = [p[1] for p in pairs]
        monkeypatch.setattr(strategy, "_find_squeeze_signal_indices", lambda *a, **k: ma)
        monkeypatch.setattr(strategy, "_find_tv_zp_signal_indices", lambda *a, **k: (zp, [], {}))

        df = self._df_with_signals(300)
        result = calculate_tv_dual_win_rate(df, require_both=True)

        assert result["signal_count"] == 6
        # 用的是严格配对样本，不应出现"任意单信号"标注
        assert "任意单信号" not in result.get("sample_warning", "")

    def test_loose_mode_never_uses_paired_logic(self, monkeypatch):
        """require_both=False 始终用并集，不受配对影响。"""
        import core.strategy as strategy

        monkeypatch.setattr(strategy, "_find_squeeze_signal_indices", lambda *a, **k: [50, 120])
        monkeypatch.setattr(strategy, "_find_tv_zp_signal_indices", lambda *a, **k: ([80, 150], [], {}))

        df = self._df_with_signals()
        result = calculate_tv_dual_win_rate(df, require_both=False)

        assert result["signal_count"] == 4
        assert "任意单信号" not in result.get("sample_warning", "")


class TestTradingViewZPStrategy:
    def test_expiry_and_alternate_rule_keeps_first_three_leading_bars(self):
        leading_long = pd.Series([False, False, False, True, True, True, True, False, False, False])
        leading_short = pd.Series([False, True, True, False, False, False, False, True, True, True])
        long_cond = pd.Series([False, False, False, True, True, True, True, False, False, False])
        short_cond = pd.Series([False, True, True, False, False, False, False, True, True, True])

        long_indices, short_indices = _apply_tv_zp_expiry_and_alternate(
            leading_long=leading_long,
            leading_short=leading_short,
            long_cond=long_cond,
            short_cond=short_cond,
            expiry=3,
        )

        assert long_indices == [3]
        assert short_indices == [7]

    def test_alternate_rule_requires_previous_opposite_state(self):
        leading_long = pd.Series([False, True, True, True])
        leading_short = pd.Series([False, False, False, False])
        long_cond = pd.Series([False, True, True, True])
        short_cond = pd.Series([False, False, False, False])

        long_indices, short_indices = _apply_tv_zp_expiry_and_alternate(
            leading_long=leading_long,
            leading_short=leading_short,
            long_cond=long_cond,
            short_cond=short_cond,
            expiry=3,
        )

        assert long_indices == []
        assert short_indices == []

    def test_tv_zp_signal_details_returns_tradingview_style_keys(self):
        n = 180
        dates = pd.date_range("2025-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
        close = np.r_[np.linspace(10, 9, 80), np.linspace(9, 14, 100)]
        df = pd.DataFrame({
            "日期": dates,
            "开盘": close * 0.99,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "成交量": np.linspace(100000, 300000, n),
            "Vol_MA20": np.full(n, 120000.0),
            "ATR": np.full(n, 0.3),
        })

        details = get_signal_details(df, strategy_type="tv_zp")

        assert set(details.keys()) == {"buy_signals", "sell_signals", "trailing_stops"}
        assert isinstance(details["buy_signals"], list)
        assert isinstance(details["sell_signals"], list)
        assert details["trailing_stops"] == []


def test_evaluate_exit_signals_accepts_optional_code():
    df = pd.DataFrame({
        "日期": pd.date_range("2026-06-01", periods=20),
        "开盘": [10.0] * 20,
        "最高": [10.5] * 20,
        "最低": [9.8] * 20,
        "收盘": [10.2] * 19 + [9.0],
        "成交量": [100000] * 20,
        "ATR": [0.4] * 20,
    })

    assert isinstance(evaluate_exit_signals(df, entry_price=10.0, high_since_entry=10.8), list)
    assert isinstance(evaluate_exit_signals(df, entry_price=10.0, high_since_entry=10.8, code="603259"), list)
