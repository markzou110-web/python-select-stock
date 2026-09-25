"""Research-only monthly direction and weekly location from completed bars."""

from __future__ import annotations

import pandas as pd

WEEKLY_EXTENSION_MA10_RATIO = 1.2
WEEKLY_VOLUME_CLIMAX_RATIO = 1.5
WEEKLY_PULLBACK_MIN_HIGH_RATIO = 0.85
WEEKLY_PULLBACK_MAX_HIGH_RATIO = 0.98
SHADOW_REFERENCE_COST_PCT = 0.1
WEEKLY_BREAKOUT_VOLUME_RATIO = 1.3
WEEKLY_PLATFORM_MAX_RANGE = 0.25
WEEKLY_MAX_MA5_EXTENSION = 1.15


def build_completed_timeframe_context(df: pd.DataFrame) -> dict:
    """Exclude the month and week containing the latest daily bar to avoid partial bars."""
    unavailable = {
        "pa_monthly_trend": "月线数据不足",
        "pa_monthly_state": "UNAVAILABLE",
        "pa_monthly_as_of": None,
        "pa_weekly_position": "周线位置数据不足",
        "pa_weekly_position_state": "UNAVAILABLE",
        "pa_weekly_position_as_of": None,
        "pa_weekly_pattern_signals": [],
        "pa_timeframe_shadow_only": True,
    }
    if df is None or df.empty or "日期" not in df or "收盘" not in df:
        return unavailable

    work = df[[column for column in ("日期", "开盘", "收盘", "最高", "最低", "成交量") if column in df]].copy()
    work["日期"] = pd.to_datetime(work["日期"], errors="coerce")
    work["收盘"] = pd.to_numeric(work["收盘"], errors="coerce")
    work["最高"] = pd.to_numeric(work["最高"], errors="coerce") if "最高" in work else work["收盘"]
    work["开盘"] = pd.to_numeric(work["开盘"], errors="coerce") if "开盘" in work else work["收盘"]
    work["最低"] = pd.to_numeric(work["最低"], errors="coerce") if "最低" in work else work["收盘"]
    work["成交量"] = pd.to_numeric(work["成交量"], errors="coerce") if "成交量" in work else 0.0
    work = work.dropna(subset=["日期", "收盘"]).sort_values("日期").drop_duplicates("日期", keep="last")
    if work.empty:
        return unavailable

    latest = work["日期"].iloc[-1]
    work = work.set_index("日期")
    result = dict(unavailable)
    # ponytail: current periods are excluded even on their final trading day; this
    # conservative one-period lag avoids confusing an intraday quote with a close;
    # a verified market-close flag can later remove that lag.
    monthly = work["收盘"].resample("ME").last().dropna()
    monthly = monthly[monthly.index.to_period("M") < latest.to_period("M")]
    monthly = monthly.iloc[1:]  # the source history may start in the middle of a month
    if len(monthly) >= 11:
        ma5 = monthly.rolling(5).mean()
        ma10 = monthly.rolling(10).mean()
        close = float(monthly.iloc[-1])
        if close >= ma10.iloc[-1] and ma5.iloc[-1] >= ma10.iloc[-1] and ma10.iloc[-1] > ma10.iloc[-2]:
            state, label = "UP", "月线向上"
        elif close >= ma10.iloc[-1] or ma10.iloc[-1] > ma10.iloc[-2]:
            state, label = "REPAIR", "月线修复/分歧"
        else:
            state, label = "DOWN", "月线走弱"
        result.update({
            "pa_monthly_trend": label,
            "pa_monthly_state": state,
            "pa_monthly_as_of": work.index[work.index.to_period("M") == monthly.index[-1].to_period("M")].max().strftime("%Y-%m-%d"),
        })

    weekly = work.resample("W-FRI").agg({"开盘": "first", "收盘": "last", "最高": "max", "最低": "min", "成交量": "sum"}).dropna(subset=["收盘"])
    weekly = weekly[weekly.index.to_period("W-FRI") < latest.to_period("W-FRI")]
    weekly = weekly.iloc[1:]  # the source history may start in the middle of a week
    if len(weekly) >= 21:
        closes = weekly["收盘"]
        ma10 = closes.rolling(10).mean()
        ma20 = closes.rolling(20).mean()
        close = float(closes.iloc[-1])
        prior_high = float(weekly["最高"].iloc[-9:-1].max())
        prior_volume = float(weekly["成交量"].iloc[-5:-1].mean())
        volume = float(weekly["成交量"].iloc[-1])
        trend = close >= ma10.iloc[-1] and ma10.iloc[-1] >= ma10.iloc[-2]
        extended = close >= ma10.iloc[-1] * WEEKLY_EXTENSION_MA10_RATIO or (
            prior_volume > 0 and volume >= prior_volume * WEEKLY_VOLUME_CLIMAX_RATIO and close >= prior_high * WEEKLY_PULLBACK_MAX_HIGH_RATIO
        )
        if trend and extended:
            state, label = "EXTENDED", "周线高位/放量，避免追价"
        elif trend and prior_high > 0 and WEEKLY_PULLBACK_MIN_HIGH_RATIO <= close / prior_high <= WEEKLY_PULLBACK_MAX_HIGH_RATIO and prior_volume > 0 and volume < prior_volume:
            state, label = "PULLBACK", "周线缩量回调待确认"
        elif close < ma20.iloc[-1] and ma20.iloc[-1] < ma20.iloc[-2]:
            state, label = "WEAK", "周线位置偏弱"
        else:
            state, label = "NEUTRAL", "周线位置中性"
        result.update({
            "pa_weekly_position": label,
            "pa_weekly_position_state": state,
            "pa_weekly_position_as_of": work.index[work.index.to_period("W-FRI") == weekly.index[-1].to_period("W-FRI")].max().strftime("%Y-%m-%d"),
        })
        previous_volume = float(weekly["成交量"].iloc[-6:-1].mean())
        last = weekly.iloc[-1]
        previous = weekly.iloc[-2]
        body_ratio = (last["收盘"] - last["开盘"]) / max(last["最高"] - last["最低"], 0.01)
        lows = weekly["最低"].iloc[-4:]
        signals = []
        if (previous_volume > 0 and last["成交量"] >= previous_volume * 1.2
                and body_ratio >= 0.5 and close > closes.rolling(5).mean().iloc[-1]
                and previous["收盘"] <= closes.rolling(5).mean().iloc[-2]
                and lows.iloc[1] >= lows.iloc[0] and lows.iloc[2] >= lows.iloc[1]
                and lows.iloc[3] >= lows.iloc[2]):
            signals.append("站稳5周线")
        platform = weekly.iloc[-9:-1]
        platform_low = float(platform["最低"].min())
        if (platform_low > 0 and float(platform["最高"].max()) / platform_low - 1 <= WEEKLY_PLATFORM_MAX_RANGE
                and close > float(platform["最高"].max()) and body_ratio >= 0.5
                and previous_volume > 0 and last["成交量"] >= previous_volume * WEEKLY_BREAKOUT_VOLUME_RATIO):
            signals.append("周线平台放量突破")
        ma5 = closes.rolling(5).mean()
        if (ma5.iloc[-1] > ma20.iloc[-1] and ma5.iloc[-1] > ma5.iloc[-2]
                and ma20.iloc[-1] > ma20.iloc[-2]
                and ma5.iloc[-1] - ma20.iloc[-1] > ma5.iloc[-2] - ma20.iloc[-2]
                and ma5.iloc[-1] <= close <= ma5.iloc[-1] * WEEKLY_MAX_MA5_EXTENSION
                and previous_volume > 0 and previous_volume <= last["成交量"] <= previous_volume * WEEKLY_BREAKOUT_VOLUME_RATIO):
            signals.append("5/20周均线转强")
        pileup = weekly["成交量"].iloc[-4:-1]
        if (pileup.iloc[0] > 0 and pileup.iloc[0] < pileup.iloc[1] < pileup.iloc[2]
                and pileup.iloc[1] / pileup.iloc[0] <= WEEKLY_VOLUME_CLIMAX_RATIO
                and pileup.iloc[2] / pileup.iloc[1] <= WEEKLY_VOLUME_CLIMAX_RATIO
                and 0 < last["成交量"] < pileup.iloc[-1]
                and last["收盘"] < previous["收盘"]
                and last["最低"] >= max(float(weekly["最低"].iloc[-4:-1].min()), float(ma20.iloc[-1]))):
            signals.append("周线堆量后缩量回踩")
        result["pa_weekly_pattern_signals"] = signals
    return result


