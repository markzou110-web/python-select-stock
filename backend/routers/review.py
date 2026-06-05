from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import text, bindparam
from typing import Dict, Any, List, Optional
import io
import pandas as pd

from core.db import get_db_engine
from core.logging_config import logger

router = APIRouter(prefix="/api/review", tags=["review"])


def _empty_response() -> Dict[str, Any]:
    return {
        "summary": {
            "signals": 0,
            "win_rate_5d": 0,
            "avg_return_5d": 0,
            "best_bucket": "暂无",
            "worst_bucket": "暂无",
        },
        "horizons": [],
        "by_strategy": [],
        "by_price_action": [],
        "by_pa_action": [],
        "by_pa_h2_quality": [],
        "by_pa_volume_pattern": [],
        "by_pa_trend_phase": [],
        "by_pa_weekly_context": [],
        "by_pa_trap_risk": [],
        "by_trade_bucket": [],
        "execution_summary": {},
        "by_market_regime": [],
        "by_next_open_gap": [],
        "recommendation_events": [],
        "portfolio_sim": {},
        "brooks_backtests": [],
        "by_industry": [],
        "recent_dates": [],
    }


def _load_scan_performance_df(days: int) -> pd.DataFrame:
    engine = get_db_engine()
    if not engine:
        return pd.DataFrame()

    query = text("""
        WITH signals AS (
            SELECT
                code, name, industry, strategy_type, date AS signal_date, price,
                price_action_pattern, price_action_regime, price_action_entry_quality,
                pa_trade_action, pa_trade_setup, pa_risk_pct,
                COALESCE(price_action_detail->>'pa_h2_quality', '未知') AS pa_h2_quality,
                COALESCE(price_action_detail->>'pa_volume_pattern', '未知') AS pa_volume_pattern,
                COALESCE(price_action_detail->>'pa_trend_phase', '未知') AS pa_trend_phase,
                COALESCE(price_action_detail->>'pa_weekly_context', '未知') AS pa_weekly_context,
                COALESCE(price_action_detail->>'trade_bucket', 'UNKNOWN') AS trade_bucket,
                COALESCE(price_action_detail->>'trade_eligible', 'false') AS trade_eligible,
                COALESCE((price_action_detail->>'final_trade_score')::float, score, 0) AS final_trade_score,
                COALESCE(price_action_detail->>'trade_blockers', '') AS trade_blockers,
                COALESCE(price_action_detail->>'market_regime', 'UNKNOWN') AS market_regime,
                CASE
                    WHEN COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) >= 75 THEN '高陷阱风险'
                    WHEN COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) >= 45 THEN '中陷阱风险'
                    ELSE '低陷阱风险'
                END AS pa_trap_risk_bucket,
                COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) AS pa_trap_risk,
                COALESCE((price_action_detail->>'pa_always_in_strength')::float, 0) AS pa_always_in_strength,
                COALESCE((price_action_detail->>'pa_failure_risk')::float, 0) AS pa_failure_risk,
                COALESCE(price_action_detail->>'pa_trend_damage', '无') AS pa_trend_damage
            FROM scan_history
            WHERE date >= CURRENT_DATE - (:days || ' days')::interval
              AND price IS NOT NULL
              AND price > 0
        ),
        future AS (
            SELECT
                s.code,
                s.name,
                s.industry,
                COALESCE(s.strategy_type, 'squeeze') AS strategy_type,
                s.price_action_pattern,
                s.price_action_regime,
                s.price_action_entry_quality,
                COALESCE(s.pa_trade_action, 'UNKNOWN') AS pa_trade_action,
                COALESCE(s.pa_trade_setup, s.price_action_pattern, '未知') AS pa_trade_setup,
                s.pa_risk_pct,
                s.pa_h2_quality,
                s.pa_volume_pattern,
                s.pa_trend_phase,
                s.pa_weekly_context,
                s.trade_bucket,
                s.trade_eligible,
                s.final_trade_score,
                s.trade_blockers,
                s.market_regime,
                s.pa_trap_risk_bucket,
                s.pa_trap_risk,
                s.pa_always_in_strength,
                s.pa_failure_risk,
                s.pa_trend_damage,
                s.signal_date,
                s.price,
                h1.close AS close_1d,
                h1.open AS open_1d,
                h3.close AS close_3d,
                h5.close AS close_5d,
                h10.close AS close_10d,
                h20.close AS close_20d
            FROM signals s
            LEFT JOIN LATERAL (
                SELECT open, close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '1 day' ORDER BY d.date DESC LIMIT 1
            ) h1 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '3 day' ORDER BY d.date DESC LIMIT 1
            ) h3 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '5 day' ORDER BY d.date DESC LIMIT 1
            ) h5 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '10 day' ORDER BY d.date DESC LIMIT 1
            ) h10 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = s.code AND d.date > s.signal_date AND d.date <= s.signal_date + INTERVAL '20 day' ORDER BY d.date DESC LIMIT 1
            ) h20 ON true
        )
        SELECT * FROM future
        ORDER BY signal_date DESC
    """)
    df = pd.read_sql(query, engine, params={"days": int(days)})
    if df.empty:
        return df
    for horizon in [1, 3, 5, 10, 20]:
        df[f"ret_{horizon}d"] = (df[f"close_{horizon}d"] - df["price"]) / df["price"] * 100
    df["next_open_gap_pct"] = (df["open_1d"] - df["price"]) / df["price"] * 100
    df["next_open_gap_bucket"] = pd.cut(
        df["next_open_gap_pct"],
        bins=[-999, -2, 1, 3, 999],
        labels=["低开<-2%", "平开-2~1%", "高开1~3%", "高开>3%"],
    ).astype(str).replace("nan", "未知")
    return df


