"""Point-in-time monthly ETF absolute-trend and relative-strength replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RotationConfig:
    risk_assets: tuple[str, ...]
    defensive_asset: str
    trend_days: int = 210
    momentum_short_days: int = 126
    momentum_long_days: int = 252
    volatility_days: int = 60
    top_n: int = 2
    volatility_floor: float = 0.05
    target_volatility: float = 0.10
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    slippage_bps: float = 5.0
    lot_size: int = 100


@dataclass(frozen=True)
class RotationDecision:
    signal_date: pd.Timestamp
    execution_date: pd.Timestamp
    target_weights: dict[str, float]
    scores: dict[str, float]


def _normalise_prices(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "open", "close"}
    if frame is None or not required.issubset(frame.columns):
        return pd.DataFrame(columns=["date", "open", "close"])
    result = frame[["date", "open", "close"]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
    result["open"] = pd.to_numeric(result["open"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["raw_open"] = pd.to_numeric(
        frame["raw_open"] if "raw_open" in frame else frame["open"],
        errors="coerce",
    )
    result["raw_close"] = pd.to_numeric(
        frame["raw_close"] if "raw_close" in frame else frame["close"],
        errors="coerce",
    )
    result["adjustment_factor"] = pd.to_numeric(
        frame["adjustment_factor"] if "adjustment_factor" in frame else 1.0,
        errors="coerce",
    )
    return (
        result.dropna(
            subset=["date", "open", "close", "raw_open", "raw_close", "adjustment_factor"]
        )
        .loc[
            lambda value: (value[["open", "close", "raw_open", "raw_close", "adjustment_factor"]] > 0).all(axis=1)
        ]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _prepared_prices(prices: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    prepared: dict[str, pd.DataFrame] = {}
    for code, frame in prices.items():
        normalised = _normalise_prices(frame)
        if not normalised.empty:
            prepared[str(code)] = normalised
    return prepared


def _score_asset(history: pd.DataFrame, config: RotationConfig) -> float | None:
    required = max(
        config.trend_days,
        config.momentum_short_days + 1,
        config.momentum_long_days + 1,
        config.volatility_days + 1,
    )
    if len(history) < required:
        return None
    close = history["close"]
    latest = float(close.iloc[-1])
    trend = float(close.iloc[-config.trend_days :].mean())
    short_return = latest / float(close.iloc[-config.momentum_short_days - 1]) - 1
    long_return = latest / float(close.iloc[-config.momentum_long_days - 1]) - 1
    volatility = float(close.pct_change().iloc[-config.volatility_days :].std(ddof=0)) * np.sqrt(252)
    if latest <= trend or long_return <= 0:
        return None
    return (0.5 * short_return + 0.5 * long_return) / max(
        volatility,
        config.volatility_floor,
    )


def build_rotation_decisions(
    prices: Mapping[str, pd.DataFrame],
    config: RotationConfig,
) -> list[RotationDecision]:
    """Create month-end decisions using only data known on each signal date."""
    prepared = _prepared_prices(prices)
    if config.defensive_asset not in prepared or config.top_n <= 0:
        return []
    calendar = pd.DatetimeIndex(
        sorted(set().union(*(set(frame["date"]) for frame in prepared.values())))
    )
    if len(calendar) < 2:
        return []
    month_ends = pd.Series(calendar, index=calendar).groupby(calendar.to_period("M")).last()
    required = max(
        config.trend_days,
        config.momentum_short_days + 1,
        config.momentum_long_days + 1,
        config.volatility_days + 1,
    )
    decisions: list[RotationDecision] = []
    for signal_date in month_ends:
        next_dates = calendar[calendar > signal_date]
        if next_dates.empty:
            continue
        scores: dict[str, float] = {}
        has_history = False
        for code in config.risk_assets:
            frame = prepared.get(code)
            if frame is None:
                continue
            history = frame[frame["date"] <= signal_date]
            if len(history) < required or pd.Timestamp(history.iloc[-1]["date"]) != signal_date:
                continue
            has_history = True
            score = _score_asset(history, config)
            if score is not None:
                scores[code] = score
        if not has_history:
            continue
        selected = sorted(scores, key=lambda code: (-scores[code], code))[: config.top_n]
        risk_scale = 1.0
        if selected and config.target_volatility > 0:
            return_columns = []
            for code in selected:
                history = prepared[code][prepared[code]["date"] <= signal_date].tail(
                    config.volatility_days + 1
                )
                series = history.set_index("date")["close"].pct_change().rename(code)
                return_columns.append(series)
            aligned = pd.concat(return_columns, axis=1, join="inner").dropna()
            if len(aligned) >= max(10, config.volatility_days // 2):
                weights = np.repeat(1 / len(selected), len(selected))
                covariance = aligned.cov().to_numpy() * 252
                portfolio_volatility = float(np.sqrt(weights @ covariance @ weights))
                if portfolio_volatility > 0:
                    risk_scale = min(1.0, config.target_volatility / portfolio_volatility)
        target_weight = risk_scale / config.top_n
        targets = {code: target_weight for code in selected}
        defensive_weight = 1.0 - sum(targets.values())
        if defensive_weight > 1e-9:
            targets[config.defensive_asset] = defensive_weight
        decisions.append(
            RotationDecision(
                signal_date=pd.Timestamp(signal_date),
                execution_date=pd.Timestamp(next_dates[0]),
                target_weights=targets,
                scores={code: round(scores[code], 8) for code in selected},
            )
        )
    return decisions


def _metrics(
    equity: pd.DataFrame,
    *,
    initial_capital: float,
    transaction_cost: float,
    gross_traded_value: float,
    trades: int,
    rebalances: int,
    risk_exposure_days: int,
) -> dict[str, Any]:
    if equity.empty:
        return {"status": "INSUFFICIENT_DATA", "final_equity": 0.0}
    curve = equity.set_index("date")["equity"]
    starting_curve = pd.concat(
        [pd.Series([initial_capital], index=[curve.index[0] - pd.Timedelta(nanoseconds=1)]), curve]
    )
    daily_returns = starting_curve.pct_change().dropna()
    elapsed_years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1 / 252)
    total_return = float(curve.iloc[-1] / initial_capital - 1)
    annual_return = float((1 + total_return) ** (1 / elapsed_years) - 1) if total_return > -1 else -1.0
    annual_volatility = float(daily_returns.std(ddof=0) * np.sqrt(252)) if len(daily_returns) else 0.0
    sharpe = float(daily_returns.mean() / daily_returns.std(ddof=0) * np.sqrt(252)) if annual_volatility > 0 else 0.0
    drawdown = starting_curve / starting_curve.cummax() - 1
    max_drawdown = float(drawdown.min())
    yearly_values = curve.resample("YE").last().to_numpy()
    monthly_values = curve.resample("ME").last().to_numpy()
    yearly = pd.Series(np.r_[initial_capital, yearly_values]).pct_change().dropna()
    monthly = pd.Series(np.r_[initial_capital, monthly_values]).pct_change().dropna()
    return {
        "status": "OK",
        "start_date": curve.index[0].strftime("%Y-%m-%d"),
        "end_date": curve.index[-1].strftime("%Y-%m-%d"),
        "initial_equity": round(float(initial_capital), 2),
        "final_equity": round(float(curve.iloc[-1]), 2),
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(annual_return * 100, 2),
        "annual_volatility_pct": round(annual_volatility * 100, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "calmar": round(annual_return / abs(max_drawdown), 3) if max_drawdown < 0 else None,
        "positive_year_rate_pct": round(float((yearly > 0).mean()) * 100, 2) if len(yearly) else 0.0,
        "monthly_win_rate_pct": round(float((monthly > 0).mean()) * 100, 2) if len(monthly) else 0.0,
        "risk_exposure_pct": round(risk_exposure_days / len(curve) * 100, 2),
        "rebalances": rebalances,
        "trades": trades,
        "turnover_times": round(gross_traded_value / float(curve.mean()), 3),
        "transaction_cost": round(transaction_cost, 2),
    }


def run_etf_rotation(
    prices: Mapping[str, pd.DataFrame],
    config: RotationConfig,
) -> dict[str, Any]:
    """Replay decisions at the next session open with ETF costs and 100-share lots."""
    prepared = _prepared_prices(prices)
    decisions = build_rotation_decisions(prepared, config)
    if not decisions:
        return {"metrics": {"status": "INSUFFICIENT_DATA", "final_equity": 0.0}, "trades": [], "decisions": [], "equity_curve": []}

    lookups = {
        code: frame.set_index("date")[
            ["open", "close", "raw_open", "raw_close", "adjustment_factor"]
        ].to_dict("index")
        for code, frame in prepared.items()
    }
    start_date = decisions[0].execution_date
    calendar = pd.DatetimeIndex(
        sorted(
            date
            for date in set().union(*(set(frame["date"]) for frame in prepared.values()))
            if date >= start_date
        )
    )
    decisions_by_date = {item.execution_date: item for item in decisions}
    cash = float(config.initial_capital)
    positions: dict[str, float] = {}
    last_close: dict[str, float] = {}
    last_adjustment_factor: dict[str, float] = {}
    trade_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    transaction_cost = 0.0
    gross_traded_value = 0.0
    risk_exposure_days = 0

    def raw_price(code: str, day: pd.Timestamp, field: str) -> float | None:
        row = lookups.get(code, {}).get(day)
        return float(row[field]) if row and float(row[field]) > 0 else None

    def record_trade(
        decision: RotationDecision,
        code: str,
        side: str,
        shares: float,
        raw: float,
        target_weight: float,
    ) -> None:
        nonlocal cash, transaction_cost, gross_traded_value
        slip = config.slippage_bps / 10_000
        fill = raw * (1 + slip if side == "BUY" else 1 - slip)
        value = fill * shares
        fee = max(value * config.commission_rate, config.minimum_commission) if shares > 0 else 0.0
        slippage = abs(fill - raw) * shares
        if side == "BUY":
            cash -= value + fee
            positions[code] = positions.get(code, 0) + shares
        else:
            cash += value - fee
            positions[code] = positions.get(code, 0) - shares
            if positions[code] <= 0:
                positions.pop(code, None)
        transaction_cost += fee + slippage
        gross_traded_value += raw * shares
        trade_rows.append(
            {
                "signal_date": decision.signal_date.strftime("%Y-%m-%d"),
                "execution_date": decision.execution_date.strftime("%Y-%m-%d"),
                "code": code,
                "side": side,
                "shares": round(shares, 6),
                "raw_price": round(raw, 4),
                "fill_price": round(fill, 4),
                "fee": round(fee, 2),
                "target_weight": round(target_weight, 4),
            }
        )

    for day in calendar:
        for code in list(positions):
            factor = raw_price(code, day, "adjustment_factor")
            previous_factor = last_adjustment_factor.get(code)
            if factor is not None and previous_factor is not None:
                positions[code] *= factor / previous_factor
            if factor is not None:
                last_adjustment_factor[code] = factor
        decision = decisions_by_date.get(day)
        if decision:
            open_equity = cash + sum(
                shares * (raw_price(code, day, "raw_open") or last_close.get(code, 0.0))
                for code, shares in positions.items()
            )
            desired_values = {
                code: open_equity * weight
                for code, weight in decision.target_weights.items()
            }
            for code, shares in list(positions.items()):
                raw = raw_price(code, day, "raw_open")
                if raw is None:
                    continue
                target_shares = int(desired_values.get(code, 0.0) / raw / config.lot_size) * config.lot_size
                sell_shares = max(0, shares - target_shares)
                if sell_shares:
                    record_trade(decision, code, "SELL", sell_shares, raw, decision.target_weights.get(code, 0.0))
            for code, target_value in desired_values.items():
                raw = raw_price(code, day, "raw_open")
                if raw is None:
                    continue
                current_shares = positions.get(code, 0)
                target_shares = int(target_value / raw / config.lot_size) * config.lot_size
                wanted = max(0, target_shares - current_shares)
                slip_fill = raw * (1 + config.slippage_bps / 10_000)
                affordable = int(
                    max(0.0, cash)
                    / (slip_fill * (1 + config.commission_rate))
                    / config.lot_size
                ) * config.lot_size
                while affordable > 0:
                    estimated_value = affordable * slip_fill
                    estimated_fee = max(
                        estimated_value * config.commission_rate,
                        config.minimum_commission,
                    )
                    if estimated_value + estimated_fee <= cash:
                        break
                    affordable -= config.lot_size
                buy_shares = min(wanted, affordable)
                if buy_shares:
                    record_trade(decision, code, "BUY", buy_shares, raw, decision.target_weights[code])
                    factor = raw_price(code, day, "adjustment_factor")
                    if factor is not None:
                        last_adjustment_factor[code] = factor

        market_value = 0.0
        for code, shares in positions.items():
            close = raw_price(code, day, "raw_close") or last_close.get(code)
            if close is None:
                continue
            last_close[code] = close
            market_value += close * shares
        risk_exposure_days += int(any(code in config.risk_assets for code in positions))
        equity_rows.append(
            {
                "date": day,
                "equity": cash + market_value,
                "cash": cash,
                "positions": dict(positions),
            }
        )

    equity = pd.DataFrame(equity_rows)
    metrics = _metrics(
        equity,
        initial_capital=config.initial_capital,
        transaction_cost=transaction_cost,
        gross_traded_value=gross_traded_value,
        trades=len(trade_rows),
        rebalances=len(decisions),
        risk_exposure_days=risk_exposure_days,
    )
    return {
        "metrics": metrics,
        "decisions": [
            {
                "signal_date": item.signal_date.strftime("%Y-%m-%d"),
                "execution_date": item.execution_date.strftime("%Y-%m-%d"),
                "target_weights": item.target_weights,
                "scores": item.scores,
            }
            for item in decisions
        ],
        "trades": trade_rows,
        "equity_curve": [
            {
                **row,
                "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                "equity": round(float(row["equity"]), 2),
                "cash": round(float(row["cash"]), 2),
            }
            for row in equity_rows
        ],
    }
