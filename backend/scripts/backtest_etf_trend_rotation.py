"""Fetch representative A-share ETFs and replay the pre-registered rotation policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import akshare as ak
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.etf_trend_rotation import RotationConfig, run_etf_rotation


ETF_NAMES = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "159915": "创业板ETF",
    "512100": "中证1000ETF",
    "588000": "科创50ETF",
    "512890": "红利低波ETF",
    "511010": "5年期国债ETF",
}
RISK_ASSETS = tuple(code for code in ETF_NAMES if code != "511010")
DEFENSIVE_ASSET = "511010"


def _cache_path(cache_dir: Path, code: str, start_date: str, end_date: str) -> Path:
    return cache_dir / f"{code}_total_return_v1_{start_date}_{end_date}.csv"


def _fetch_tencent_series(
    code: str,
    start_date: str,
    end_date: str,
    adjustment: str,
) -> pd.DataFrame:
    market_code = f"sh{code}" if code.startswith(("5", "6")) else f"sz{code}"
    cursor = pd.Timestamp(end_date)
    start = pd.Timestamp(start_date)
    rows: list[list[Any]] = []
    session = requests.Session()
    session.trust_env = False
    for _ in range(30):
        parameter = f"{market_code},day,,{cursor.strftime('%Y-%m-%d')},320,{adjustment}"
        response = session.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": parameter},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        item = (payload.get("data") or {}).get(market_code) or {}
        page = item.get(f"{adjustment}day") if adjustment else item.get("day")
        page = page or []
        if not page:
            break
        rows.extend(page)
        earliest = pd.Timestamp(page[0][0])
        if earliest <= start:
            break
        cursor = earliest - pd.Timedelta(days=1)
    if not rows:
        raise RuntimeError("Tencent returned no ETF history")
    frame = pd.DataFrame(rows).iloc[:, :3]
    frame.columns = ["date", "open", "close"]
    frame["date"] = pd.to_datetime(frame["date"])
    return (
        frame[(frame["date"] >= start) & (frame["date"] <= pd.Timestamp(end_date))]
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _combine_raw_and_adjusted(raw: pd.DataFrame, adjusted: pd.DataFrame) -> pd.DataFrame:
    raw = raw.copy()
    adjusted = adjusted.copy()
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    adjusted["date"] = pd.to_datetime(adjusted["date"], errors="coerce")
    for frame in (raw, adjusted):
        frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    combined = raw.merge(adjusted, on="date", how="inner", suffixes=("_raw", "_adjusted"))
    combined["adjustment_factor"] = combined["close_adjusted"] / combined["close_raw"]
    return combined.rename(
        columns={
            "open_adjusted": "open",
            "close_adjusted": "close",
            "open_raw": "raw_open",
            "close_raw": "raw_close",
        }
    )[["date", "open", "close", "raw_open", "raw_close", "adjustment_factor"]]


def _fetch_tencent_prices(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    raw = _fetch_tencent_series(code, start_date, end_date, "")
    adjusted = _fetch_tencent_series(code, start_date, end_date, "hfq")
    return _combine_raw_and_adjusted(raw, adjusted)


def fetch_etf_prices(
    code: str,
    *,
    start_date: str,
    end_date: str,
    cache_dir: Path,
    retries: int = 3,
) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = _cache_path(cache_dir, code, start_date, end_date)
    last_error: Exception | None = None
    try:
        frame = _fetch_tencent_prices(code, start_date, end_date)
        frame.to_csv(cached, index=False)
        return frame
    except Exception as exc:  # pragma: no cover - depends on public data source
        last_error = exc
    for attempt in range(max(1, retries)):
        try:
            raw = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="",
            )
            adjusted = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="hfq",
            )
            raw = raw.rename(columns={"日期": "date", "开盘": "open", "收盘": "close"})[
                ["date", "open", "close"]
            ]
            adjusted = adjusted.rename(
                columns={"日期": "date", "开盘": "open", "收盘": "close"}
            )[["date", "open", "close"]]
            frame = _combine_raw_and_adjusted(raw, adjusted)
            frame.to_csv(cached, index=False)
            return frame
        except Exception as exc:  # pragma: no cover - depends on public data source
            last_error = exc
            time.sleep(min(2**attempt, 4))
    if cached.exists():
        frame = pd.read_csv(cached)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame[
            (frame["date"] >= pd.Timestamp(start_date))
            & (frame["date"] <= pd.Timestamp(end_date))
        ]
        if not frame.empty:
            return frame.reset_index(drop=True)
    raise RuntimeError(f"ETF {code} data unavailable: {type(last_error).__name__}") from last_error


def _data_manifest(prices: dict[str, pd.DataFrame]) -> dict[str, Any]:
    manifest = {}
    for code, frame in prices.items():
        canonical = frame.copy()
        canonical["date"] = pd.to_datetime(canonical["date"]).dt.strftime("%Y-%m-%d")
        payload = canonical.to_csv(index=False).encode("utf-8")
        manifest[code] = {
            "name": ETF_NAMES[code],
            "rows": len(frame),
            "start_date": canonical["date"].min(),
            "end_date": canonical["date"].max(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return manifest


def _curve_metrics(curve: pd.Series, *, initial_value: float | None = None) -> dict[str, Any]:
    curve = pd.to_numeric(curve, errors="coerce").dropna()
    if len(curve) < 2:
        return {"status": "INSUFFICIENT_DATA"}
    base = float(initial_value) if initial_value is not None else float(curve.iloc[0])
    measured_curve = curve
    if initial_value is not None:
        measured_curve = pd.concat(
            [
                pd.Series(
                    [base],
                    index=[curve.index[0] - pd.Timedelta(nanoseconds=1)],
                ),
                curve,
            ]
        )
    returns = measured_curve.pct_change().dropna()
    elapsed = max((curve.index[-1] - curve.index[0]).days / 365.25, 1 / 252)
    total = float(curve.iloc[-1] / base - 1)
    annual = float((1 + total) ** (1 / elapsed) - 1) if total > -1 else -1.0
    drawdown = measured_curve / measured_curve.cummax() - 1
    volatility = float(returns.std(ddof=0) * np.sqrt(252))
    return {
        "status": "OK",
        "start_date": curve.index[0].strftime("%Y-%m-%d"),
        "end_date": curve.index[-1].strftime("%Y-%m-%d"),
        "total_return_pct": round(total * 100, 2),
        "annual_return_pct": round(annual * 100, 2),
        "annual_volatility_pct": round(volatility * 100, 2),
        "sharpe": round(float(returns.mean() / returns.std(ddof=0) * np.sqrt(252)), 3) if volatility else 0.0,
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "calmar": round(annual / abs(float(drawdown.min())), 3) if float(drawdown.min()) < 0 else None,
    }


def _benchmark_curve(
    frame: pd.DataFrame,
    *,
    start_date: str,
    capital: float,
    commission_rate: float,
    minimum_commission: float,
    slippage_bps: float,
) -> pd.Series:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"]).dt.normalize()
    work = work[work["date"] >= pd.Timestamp(start_date)].sort_values("date")
    if work.empty:
        return pd.Series(dtype=float)
    fill = float(work.iloc[0]["raw_open"]) * (1 + slippage_bps / 10_000)
    shares = int(capital / fill / 100) * 100
    fee = max(shares * fill * commission_rate, minimum_commission)
    while shares > 0 and shares * fill + fee > capital:
        shares -= 100
        fee = max(shares * fill * commission_rate, minimum_commission) if shares else 0.0
    cash = capital - shares * fill - fee
    starting_factor = float(work.iloc[0]["adjustment_factor"])
    adjusted_shares = shares * pd.to_numeric(
        work["adjustment_factor"], errors="coerce"
    ).to_numpy() / starting_factor
    return pd.Series(
        cash + adjusted_shares * pd.to_numeric(work["raw_close"], errors="coerce").to_numpy(),
        index=pd.DatetimeIndex(work["date"]),
    )


def _segmented_metrics(curve: pd.Series, *, initial_value: float) -> dict[str, Any]:
    boundaries = {
        "train_2014_2019": (None, pd.Timestamp("2019-12-31")),
        "validation_2020_2022": (pd.Timestamp("2020-01-01"), pd.Timestamp("2022-12-31")),
        "test_2023_present": (pd.Timestamp("2023-01-01"), None),
    }
    metrics: dict[str, Any] = {}
    for name, (start, end) in boundaries.items():
        values = curve
        if start is not None:
            values = values[values.index >= start]
        if end is not None:
            values = values[values.index <= end]
        previous = curve[curve.index < values.index[0]] if not values.empty else pd.Series(dtype=float)
        base = float(previous.iloc[-1]) if not previous.empty else initial_value
        metrics[name] = _curve_metrics(values, initial_value=base)
    return metrics


def run(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = Path(args.cache_dir).expanduser()
    prices = {
        code: fetch_etf_prices(
            code,
            start_date=args.start_date,
            end_date=args.end_date,
            cache_dir=cache_dir,
        )
        for code in ETF_NAMES
    }
    config = RotationConfig(
        risk_assets=RISK_ASSETS,
        defensive_asset=DEFENSIVE_ASSET,
        initial_capital=args.initial_capital,
    )
    result = run_etf_rotation(prices, config)
    stress_config = RotationConfig(
        risk_assets=RISK_ASSETS,
        defensive_asset=DEFENSIVE_ASSET,
        initial_capital=args.initial_capital,
        commission_rate=0.0005,
        slippage_bps=15.0,
    )
    stress_result = run_etf_rotation(prices, stress_config)
    equity = pd.DataFrame(result["equity_curve"])
    if equity.empty:
        report = {
            "meta": {
                "version": "etf-absolute-relative-rotation-v1",
                "generated_on": date.today().isoformat(),
                "status": "INSUFFICIENT_DATA",
            },
            "config": {
                "risk_assets": list(config.risk_assets),
                "defensive_asset": config.defensive_asset,
            },
            "data_manifest": _data_manifest(prices),
            "strategy": {"metrics": result["metrics"]},
        }
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report
    curve = pd.Series(
        pd.to_numeric(equity["equity"], errors="coerce").to_numpy(),
        index=pd.to_datetime(equity["date"]),
    )
    benchmark_curve = _benchmark_curve(
        prices["510300"],
        start_date=result["metrics"]["start_date"],
        capital=config.initial_capital,
        commission_rate=config.commission_rate,
        minimum_commission=config.minimum_commission,
        slippage_bps=config.slippage_bps,
    )
    yearly_values = curve.resample("YE").last()
    yearly_returns = pd.Series(
        np.r_[config.initial_capital, yearly_values.to_numpy()]
    ).pct_change().dropna()
    selection_counts: dict[str, int] = {}
    for decision in result["decisions"]:
        for code, weight in decision["target_weights"].items():
            if weight > 0:
                selection_counts[code] = selection_counts.get(code, 0) + 1
    report = {
        "meta": {
            "version": "etf-absolute-relative-rotation-v1",
            "generated_on": date.today().isoformat(),
            "hypothesis": "Liquid broad/style ETFs with positive absolute trend and superior risk-adjusted momentum outperform a single equity benchmark with lower drawdown.",
            "signal_timing": "Month-end close signal; next A-share session open execution",
            "costs": "5bp slippage each side; 0.025% commission with CNY5 minimum; 100-share lots; no ETF stamp tax",
            "price_mode": "Backward-adjusted closes for point-in-time signals; raw OHLC for fills; adjustment-factor changes applied to holdings as total-return reinvestment",
            "universe": {code: ETF_NAMES[code] for code in ETF_NAMES},
            "limitations": [
                "The representative ETF list is fixed today and therefore retains product-selection survivorship bias.",
                "Free public adjusted ETF data may be revised and is not an institutional point-in-time database.",
                "Corporate actions are represented as synthetic total-return reinvestment; discretionary orders use raw prices, but fractional corporate-action residuals are an approximation.",
            ],
        },
        "config": {
            "trend_days": config.trend_days,
            "momentum_short_days": config.momentum_short_days,
            "momentum_long_days": config.momentum_long_days,
            "volatility_days": config.volatility_days,
            "target_volatility": config.target_volatility,
            "top_n": config.top_n,
            "risk_assets": list(config.risk_assets),
            "defensive_asset": config.defensive_asset,
        },
        "data_manifest": _data_manifest(prices),
        "strategy": {
            "metrics": result["metrics"],
            "segments": _segmented_metrics(curve, initial_value=config.initial_capital),
            "yearly_returns_pct": {
                str(index.year): round(float(value) * 100, 2)
                for index, value in zip(yearly_values.index, yearly_returns, strict=True)
            },
            "selection_counts": selection_counts,
            "decisions": result["decisions"],
            "trades": result["trades"],
        },
        "cost_stress": {
            "assumption": "15bp slippage each side and 0.05% commission; all other rules unchanged",
            "metrics": stress_result["metrics"],
        },
        "benchmark_510300": {
            "metrics": _curve_metrics(benchmark_curve, initial_value=config.initial_capital),
            "segments": _segmented_metrics(
                benchmark_curve,
                initial_value=config.initial_capital,
            ),
        },
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the pre-registered A-share ETF rotation policy")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--cache-dir", default=str(Path(tempfile.gettempdir()) / "alphavision_etf_cache"))
    parser.add_argument("--output", default="/tmp/etf_trend_rotation_report.json")
    args = parser.parse_args()
    report = run(args)
    print(json.dumps({
        "output": args.output,
        "strategy": report["strategy"]["metrics"],
        "benchmark": report.get("benchmark_510300", {}).get("metrics"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