@router.get("/scan-performance")
def get_scan_performance(days: int = 120) -> Dict[str, Any]:
    """
    Review historical scan signals by joining scan_history with future daily_k prices.
    A win is defined as a positive future return at the requested horizon.
    """
    engine = get_db_engine()
    if not engine:
        return _empty_response()

    try:
        df = _load_scan_performance_df(days)
        if df.empty:
            return _empty_response()

        def metric_frame(grouped: pd.DataFrame, key: str) -> List[Dict[str, Any]]:
            rows = []
            for value, group in grouped:
                returns = group["ret_5d"].dropna()
                if returns.empty:
                    continue
                rows.append({
                    key: value or "未知",
                    "signals": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 1),
                    "avg_return": round(float(returns.mean()), 2),
                    "best_return": round(float(returns.max()), 2),
                    "worst_return": round(float(returns.min()), 2),
                })
            rows.sort(key=lambda r: (r["win_rate"], r["avg_return"], r["signals"]), reverse=True)
            return rows[:12]

        horizons = []
        for horizon in [1, 3, 5, 10, 20]:
            returns = df[f"ret_{horizon}d"].dropna()
            if returns.empty:
                horizons.append({"horizon": f"{horizon}日", "signals": 0, "win_rate": 0, "avg_return": 0})
            else:
                horizons.append({
                    "horizon": f"{horizon}日",
                    "signals": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 1),
                    "avg_return": round(float(returns.mean()), 2),
                })

        by_strategy = metric_frame(df.groupby("strategy_type", dropna=False), "strategy")
        by_industry = metric_frame(df.groupby("industry", dropna=False), "industry")
        by_price_action = metric_frame(df.groupby("pa_trade_setup", dropna=False), "setup")
        by_pa_action = metric_frame(df.groupby("pa_trade_action", dropna=False), "action")
        by_pa_h2_quality = metric_frame(df.groupby("pa_h2_quality", dropna=False), "quality")
        by_pa_volume_pattern = metric_frame(df.groupby("pa_volume_pattern", dropna=False), "pattern")
        by_pa_trend_phase = metric_frame(df.groupby("pa_trend_phase", dropna=False), "phase")
        by_pa_weekly_context = metric_frame(df.groupby("pa_weekly_context", dropna=False), "context")
        by_pa_trap_risk = metric_frame(df.groupby("pa_trap_risk_bucket", dropna=False), "risk")
        by_trade_bucket = metric_frame(df.groupby("trade_bucket", dropna=False), "bucket")
        by_market_regime = metric_frame(df.groupby("market_regime", dropna=False), "regime")
        by_next_open_gap = metric_frame(df.groupby("next_open_gap_bucket", dropna=False), "bucket")

        ret_1d_all = df["ret_1d"].dropna()
        trade_ret_1d = df.loc[df["trade_bucket"].eq("TRADE"), "ret_1d"].dropna()
        blocked_ret_1d = df.loc[df["trade_bucket"].eq("BLOCK"), "ret_1d"].dropna()
        watch_ret_1d = df.loc[df["trade_bucket"].eq("WATCH"), "ret_1d"].dropna()
        execution_summary = {
            "trade_signals": int(len(trade_ret_1d)),
            "trade_win_rate_1d": round(float((trade_ret_1d > 0).mean() * 100), 1) if not trade_ret_1d.empty else 0,
            "trade_avg_return_1d": round(float(trade_ret_1d.mean()), 2) if not trade_ret_1d.empty else 0,
            "watch_signals": int(len(watch_ret_1d)),
            "watch_avg_return_1d": round(float(watch_ret_1d.mean()), 2) if not watch_ret_1d.empty else 0,
            "blocked_signals": int(len(blocked_ret_1d)),
            "blocked_avg_return_1d": round(float(blocked_ret_1d.mean()), 2) if not blocked_ret_1d.empty else 0,
            "filter_alpha_1d": round(float(trade_ret_1d.mean() - ret_1d_all.mean()), 2) if not trade_ret_1d.empty and not ret_1d_all.empty else 0,
        }

        def strategy_backtest(name: str, mask: pd.Series) -> Dict[str, Any]:
            returns = df.loc[mask, "ret_5d"].dropna()
            if returns.empty:
                return {"strategy": name, "signals": 0, "win_rate": 0, "avg_return": 0, "best_return": 0, "worst_return": 0}
            return {
                "strategy": name,
                "signals": int(len(returns)),
                "win_rate": round(float((returns > 0).mean() * 100), 1),
                "avg_return": round(float(returns.mean()), 2),
                "best_return": round(float(returns.max()), 2),
                "worst_return": round(float(returns.min()), 2),
            }

        brooks_backtests = [
            strategy_backtest("强H2二次入场", df["pa_h2_quality"].eq("强")),
            strategy_backtest("量能确认信号", df["pa_volume_pattern"].isin(["放量突破", "缩量回调后放量反包"])),
            strategy_backtest("强趋势持有", df["pa_always_in_strength"].fillna(0).ge(70)),
            strategy_backtest("低陷阱风险", df["pa_trap_risk"].fillna(100).le(45)),
            strategy_backtest("周线多头共振", df["pa_weekly_context"].isin(["周线多头", "周线向上突破"])),
            strategy_backtest("趋势破坏回避样本", df["pa_trend_damage"].isin(["跌破EMA20", "跌破EMA60", "短线低点破坏"])),
        ]

        recent = []
        for date_value, group in df.groupby("signal_date"):
            returns = group["ret_5d"].dropna()
            if returns.empty:
                continue
            recent.append({
                "date": str(date_value),
                "signals": int(len(returns)),
                "win_rate": round(float((returns > 0).mean() * 100), 1),
                "avg_return": round(float(returns.mean()), 2),
            })
        recent.sort(key=lambda r: r["date"], reverse=True)

        ret_5d = df["ret_5d"].dropna()
        best_bucket = by_industry[0]["industry"] if by_industry else "暂无"
        worst_bucket = by_industry[-1]["industry"] if by_industry else "暂无"
        return {
            "summary": {
                "signals": int(len(ret_5d)),
                "win_rate_5d": round(float((ret_5d > 0).mean() * 100), 1) if not ret_5d.empty else 0,
                "avg_return_5d": round(float(ret_5d.mean()), 2) if not ret_5d.empty else 0,
                "best_bucket": best_bucket,
                "worst_bucket": worst_bucket,
            },
            "horizons": horizons,
            "by_strategy": by_strategy,
            "by_price_action": by_price_action,
            "by_pa_action": by_pa_action,
            "by_pa_h2_quality": by_pa_h2_quality,
            "by_pa_volume_pattern": by_pa_volume_pattern,
            "by_pa_trend_phase": by_pa_trend_phase,
            "by_pa_weekly_context": by_pa_weekly_context,
            "by_pa_trap_risk": by_pa_trap_risk,
            "by_trade_bucket": by_trade_bucket,
            "by_market_regime": by_market_regime,
            "by_next_open_gap": by_next_open_gap,
            "execution_summary": execution_summary,
            "recommendation_events": get_recommendation_event_review(days=days, limit=12).get("items", []),
            "portfolio_sim": get_portfolio_simulation(days=days, max_daily=3, hold_days=5),
            "brooks_backtests": brooks_backtests,
            "by_industry": by_industry,
            "recent_dates": recent[:20],
        }
    except Exception as exc:
        logger.error(f"Review performance error: {exc}")
        return _empty_response()


