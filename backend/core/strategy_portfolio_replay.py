"""Cash- and risk-constrained portfolio replay for pre-registered R0/R1/R2 arms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from core.execution_labels import minimum_buy_shares


@dataclass(frozen=True)
class PortfolioConfig:
    name: str
    single_risk_pct: float
    total_risk_pct: float
    sector_risk_pct: float
    single_capital_pct: float
    total_capital_pct: float
    max_positions: int = 8


PORTFOLIO_CONFIGS = {
    "R0": PortfolioConfig("R0", 1.0, 6.0, 2.0, 25.0, 80.0),
    "R1": PortfolioConfig("R1", 0.5, 2.0, 1.0, 15.0, 60.0),
    "R2": PortfolioConfig("R2", 0.75, 3.0, 1.5, 20.0, 80.0),
}


def _buy_fee(value: float) -> float:
    return max(value * 0.00025, 5.0)


def _sell_fee(value: float) -> float:
    return max(value * 0.00025, 5.0) + value * 0.0005


def _max_consecutive_losses(pnls: list[float]) -> int:
    longest = current = 0
    for pnl in pnls:
        current = current + 1 if pnl < 0 else 0
        longest = max(longest, current)
    return longest


def simulate_portfolio(
    events: pd.DataFrame,
    daily_prices: pd.DataFrame,
    config: PortfolioConfig,
    *,
    initial_capital: float = 1_000_000.0,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Replay executable events with no leverage and point-in-time ranking."""
    if events is None or events.empty or daily_prices is None or daily_prices.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "config": config.name,
            "trades": [],
            "unfilled": [],
            "daily_equity": [],
        }

    required_events = {
        "code", "entry_date", "entry_price", "exit_date", "exit_price", "stop_price",
    }
    required_prices = {"date", "code", "close"}
    if not required_events.issubset(events.columns) or not required_prices.issubset(daily_prices.columns):
        return {
            "status": "INVALID_DATA",
            "config": config.name,
            "trades": [],
            "unfilled": [],
            "daily_equity": [],
        }

    candidates = events.copy()
    candidates["code"] = candidates["code"].astype(str)
    candidates["entry_date"] = pd.to_datetime(candidates["entry_date"], errors="coerce").dt.normalize()
    candidates["exit_date"] = pd.to_datetime(candidates["exit_date"], errors="coerce").dt.normalize()
    for column in ("entry_price", "exit_price", "stop_price", "score"):
        if column not in candidates:
            candidates[column] = 0.0
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce")
    candidates["industry"] = candidates.get("industry", pd.Series(index=candidates.index, dtype="object")).fillna("未知")
    candidates = candidates.dropna(subset=["entry_date", "entry_price", "stop_price"])

    prices = daily_prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.normalize()
    prices["code"] = prices["code"].astype(str)
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    if "volume" not in prices:
        prices["volume"] = float("nan")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    prices = prices.dropna(subset=["date", "code", "close"]).sort_values(["date", "code"])
    if prices.empty:
        return {
            "status": "INSUFFICIENT_DATA",
            "config": config.name,
            "trades": [],
            "unfilled": [],
            "daily_equity": [],
        }

    close_lookup = {
        (pd.Timestamp(row.date), str(row.code)): float(row.close)
        for row in prices.itertuples(index=False)
    }
    dates = list(pd.DatetimeIndex(prices["date"].drop_duplicates()).sort_values())
    cash = float(initial_capital)
    open_positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    unfilled: list[dict[str, Any]] = []
    daily_equity: list[dict[str, Any]] = []
    closed_pnls: list[float] = []
    last_close: dict[str, float] = {}
    peak = float(initial_capital)
    max_drawdown = 0.0
    utilization_sum = 0.0
    max_sector_risk_pct = 0.0
    daily_halt_count = 0
    floating_halt_count = 0
    prior_floating_halt = False
    prior_daily_halt = False
    last_volume: dict[str, float] = {}

    def marked_equity(day: pd.Timestamp, *, at_close: bool = False) -> tuple[float, float, float]:
        market_value = 0.0
        cost = 0.0
        unrealized = 0.0
        for position in open_positions.values():
            code = position["code"]
            mark = last_close.get(code, position["entry_price"])
            if position["entry_date"] == day:
                mark = position["entry_price"]
            if at_close:
                mark = close_lookup.get((day, code), mark)
                last_close[code] = mark
            value = mark * position["shares"]
            market_value += value
            cost += position["entry_value"]
            unrealized += value - position["entry_value"]
        return cash + market_value, market_value, unrealized / cost * 100 if cost > 0 else 0.0

    for day in dates:
        scheduled_exit_pnl = 0.0
        for position in open_positions.values():
            if pd.isna(position["exit_date"]) or position["exit_date"] != day:
                continue
            exit_price = float(position["exit_price"] or 0)
            if exit_price <= 0:
                continue
            sell_value = exit_price * position["shares"]
            scheduled_exit_pnl += (
                sell_value
                - _sell_fee(sell_value)
                - position["entry_value"]
                - position["buy_fee"]
            )
        daily_halt = scheduled_exit_pnl < -abs(float(initial_capital)) * 0.05
        if daily_halt:
            daily_halt_count += 1

        day_candidates = candidates[candidates["entry_date"].eq(day)].sort_values(
            ["score", "code"],
            ascending=[False, True],
        )
        for row in day_candidates.itertuples(index=False):
            code = str(row.code)

            def reject(reason: str) -> None:
                unfilled.append(
                    {
                        "code": code,
                        "entry_date": day.strftime("%Y-%m-%d"),
                        "reason": reason,
                    }
                )

            if daily_halt or prior_daily_halt or prior_floating_halt:
                reject("组合熔断暂停新增")
                continue
            if code in open_positions:
                reject("同一股票已有持仓")
                continue
            if len(open_positions) >= config.max_positions:
                reject("最大持仓数量已满")
                continue

            entry_price = float(row.entry_price or 0)
            stop_price = float(row.stop_price or 0)
            risk_fraction = (entry_price - stop_price) / entry_price if entry_price > 0 else 0.0
            if entry_price <= 0 or risk_fraction <= 0 or risk_fraction >= 1:
                reject("入场价或有效止损无效")
                continue

            equity, market_value, _floating_pct = marked_equity(day)
            open_risk = sum(position["account_risk_amount"] for position in open_positions.values())
            industry = str(row.industry or "未知")
            sector_risk = sum(
                position["account_risk_amount"]
                for position in open_positions.values()
                if industry != "未知" and position["industry"] == industry
            )
            total_risk_room = equity * config.total_risk_pct / 100 - open_risk
            sector_risk_room = (
                equity * config.sector_risk_pct / 100 - sector_risk
                if industry != "未知"
                else float("inf")
            )
            if total_risk_room <= 0:
                reject("总开放风险额度不足")
                continue
            if sector_risk_room <= 0:
                reject("同板块风险额度不足")
                continue

            event_market_cap = float(getattr(row, "market_capital_pct", 100.0) or 100.0)
            total_capital_pct = min(config.total_capital_pct, event_market_cap)
            single_capital_pct = config.single_capital_pct
            if config.name == "R0":
                production_cap = float(
                    getattr(row, "r0_single_capital_pct", single_capital_pct)
                    or single_capital_pct
                )
                single_capital_pct = min(single_capital_pct, production_cap)
            capital_rooms = [
                equity * config.single_risk_pct / 100 / risk_fraction,
                equity * single_capital_pct / 100,
                equity * total_capital_pct / 100 - market_value,
                total_risk_room / risk_fraction,
                sector_risk_room / risk_fraction,
                cash,
            ]
            target_value = max(0.0, min(capital_rooms))
            minimum_shares = minimum_buy_shares(code)
            lot_size = 100
            shares = int(target_value / entry_price / lot_size) * lot_size
            prior_volume = last_volume.get(code)
            if prior_volume is not None and prior_volume >= 0:
                capacity_shares = int(prior_volume * 0.05 / lot_size) * lot_size
                shares = min(shares, capacity_shares)
            affordable = int(max(0.0, cash - 5.0) / entry_price / lot_size) * lot_size
            shares = min(shares, affordable)
            if shares < minimum_shares:
                binding_index = capital_rooms.index(min(capital_rooms))
                reason = {
                    2: "总资金使用额度不足",
                    3: "总开放风险额度不足",
                    4: "同板块风险额度不足",
                    5: "现金不足",
                }.get(binding_index, "仓位不足最低申报数量")
                reject(reason)
                continue

            entry_value = entry_price * shares
            buy_fee = _buy_fee(entry_value)
            if entry_value + buy_fee > cash:
                reject("现金不足")
                continue
            account_risk = entry_value * risk_fraction
            cash -= entry_value + buy_fee
            trade = {
                "code": code,
                "industry": industry,
                "entry_date": day,
                "exit_date": row.exit_date,
                "entry_price": entry_price,
                "exit_price": float(row.exit_price) if pd.notna(row.exit_price) else None,
                "stop_price": stop_price,
                "shares": shares,
                "entry_value": round(entry_value, 2),
                "buy_fee": round(buy_fee, 2),
                "account_risk_amount": round(account_risk, 2),
                "status": "OPEN",
            }
            trades.append(trade)
            open_positions[code] = trade

        # ponytail: date-only exits have unknown intraday order. Settle them
        # after entries; timestamped fills are needed to reuse same-day proceeds.
        for code in list(open_positions):
            position = open_positions[code]
            if pd.isna(position["exit_date"]) or position["exit_date"] != day:
                continue
            exit_price = float(position["exit_price"] or 0)
            if exit_price <= 0:
                continue
            sell_value = exit_price * position["shares"]
            sell_fee = _sell_fee(sell_value)
            cash += sell_value - sell_fee
            pnl = sell_value - sell_fee - position["entry_value"] - position["buy_fee"]
            position.update(
                {
                    "status": "CLOSED",
                    "sell_fee": round(sell_fee, 2),
                    "pnl": round(pnl, 2),
                    "return_pct": round(pnl / (position["entry_value"] + position["buy_fee"]) * 100, 4),
                }
            )
            closed_pnls.append(pnl)
            del open_positions[code]

        prior_daily_halt = daily_halt
        for row in prices[prices["date"].eq(day)].itertuples(index=False):
            if pd.notna(row.volume):
                last_volume[str(row.code)] = float(row.volume)

        equity, market_value, floating_pct = marked_equity(day, at_close=True)
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak * 100 if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        utilization = market_value / equity * 100 if equity > 0 else 0.0
        utilization_sum += utilization
        sector_risks: dict[str, float] = {}
        for position in open_positions.values():
            if position["industry"] == "未知":
                continue
            sector_risks[position["industry"]] = (
                sector_risks.get(position["industry"], 0.0)
                + position["account_risk_amount"]
            )
        if sector_risks and equity > 0:
            max_sector_risk_pct = max(
                max_sector_risk_pct,
                max(sector_risks.values()) / equity * 100,
            )
        prior_floating_halt = floating_pct < -5.0
        if prior_floating_halt:
            floating_halt_count += 1
        daily_equity.append(
            {
                "date": day.strftime("%Y-%m-%d"),
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "utilization_pct": round(utilization, 2),
                "floating_pnl_pct": round(floating_pct, 2),
            }
        )

    final_equity = daily_equity[-1]["equity"] if daily_equity else float(initial_capital)
    total_return = final_equity / initial_capital - 1 if initial_capital > 0 else 0.0
    years = len(dates) / 252
    annualized = (final_equity / initial_capital) ** (1 / years) - 1 if years > 0 and final_equity > 0 else 0.0
    benchmark_return_pct = None
    if benchmark is not None and not benchmark.empty and {"date", "close"}.issubset(benchmark.columns):
        bench = benchmark.copy()
        bench["date"] = pd.to_datetime(bench["date"], errors="coerce").dt.normalize()
        bench["close"] = pd.to_numeric(bench["close"], errors="coerce")
        bench = bench.dropna(subset=["date", "close"]).sort_values("date")
        bench = bench[bench["date"].between(dates[0], dates[-1])]
        if len(bench) >= 2 and float(bench["close"].iloc[0]) > 0:
            benchmark_return_pct = (
                float(bench["close"].iloc[-1]) / float(bench["close"].iloc[0]) - 1
            ) * 100
    closed = sum(trade["status"] == "CLOSED" for trade in trades)
    return {
        "status": "SHADOW_ONLY",
        "config": config.name,
        "execution_assumption": "entries_before_date_only_exits_prior_close_sizing",
        "liquidity_basis": "previous_available_volume_in_shares",
        "initial_capital": round(float(initial_capital), 2),
        "final_equity": round(float(final_equity), 2),
        "total_return_pct": round(total_return * 100, 4),
        "benchmark_return_pct": (
            round(benchmark_return_pct, 4)
            if benchmark_return_pct is not None
            else None
        ),
        "relative_benchmark_return_pct": (
            round(total_return * 100 - benchmark_return_pct, 4)
            if benchmark_return_pct is not None
            else None
        ),
        "annualized_return_pct": round(annualized * 100, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "calmar": round(annualized * 100 / max_drawdown, 4) if max_drawdown > 0 else None,
        "avg_utilization_pct": round(utilization_sum / len(dates), 2) if dates else 0.0,
        "max_consecutive_losses": _max_consecutive_losses(closed_pnls),
        "max_sector_risk_pct": round(max_sector_risk_pct, 4),
        "closed_trades": closed,
        "open_positions": len(open_positions),
        "unfilled_count": len(unfilled),
        "unfilled_rate_pct": round(len(unfilled) / len(candidates) * 100, 2) if len(candidates) else 0.0,
        "daily_loss_halt_days": daily_halt_count,
        "floating_loss_halt_days": floating_halt_count,
        "trades": trades,
        "unfilled": unfilled,
        "daily_equity": daily_equity,
    }


def compare_portfolio_configs(
    events: pd.DataFrame,
    daily_prices: pd.DataFrame,
    *,
    initial_capital: float = 1_000_000.0,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Run the three frozen configs without optimizing a missing drawdown budget."""
    reports = {
        name: simulate_portfolio(
            events,
            daily_prices,
            config,
            initial_capital=initial_capital,
            benchmark=benchmark,
        )
        for name, config in PORTFOLIO_CONFIGS.items()
    }
    return {
        "status": "E3_NOT_REACHED",
        "selected_config": None,
        "selection_blocker": "用户尚未冻结可接受的组合最大回撤数值，不能事后选择R0/R1/R2",
        "configs": reports,
    }