def classify_mtf_relation(monthly_state, weekly_state, daily_state) -> str:
    """三周期关系（大=月/周定性质，中=日线操作周期）——《交易之路》三周期框架。

    口诀"大小同涨跌，中期猜底顶"：
    - 大小同向 + 中期回踩（逆小势）= 顺大势逆小势的切入点；
    - 大小逆向 = 耐心等待；
    - 大小同向但中期走弱/背离 = 持有不追。
    输入为 pa_monthly_state / pa_weekly_position_state / pa_daily_state。
    """
    monthly = str(monthly_state or "").upper()
    weekly = str(weekly_state or "").upper()
    daily = str(daily_state or "").upper()
    if monthly in ("", "UNAVAILABLE") and weekly in ("", "UNAVAILABLE"):
        return "数据不足"
    big_bull = monthly == "UP" or weekly in ("PULLBACK", "EXTENDED")
    big_weak = monthly == "DOWN" or weekly == "WEAK"
    if big_bull and daily == "UP":
        return "三周期共振多头"
    if big_bull and daily in ("MIXED", "DOWN"):
        return "顺大势逆小势·中期回踩"
    if big_weak and daily == "UP":
        return "逆大势反弹·不追"
    if big_weak and daily in ("MIXED", "DOWN"):
        return "大小同向向下·回避"
    return "周期方向不明"