@router.get("/recommendation-events")
def get_recommendation_event_review(days: int = 120, limit: int = 50) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return {"items": [], "summary": {}}
    try:
        df = pd.read_sql(text("""
            SELECT
                e.*,
                h1.close AS close_1d,
                h3.close AS close_3d,
                h5.close AS close_5d,
                h10.close AS close_10d
            FROM recommendation_events e
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = e.code AND d.date > e.event_date ORDER BY d.date ASC OFFSET 0 LIMIT 1
            ) h1 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = e.code AND d.date > e.event_date ORDER BY d.date ASC OFFSET 2 LIMIT 1
            ) h3 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = e.code AND d.date > e.event_date ORDER BY d.date ASC OFFSET 4 LIMIT 1
            ) h5 ON true
            LEFT JOIN LATERAL (
                SELECT close FROM daily_k d WHERE d.code = e.code AND d.date > e.event_date ORDER BY d.date ASC OFFSET 9 LIMIT 1
            ) h10 ON true
            WHERE e.event_date >= CURRENT_DATE - (:days || ' days')::interval
            ORDER BY e.event_date DESC, e.final_trade_score DESC NULLS LAST
            LIMIT :limit
        """), engine, params={"days": int(days), "limit": max(1, min(int(limit), 300))})
        if df.empty:
            return {"items": [], "summary": {}}
        for horizon in [1, 3, 5, 10]:
            df[f"ret_{horizon}d"] = (pd.to_numeric(df[f"close_{horizon}d"], errors="coerce") - df["recommendation_price"]) / df["recommendation_price"] * 100
        ret_5d = df["ret_5d"].dropna()
        return {
            "items": df.where(pd.notna(df), None).to_dict("records"),
            "summary": {
                "events": int(len(df)),
                "win_rate_5d": round(float((ret_5d > 0).mean() * 100), 1) if not ret_5d.empty else 0,
                "avg_return_5d": round(float(ret_5d.mean()), 2) if not ret_5d.empty else 0,
            },
        }
    except Exception as exc:
        logger.error(f"Recommendation event review error: {exc}")
        return {"items": [], "summary": {}, "error": str(exc)}


@router.get("/portfolio-sim")
def get_portfolio_simulation(days: int = 120, max_daily: int = 3, hold_days: int = 5) -> Dict[str, Any]:
    """Simple portfolio-level scan simulation: take top N TRADE events per day, equal-weight by trade."""
    engine = get_db_engine()
    if not engine:
        return {}
    try:
        df = _load_scan_performance_df(days)
        if df.empty:
            return {"trades": 0, "avg_return": 0, "win_rate": 0}
        ret_col = f"ret_{hold_days}d" if f"ret_{hold_days}d" in df.columns else "ret_5d"
        eligible = df[df["trade_bucket"].isin(["TRADE", "UNKNOWN"])].copy()
        eligible["rank_score"] = pd.to_numeric(eligible.get("final_trade_score", eligible.get("score", 0)), errors="coerce").fillna(0)
        picks = []
        for date_value, group in eligible.groupby("signal_date"):
            picks.append(group.sort_values("rank_score", ascending=False).head(max(1, int(max_daily))))
        if not picks:
            return {"trades": 0, "avg_return": 0, "win_rate": 0}
        picked = pd.concat(picks, ignore_index=True)
        returns = picked[ret_col].dropna()
        if returns.empty:
            return {"trades": 0, "avg_return": 0, "win_rate": 0}
        daily = picked.groupby("signal_date")[ret_col].mean().dropna()
        return {
            "trades": int(len(returns)),
            "days": int(len(daily)),
            "max_daily": int(max_daily),
            "hold_days": int(hold_days),
            "avg_return": round(float(returns.mean()), 2),
            "daily_avg_return": round(float(daily.mean()), 2),
            "win_rate": round(float((returns > 0).mean() * 100), 1),
            "total_compound_return": round(float(((1 + daily / 100).prod() - 1) * 100), 2) if not daily.empty else 0,
        }
    except Exception as exc:
        logger.error(f"Portfolio simulation error: {exc}")
        return {"trades": 0, "avg_return": 0, "win_rate": 0, "error": str(exc)}