def build_timeframe_shadow_report(frame: pd.DataFrame) -> dict:
    """Compare tagged scan candidates with a next-open-to-day-five research proxy."""
    contract = {
        "mode": "SHADOW_ONLY",
        "sample_unit": "最新 code + signal_date + strategy_type 扫描快照",
        "entry": "信号次交易日开盘价（假设可成交）",
        "exit": "信号后第5个交易日收盘价（不是系统真实卖出规则）",
        "reference_cost_pct": SHADOW_REFERENCE_COST_PCT,
        "limitations": "不模拟涨跌停无法成交、滑点、最低佣金、止损或盈利目标；仅用于比较过滤条件，不代表策略收益。",
    }
    if frame is None or frame.empty:
        return {"contract": contract, "tagged_candidates": 0, "arms": []}
    required = {
        "code", "signal_date", "strategy_type", "trade_bucket", "pa_trade_action",
        "pa_monthly_state", "pa_weekly_position_state", "pa_swing_entry_route",
        "open_1d", "close_5d", "data_quality_excluded",
    }
    if not required.issubset(frame.columns):
        return {"contract": contract, "tagged_candidates": 0, "arms": [], "status": "INVALID_DATA"}
    work = frame.drop_duplicates(["code", "signal_date", "strategy_type"], keep="first").copy()
    work = work[
        work["pa_monthly_state"].isin(["UP", "REPAIR", "DOWN"])
        & work["pa_weekly_position_state"].isin(["PULLBACK", "EXTENDED", "WEAK", "NEUTRAL"])
        & work["trade_bucket"].isin(["TRADE", "EARLY", "OBSERVE"])
        & work["pa_trade_action"].ne("AVOID")
        & work["pa_swing_entry_route"].isin(["BREAKOUT", "PULLBACK"])
    ].copy()
    if work.empty:
        return {"contract": contract, "tagged_candidates": 0, "arms": []}
    open_price = pd.to_numeric(work["open_1d"], errors="coerce")
    close_price = pd.to_numeric(work["close_5d"], errors="coerce")
    clean = ~work["data_quality_excluded"].fillna(False).astype(bool)
    work["proxy_return_5d_pct"] = ((close_price / open_price - 1) * 100 - contract["reference_cost_pct"]).where(
        clean & open_price.gt(0) & close_price.gt(0)
    )
    monthly_up = work["pa_monthly_state"].eq("UP")
    week_not_chase = work["pa_weekly_position_state"].isin(["PULLBACK", "NEUTRAL"])
    pullback = work["pa_weekly_position_state"].eq("PULLBACK") & work["pa_swing_entry_route"].eq("PULLBACK")
    arms = []
    for name, mask in (
        ("现有日线新触发候选", pd.Series(True, index=work.index)),
        ("月线向上", monthly_up),
        ("月线向上＋周线不追高", monthly_up & week_not_chase),
        ("月线向上＋周线回调转强", monthly_up & pullback),
    ):
        returns = work.loc[mask, "proxy_return_5d_pct"].dropna()
        arms.append({
            "name": name,
            "candidates": int(mask.sum()),
            "mature_5d": int(len(returns)),
            "win_rate_5d_pct": round(float(returns.gt(0).mean() * 100), 1) if len(returns) else None,
            "avg_proxy_return_5d_pct": round(float(returns.mean()), 2) if len(returns) else None,
        })
    return {"contract": contract, "tagged_candidates": int(len(work)), "arms": arms}