@router.get("/next-day-followup")
def get_next_day_followup(date: Optional[str] = None, limit: int = 80) -> Dict[str, Any]:
    """Track what happened after one scan date: trigger, surge, fade, or stop-risk."""
    engine = get_db_engine()
    if not engine:
        return {"date": date, "items": [], "summary": {}, "error": "数据库未连接"}

    try:
        with engine.connect() as conn:
            if not date:
                row = conn.execute(text("SELECT MAX(date)::text AS date FROM scan_history")).mappings().first()
                date = row["date"] if row else None
            if not date:
                return {"date": None, "items": [], "summary": {}, "error": "暂无扫描历史"}

            scan_df = pd.read_sql(
                text("""
                    SELECT code, name, industry, date, price, score, strategy_type,
                           pa_trade_action, pa_trade_setup, pa_entry_price, pa_stop_price,
                           COALESCE(price_action_detail->>'trade_bucket', 'UNKNOWN') AS trade_bucket,
                           COALESCE(price_action_detail->>'trade_eligible', 'false') AS trade_eligible,
                           COALESCE(price_action_detail->>'trade_blockers', '') AS trade_blockers
                    FROM scan_history
                    WHERE date = :date
                    ORDER BY score DESC
                    LIMIT :limit
                """),
                conn,
                params={"date": date, "limit": max(1, min(int(limit), 300))},
            )
            if scan_df.empty:
                return {"date": date, "items": [], "summary": {}, "error": "该日期没有扫描结果"}

            codes = scan_df["code"].astype(str).str.zfill(6).tolist()
            k_query = text("""
                SELECT code, date, open, high, low, close
                FROM daily_k
                WHERE code IN :codes AND date > :date
                ORDER BY code, date
            """).bindparams(bindparam("codes", expanding=True))
            k_df = pd.read_sql(k_query, conn, params={"codes": codes, "date": date})

        if not k_df.empty:
            k_df["code"] = k_df["code"].astype(str).str.zfill(6)
            for col in ["open", "high", "low", "close"]:
                k_df[col] = pd.to_numeric(k_df[col], errors="coerce")

        items: List[Dict[str, Any]] = []
        for _, signal in scan_df.iterrows():
            code = str(signal.get("code", "")).zfill(6)
            base_price = float(signal.get("price") or 0)
            entry_line = float(signal.get("pa_entry_price") or 0)
            stop_line = float(signal.get("pa_stop_price") or 0)
            group = k_df[k_df["code"] == code] if not k_df.empty else pd.DataFrame()

            item = {
                "code": code,
                "name": signal.get("name"),
                "industry": signal.get("industry"),
                "signal_date": str(signal.get("date")),
                "signal_price": round(base_price, 2),
                "entry_line": round(entry_line, 2) if entry_line else None,
                "stop_line": round(stop_line, 2) if stop_line else None,
                "score": round(float(signal.get("score") or 0), 1),
                "strategy_type": signal.get("strategy_type"),
                "setup": signal.get("pa_trade_setup") or signal.get("pa_trade_action") or "历史信号",
                "trade_bucket": signal.get("trade_bucket") or "UNKNOWN",
                "trade_eligible": str(signal.get("trade_eligible")).lower() == "true",
                "trade_blockers": signal.get("trade_blockers") or "",
                "followup_status": "待跟踪",
                "execution_action": "待下一交易日确认",
                "max_gain_pct": None,
                "latest_gain_pct": None,
                "latest_close": None,
                "latest_date": None,
            }
            if group.empty or base_price <= 0:
                items.append(item)
                continue

            max_high = float(group["high"].max() or 0)
            min_low = float(group["low"].min() or 0)
            latest = group.iloc[-1]
            latest_close = float(latest.get("close") or 0)
            max_gain = (max_high - base_price) / base_price * 100
            latest_gain = (latest_close - base_price) / base_price * 100
            first_open = float(group.iloc[0].get("open") or 0)
            first_open_gap = (first_open - base_price) / base_price * 100 if first_open > 0 else None

            if stop_line and min_low <= stop_line:
                status = "风控触发"
            elif max_gain >= 9.5:
                status = "涨停验证"
            elif max_gain >= 5:
                status = "大涨验证"
            elif max_gain >= 3 and latest_gain < 1:
                status = "冲高回落"
            elif entry_line and max_high >= entry_line:
                status = "触发入场线"
            elif max_gain >= 2:
                status = "触发观察"
            else:
                status = "未触发"

            trade_bucket = item.get("trade_bucket")
            if trade_bucket == "BLOCK":
                action = "禁止追买：只复盘不交易"
            elif stop_line and min_low <= stop_line:
                action = "取消：已触发风控线"
            elif first_open_gap is not None and first_open_gap >= 9.5:
                action = "不追：涨停/一字高开"
            elif first_open_gap is not None and first_open_gap > 3:
                action = "不追：高开超过3%，等回踩"
            elif entry_line and max_high >= entry_line and latest_gain >= 0:
                action = "尾盘确认可试：小仓、贴近入场线"
            elif max_gain >= 3 and latest_gain < 1:
                action = "冲高回落：不买，等二次确认"
            elif trade_bucket == "WATCH":
                action = "观察：等回踩/放量站稳"
            else:
                action = "未触发：继续观察"

            item.update({
                "followup_status": status,
                "execution_action": action,
                "max_gain_pct": round(max_gain, 2),
                "latest_gain_pct": round(latest_gain, 2),
                "first_open_gap_pct": round(first_open_gap, 2) if first_open_gap is not None else None,
                "latest_close": round(latest_close, 2),
                "latest_date": str(latest.get("date")),
                "days_tracked": int(len(group)),
            })
            items.append(item)

        summary_status: Dict[str, int] = {}
        for item in items:
            status = item.get("followup_status") or "未知"
            summary_status[status] = summary_status.get(status, 0) + 1
        gains = [item["max_gain_pct"] for item in items if isinstance(item.get("max_gain_pct"), (int, float))]
        summary = {
            "signals": len(items),
            "tracked": len(gains),
            "avg_max_gain_pct": round(float(sum(gains) / len(gains)), 2) if gains else 0,
            "status_counts": summary_status,
        }
        return {"date": date, "items": items, "summary": summary}
    except Exception as exc:
        logger.error(f"Next-day followup error: {exc}")
        return {"date": date, "items": [], "summary": {}, "error": str(exc)}


@router.get("/scan-performance/export")
def export_scan_performance(days: int = 120):
    df = _load_scan_performance_df(days)
    if df.empty:
        df = pd.DataFrame(columns=[
            "code", "name", "industry", "strategy_type", "signal_date", "price",
            "pa_trade_action", "pa_trade_setup", "pa_risk_pct",
            "pa_h2_quality", "pa_volume_pattern", "pa_trend_phase", "pa_weekly_context", "pa_trap_risk_bucket",
            "trade_bucket", "trade_eligible", "final_trade_score", "trade_blockers",
            "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
        ])

    export_cols = [
        "code", "name", "industry", "strategy_type", "signal_date", "price",
        "pa_trade_action", "pa_trade_setup", "pa_risk_pct",
        "pa_h2_quality", "pa_volume_pattern", "pa_trend_phase", "pa_weekly_context", "pa_trap_risk_bucket",
        "trade_bucket", "trade_eligible", "final_trade_score", "trade_blockers",
        "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    ]
    export_df = df[[c for c in export_cols if c in df.columns]].copy()
    rename_map = {
        "code": "代码",
        "name": "名称",
        "industry": "行业",
        "strategy_type": "策略",
        "signal_date": "信号日期",
        "price": "信号价",
        "pa_trade_action": "Brooks动作",
        "pa_trade_setup": "Brooks形态",
        "pa_risk_pct": "Brooks风险%",
        "pa_h2_quality": "H2质量",
        "pa_volume_pattern": "量能行为",
        "pa_trend_phase": "趋势阶段",
        "pa_weekly_context": "周线环境",
        "pa_trap_risk_bucket": "陷阱风险",
        "trade_bucket": "交易桶",
        "trade_eligible": "可交易",
        "final_trade_score": "最终交易分",
        "trade_blockers": "过滤原因",
        "ret_1d": "1日收益%",
        "ret_3d": "3日收益%",
        "ret_5d": "5日收益%",
        "ret_10d": "10日收益%",
        "ret_20d": "20日收益%",
    }
    export_df = export_df.rename(columns=rename_map)
    for col in [c for c in export_df.columns if c.endswith("%")]:
        export_df[col] = export_df[col].round(2)

    buf = io.StringIO()
    buf.write("\ufeff")
    export_df.to_csv(buf, index=False)
    buf.seek(0)
    filename = f"alpha_vision_review_{days}d.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/next-day-followup/export")
def export_next_day_followup(date: Optional[str] = None, limit: int = 300):
    payload = get_next_day_followup(date=date, limit=limit)
    rows = payload.get("items") or []
    export_df = pd.DataFrame(rows)
    if export_df.empty:
        export_df = pd.DataFrame(columns=[
            "code", "name", "industry", "signal_date", "signal_price",
            "entry_line", "stop_line", "followup_status", "max_gain_pct",
            "latest_gain_pct", "latest_close", "latest_date", "setup", "execution_action",
        ])

    export_cols = [
        "code", "name", "industry", "signal_date", "signal_price",
        "entry_line", "stop_line", "followup_status", "max_gain_pct",
        "latest_gain_pct", "first_open_gap_pct", "latest_close", "latest_date",
        "days_tracked", "strategy_type", "setup", "trade_bucket", "trade_blockers", "execution_action",
    ]
    export_df = export_df[[c for c in export_cols if c in export_df.columns]].copy()
    rename_map = {
        "code": "代码",
        "name": "名称",
        "industry": "行业",
        "signal_date": "信号日期",
        "signal_price": "信号价",
        "entry_line": "入场线",
        "stop_line": "止损线",
        "followup_status": "跟踪状态",
        "execution_action": "执行动作",
        "max_gain_pct": "最高涨幅%",
        "latest_gain_pct": "最新表现%",
        "first_open_gap_pct": "次日开盘跳空%",
        "latest_close": "最新收盘",
        "latest_date": "最新日期",
        "days_tracked": "跟踪天数",
        "strategy_type": "策略",
        "setup": "形态",
        "trade_bucket": "交易桶",
        "trade_blockers": "过滤原因",
    }
    export_df = export_df.rename(columns=rename_map)
    for col in ["最高涨幅%", "最新表现%", "次日开盘跳空%"]:
        if col in export_df.columns:
            export_df[col] = pd.to_numeric(export_df[col], errors="coerce").round(2)

    buf = io.StringIO()
    buf.write("\ufeff")
    export_df.to_csv(buf, index=False)
    buf.seek(0)
    filename = f"alpha_vision_followup_{payload.get('date') or 'latest'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
