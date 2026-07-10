from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import text, bindparam
from typing import Dict, Any, List, Optional
import io
import json
import pandas as pd

from core.db import get_db_engine
from core.db import get_scan_dates, get_scan_history_by_date
from core.daily_strategy_report import build_daily_strategy_report, build_daily_strategy_report_body
from core.logging_config import logger
from core.outcome_calibration import build_blocker_report, build_calibration_report, load_scan_outcomes
from core.performance_metrics import return_metrics
from core.pro_workflow import classify_strategy_health
from core.research_context import build_ai_research_context, get_global_market_context
from core.research_radar import build_candidate_research_radar
from core.strategy_health import build_strategy_health
from core.data import get_index_data

router = APIRouter(prefix="/api/review", tags=["review"])


def _measurement_contract(scope: str) -> Dict[str, Any]:
    contracts = {
        "scan": {
            "sample_unit": "code + signal_date + strategy_type scan snapshot",
            "entry_price": "scan_history.price",
            "risk_model": "raw future close; execution filters reported separately",
        },
        "recommendation_event": {
            "sample_unit": "one persisted recommendation event",
            "entry_price": "recommendation_events.recommendation_price",
            "risk_model": "raw future close; no stop/take simulation",
        },
        "calibration": {
            "sample_unit": "latest code + signal_date + strategy_type snapshot",
            "entry_price": "daily_k signal-date close",
            "risk_model": "raw future close; blocker comparison is diagnostic",
        },
        "profitability_layers": {
            "sample_unit": "layer-specific scan, recommendation event, or real trade",
            "entry_price": "scan price / recommendation price / actual trade price by layer",
            "risk_model": "raw future close; layers are not directly interchangeable",
        },
    }
    return {
        "scope": scope,
        **contracts[scope],
        "horizons": [1, 3, 5, 10],
        "maturity_rule": "future Nth trading-day close must exist",
        "return_type": "absolute_return_pct",
        "benchmark_adjusted": False,
        "version": "measurement-contract-v1",
    }


def _empty_response() -> Dict[str, Any]:
    return {
        "measurement_contract": _measurement_contract("scan"),
        "summary": {
            "signals": 0,
            "win_rate_5d": 0,
            "avg_return_5d": 0,
            "expected_return_5d": 0,
            "profit_loss_ratio_5d": 0,
            "ci95_low_5d": 0,
            "ci95_high_5d": 0,
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
        "by_market_sentiment": [],
        "by_next_open_gap": [],
        "by_sector_phase": [],
        "by_sector_role": [],
        "by_sector_mainline": [],
        "by_trade_state": [],
        "by_opportunity_bucket": [],
        "by_sector_alignment": [],
        "by_sector_strength": [],
        "by_stock_sector_fit": [],
        "data_quality": {},
        "path_metrics": {},
        "score_buckets": {},
        "by_price_action_version": [],
        "recommendation_events": [],
        "portfolio_sim": {},
        "brooks_backtests": [],
        "by_industry": [],
        "recent_dates": [],
    }


def _count_recommendation_events_for_date(scan_date: str) -> int:
    engine = get_db_engine()
    if not engine or not scan_date:
        return 0
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("""
                SELECT COUNT(*)
                FROM recommendation_events
                WHERE event_date = :scan_date
                  AND COALESCE(source, '') LIKE 'bark%'
            """), {"scan_date": scan_date}).scalar() or 0)
    except Exception:
        return 0


def _load_scan_performance_df(days: int) -> pd.DataFrame:
    engine = get_db_engine()
    if not engine:
        return pd.DataFrame()

    query = text("""
        WITH signals AS (
            SELECT
                code, name, industry, strategy_type, COALESCE(data_date, date) AS signal_date, price,
                pa_entry_price, pa_stop_price, pa_target_price, pa_risk_reward,
                price_action_pattern, price_action_regime, price_action_entry_quality,
                pa_trade_action, pa_trade_setup, pa_risk_pct,
                COALESCE(price_action_detail->>'pa_h2_quality', '未知') AS pa_h2_quality,
                COALESCE(price_action_detail->>'pa_volume_pattern', '未知') AS pa_volume_pattern,
                COALESCE(price_action_detail->>'pa_trend_phase', '未知') AS pa_trend_phase,
                COALESCE(price_action_detail->>'pa_weekly_context', '未知') AS pa_weekly_context,
                COALESCE(price_action_detail->>'trade_bucket', 'UNKNOWN') AS trade_bucket,
                COALESCE(price_action_detail->>'trade_eligible', 'false') AS trade_eligible,
                COALESCE((price_action_detail->>'early_trade_candidate')::boolean, false) AS early_trade_candidate,
                COALESCE(price_action_detail->>'early_trade_grade', '') AS early_trade_grade,
                COALESCE((price_action_detail->>'final_trade_score')::float, score, 0) AS final_trade_score,
                COALESCE((price_action_detail->>'calibrated_score')::float, score, 0) AS calibrated_score,
                COALESCE((price_action_detail->>'research_eligible')::boolean, false) AS research_eligible,
                COALESCE(price_action_detail->>'trade_blockers', '') AS trade_blockers,
                COALESCE(price_action_detail->>'market_regime', 'UNKNOWN') AS market_regime,
                COALESCE(price_action_detail->>'market_sentiment_stage', 'UNKNOWN') AS market_sentiment_stage,
                COALESCE(price_action_detail->>'sector_phase', 'UNKNOWN') AS sector_phase,
                COALESCE(price_action_detail->>'sector_role', 'UNKNOWN') AS sector_role,
                COALESCE(price_action_detail->>'sector_mainline', 'UNKNOWN') AS sector_mainline,
                COALESCE(price_action_detail->>'trade_state', 'UNKNOWN') AS trade_state,
                COALESCE((price_action_detail->>'trade_opportunity_score')::float, 0) AS trade_opportunity_score,
                COALESCE((price_action_detail->>'sector_strength_score')::float, 0) AS sector_strength_score,
                COALESCE((price_action_detail->>'stock_sector_fit_score')::float, 0) AS stock_sector_fit_score,
                COALESCE((price_action_detail->>'sector_alignment_score')::float, 0) AS sector_alignment_score,
                COALESCE((price_action_detail->>'sector_relative_pct')::float, 0) AS sector_relative_pct,
                CASE
                    WHEN COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) >= 75 THEN '高陷阱风险'
                    WHEN COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) >= 45 THEN '中陷阱风险'
                    ELSE '低陷阱风险'
                END AS pa_trap_risk_bucket,
                COALESCE((price_action_detail->>'pa_trap_risk')::float, 0) AS pa_trap_risk,
                COALESCE((price_action_detail->>'pa_always_in_strength')::float, 0) AS pa_always_in_strength,
                COALESCE((price_action_detail->>'pa_failure_risk')::float, 0) AS pa_failure_risk,
                COALESCE(price_action_detail->>'pa_trend_damage', '无') AS pa_trend_damage,
                COALESCE((price_action_detail->>'pa_structure_score')::float, price_action_score, 0) AS pa_structure_score,
                COALESCE((price_action_detail->>'pa_execution_score')::float, 0) AS pa_execution_score,
                COALESCE((price_action_detail->>'pa_risk_score')::float, 0) AS pa_risk_score,
                COALESCE(price_action_detail->>'price_action_version', 'legacy') AS price_action_version,
                COALESCE(price_action_detail->>'target_model_version', 'legacy') AS target_model_version,
                COALESCE(price_action_detail->>'score_model_version', 'legacy') AS score_model_version
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
                s.early_trade_candidate,
                s.early_trade_grade,
                s.final_trade_score,
                s.calibrated_score,
                s.research_eligible,
                s.trade_blockers,
                s.market_regime,
                s.market_sentiment_stage,
                s.sector_phase,
                s.sector_role,
                s.sector_mainline,
                s.trade_state,
                s.trade_opportunity_score,
                s.sector_strength_score,
                s.stock_sector_fit_score,
                s.sector_alignment_score,
                s.sector_relative_pct,
                s.pa_trap_risk_bucket,
                s.pa_trap_risk,
                s.pa_always_in_strength,
                s.pa_failure_risk,
                s.pa_trend_damage,
                s.pa_structure_score,
                s.pa_execution_score,
                s.pa_risk_score,
                s.price_action_version,
                s.target_model_version,
                s.score_model_version,
                s.signal_date,
                s.price,
                s.pa_entry_price,
                s.pa_stop_price,
                s.pa_target_price,
                s.pa_risk_reward,
                trigger_event.trigger_date,
                trigger_event.actual_entry_price,
                triggered_path.max_high_20d,
                triggered_path.min_low_20d,
                triggered_path.target_hit_date,
                triggered_path.stop_hit_date,
                signal_path.closes[1] AS close_1d,
                signal_path.opens[1] AS open_1d,
                signal_path.closes[3] AS close_3d,
                signal_path.closes[5] AS close_5d,
                signal_path.closes[10] AS close_10d,
                signal_path.closes[20] AS close_20d,
                triggered_path.closes[1] AS triggered_close_1d,
                triggered_path.closes[3] AS triggered_close_3d,
                triggered_path.closes[5] AS triggered_close_5d,
                triggered_path.closes[10] AS triggered_close_10d,
                triggered_path.closes[20] AS triggered_close_20d
            FROM signals s
            LEFT JOIN LATERAL (
                SELECT array_agg(p.open ORDER BY p.date) AS opens,
                       array_agg(p.close ORDER BY p.date) AS closes
                FROM (
                    SELECT date, open, close
                    FROM daily_k d
                    WHERE d.code = s.code AND d.date > s.signal_date
                    ORDER BY d.date
                    LIMIT 20
                ) p
            ) signal_path ON true
            LEFT JOIN LATERAL (
                SELECT p.date AS trigger_date, GREATEST(p.open, s.pa_entry_price) AS actual_entry_price
                FROM (
                    SELECT d.date, d.open, d.high
                    FROM daily_k d
                    WHERE d.code = s.code AND d.date > s.signal_date
                    ORDER BY d.date
                    LIMIT 20
                ) p
                WHERE s.pa_entry_price > 0 AND p.high >= s.pa_entry_price
                ORDER BY p.date
                LIMIT 1
            ) trigger_event ON true
            LEFT JOIN LATERAL (
                SELECT
                    MAX(p.high) AS max_high_20d,
                    MIN(p.low) AS min_low_20d,
                    MIN(p.date) FILTER (WHERE s.pa_target_price > 0 AND p.high >= s.pa_target_price) AS target_hit_date,
                    MIN(p.date) FILTER (WHERE s.pa_stop_price > 0 AND p.low <= s.pa_stop_price) AS stop_hit_date,
                    array_agg(p.close ORDER BY p.date) FILTER (WHERE p.date > trigger_event.trigger_date) AS closes
                FROM (
                    SELECT date, high, low, close
                    FROM daily_k d
                    WHERE d.code = s.code AND d.date >= trigger_event.trigger_date
                    ORDER BY d.date
                    LIMIT 21
                ) p
            ) triggered_path ON true
        )
        SELECT * FROM future
        ORDER BY signal_date DESC
    """)
    df = pd.read_sql(query, engine, params={"days": int(days)})
    if df.empty:
        return df
    for horizon in [1, 3, 5, 10, 20]:
        df[f"ret_{horizon}d"] = (df[f"close_{horizon}d"] - df["price"]) / df["price"] * 100
        df[f"triggered_ret_{horizon}d"] = (
            (df[f"triggered_close_{horizon}d"] - df["actual_entry_price"]) / df["actual_entry_price"] * 100
        )
    df["triggered"] = df["trigger_date"].notna()
    df["mfe_20d_pct"] = (df["max_high_20d"] - df["actual_entry_price"]) / df["actual_entry_price"] * 100
    df["mae_20d_pct"] = (df["min_low_20d"] - df["actual_entry_price"]) / df["actual_entry_price"] * 100
    df["target_hit_20d"] = df["target_hit_date"].notna()
    df["stop_hit_20d"] = df["stop_hit_date"].notna()
    df["target_before_stop"] = (
        df["target_hit_date"].notna()
        & (df["stop_hit_date"].isna() | (df["target_hit_date"] < df["stop_hit_date"]))
    )
    df["path_outcome"] = "OPEN"
    df.loc[df["target_hit_date"].notna() & df["stop_hit_date"].isna(), "path_outcome"] = "TARGET_FIRST"
    df.loc[df["stop_hit_date"].notna() & df["target_hit_date"].isna(), "path_outcome"] = "STOP_FIRST"
    df.loc[df["target_hit_date"] < df["stop_hit_date"], "path_outcome"] = "TARGET_FIRST"
    df.loc[df["stop_hit_date"] < df["target_hit_date"], "path_outcome"] = "STOP_FIRST"
    df.loc[df["target_hit_date"].eq(df["stop_hit_date"]) & df["target_hit_date"].notna(), "path_outcome"] = "AMBIGUOUS"
    df["next_open_gap_pct"] = (df["open_1d"] - df["price"]) / df["price"] * 100
    df["data_quality_excluded"] = (
        df["next_open_gap_pct"].abs().ge(14)
        & df["ret_1d"].abs().ge(20)
        & df["open_1d"].notna()
    )
    for horizon in [1, 3, 5, 10, 20]:
        df.loc[df["data_quality_excluded"], f"ret_{horizon}d"] = pd.NA
    df.loc[df["data_quality_excluded"], ["mfe_20d_pct", "mae_20d_pct"]] = pd.NA
    df["performance_eligible"] = (
        ~df["data_quality_excluded"].fillna(False)
        & df["pa_trade_action"].ne("UNKNOWN")
        & df["pa_trade_setup"].ne("未知")
    )
    df["sector_alignment_bucket"] = pd.cut(
        pd.to_numeric(df["sector_alignment_score"], errors="coerce"),
        bins=[-1, 50, 70, 100],
        labels=["弱联动<50", "中联动50~70", "强联动>70"],
    ).astype(str).replace("nan", "未知")
    df["sector_strength_bucket"] = pd.cut(
        pd.to_numeric(df["sector_strength_score"], errors="coerce"),
        bins=[-1, 50, 70, 100],
        labels=["弱板块<50", "中板块50~70", "强板块>70"],
    ).astype(str).replace("nan", "未知")
    df["stock_sector_fit_bucket"] = pd.cut(
        pd.to_numeric(df["stock_sector_fit_score"], errors="coerce"),
        bins=[-1, 45, 60, 100],
        labels=["弱适配<45", "中适配45~60", "强适配>60"],
    ).astype(str).replace("nan", "未知")
    df["next_open_gap_bucket"] = pd.cut(
        df["next_open_gap_pct"],
        bins=[-999, -2, 1, 3, 999],
        labels=["低开<-2%", "平开-2~1%", "高开1~3%", "高开>3%"],
    ).astype(str).replace("nan", "未知")
    return df


def _layer_verdict(metrics_5d: Dict[str, Any]) -> Dict[str, Any]:
    signals = int(metrics_5d.get("signals") or 0)
    expected = float(metrics_5d.get("expected_return") or 0)
    ci_low = float(metrics_5d.get("ci95_low") or 0)
    if signals < 10:
        return {"status": "INSUFFICIENT", "action": "样本不足，只观察，不作为调参依据"}
    if expected > 0 and ci_low >= 0:
        return {"status": "POSITIVE", "action": "正期望较明确，可作为优先交易层"}
    if expected > 0:
        return {"status": "WATCH", "action": "均值为正但置信度不足，适合小仓复核"}
    return {"status": "WEAK", "action": "期望为负，避免直接交易或继续降权"}


def _profitability_layer_row(
    key: str,
    label: str,
    df: pd.DataFrame,
    source: str = "scan_history",
) -> Dict[str, Any]:
    horizon_metrics = {}
    for horizon in [1, 3, 5, 10]:
        col = f"ret_{horizon}d"
        horizon_metrics[f"{horizon}d"] = return_metrics(df[col]) if col in df.columns else return_metrics(pd.Series(dtype=float))
    metrics_5d = horizon_metrics["5d"]
    return {
        "key": key,
        "label": label,
        "source": source,
        "rows": int(len(df)),
        "latest_date": str(df["signal_date"].max()) if "signal_date" in df.columns and not df.empty else None,
        "metrics": horizon_metrics,
        "verdict": _layer_verdict(metrics_5d),
    }


def _build_profitability_layers(scan_df: pd.DataFrame, event_df: pd.DataFrame | None = None) -> List[Dict[str, Any]]:
    if scan_df.empty:
        return []

    df = scan_df[scan_df["performance_eligible"].fillna(False)].copy()
    trade_eligible = df["trade_eligible"].astype(str).str.lower().eq("true")
    early_candidate = (
        df.get("early_trade_candidate", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        | df.get("early_trade_grade", pd.Series("", index=df.index)).astype(str).eq("A-")
    )
    strong_sector = pd.to_numeric(df["sector_alignment_score"], errors="coerce").fillna(0).ge(70)

    layers = [
        _profitability_layer_row("scan_all", "全量有效扫描", df),
        _profitability_layer_row("trade_a", "A/正式买点", df[df["trade_bucket"].eq("TRADE") | trade_eligible]),
        _profitability_layer_row("early_a_minus", "A-提前复核", df[early_candidate]),
        _profitability_layer_row("observe", "观察池", df[df["trade_bucket"].eq("OBSERVE")]),
        _profitability_layer_row("block", "禁止/过滤", df[df["trade_bucket"].eq("BLOCK")]),
        _profitability_layer_row("tv_dual_strict", "严格双策略", df[df["strategy_type"].eq("tv_dual_strict")]),
        _profitability_layer_row("strong_sector", "强板块联动>70", df[strong_sector]),
    ]

    if event_df is not None and not event_df.empty:
        bark_df = event_df[event_df["source"].astype(str).str.startswith("bark")].copy()
        if "event_date" in bark_df.columns:
            bark_df["signal_date"] = bark_df["event_date"]
        layers.append(_profitability_layer_row("bark", "Bark推送", bark_df, source="recommendation_events"))

    return layers


def _feature_success_rows(df: pd.DataFrame, feature: str, label: str) -> List[Dict[str, Any]]:
    if df.empty or feature not in df.columns:
        return []
    rows: List[Dict[str, Any]] = []
    clean = df[[feature, "ret_5d"]].copy()
    clean[feature] = clean[feature].fillna("未知").astype(str)
    clean["ret_5d"] = pd.to_numeric(clean["ret_5d"], errors="coerce")
    clean = clean.dropna(subset=["ret_5d"])
    baseline = float((clean["ret_5d"] > 0).mean() * 100) if not clean.empty else 0
    for value, group in clean.groupby(feature):
        if not value or value == "nan":
            continue
        signals = int(len(group))
        if signals < 2:
            continue
        win_rate = float((group["ret_5d"] > 0).mean() * 100)
        avg_return = float(group["ret_5d"].mean())
        rows.append({
            "feature": feature,
            "label": label,
            "value": value,
            "signals": signals,
            "win_rate_5d": round(win_rate, 1),
            "avg_return_5d": round(avg_return, 2),
            "lift_vs_bark_win_rate": round(win_rate - baseline, 1),
        })
    rows.sort(key=lambda row: (row["avg_return_5d"], row["win_rate_5d"], row["signals"]), reverse=True)
    return rows[:5]


def _build_bark_success_profile(event_df: pd.DataFrame) -> Dict[str, Any]:
    if event_df.empty:
        return {"sample": 0, "features": [], "notes": ["暂无Bark样本"]}
    bark_df = event_df[event_df["source"].astype(str).str.startswith("bark")].copy()
    bark_df["ret_5d"] = pd.to_numeric(bark_df.get("ret_5d"), errors="coerce")
    bark_df = bark_df.dropna(subset=["ret_5d"])
    if bark_df.empty:
        return {"sample": 0, "features": [], "notes": ["Bark样本暂无5日收益数据"]}
    if "sector_strength_score" in bark_df.columns:
        bark_df["sector_strength_bucket"] = pd.cut(
            pd.to_numeric(bark_df["sector_strength_score"], errors="coerce"),
            bins=[-1, 50, 70, 100],
            labels=["弱板块<50", "中板块50~70", "强板块>70"],
        ).astype(str).replace("nan", "未知")
    if "stock_sector_fit_score" in bark_df.columns:
        bark_df["stock_sector_fit_bucket"] = pd.cut(
            pd.to_numeric(bark_df["stock_sector_fit_score"], errors="coerce"),
            bins=[-1, 45, 60, 100],
            labels=["弱适配<45", "中适配45~60", "强适配>60"],
        ).astype(str).replace("nan", "未知")
    features: List[Dict[str, Any]] = []
    for col, label in [
        ("strategy_type", "策略"),
        ("trade_bucket", "交易桶"),
        ("pa_trade_setup", "价格行为"),
        ("sector_phase", "板块阶段"),
        ("sector_strength_bucket", "板块强度"),
        ("stock_sector_fit_bucket", "个股适配"),
        ("market_regime", "大盘环境"),
    ]:
        features.extend(_feature_success_rows(bark_df, col, label))
    features.sort(key=lambda row: (row["avg_return_5d"], row["lift_vs_bark_win_rate"], row["signals"]), reverse=True)
    return {
        "sample": int(len(bark_df)),
        "win_rate_5d": round(float((bark_df["ret_5d"] > 0).mean() * 100), 1),
        "avg_return_5d": round(float(bark_df["ret_5d"].mean()), 2),
        "features": features[:12],
        "scoring_hint": [
            "强联动、严格双策略、H2/缩量反包若持续正期望，保留加权。",
            "弱联动、周线中性/交易区间、震荡观察若持续低胜率，继续降权。",
        ],
    }


def _load_recommendation_event_performance_df(days: int) -> pd.DataFrame:
    engine = get_db_engine()
    if not engine:
        return pd.DataFrame()
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(recommendation_events)")).fetchall()
            columns = {row[1] for row in rows}
        else:
            rows = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'recommendation_events'
            """)).fetchall()
            columns = {row[0] for row in rows}

    def event_col(column: str) -> str:
        return f"e.{column}" if column in columns else f"NULL AS {column}"

    df = pd.read_sql(text(f"""
        SELECT
            e.source,
            e.code,
            e.name,
            e.event_date AS signal_date,
            e.recommendation_price AS price,
            e.strategy_type,
            e.trade_bucket,
            e.trade_eligible,
            e.pa_trade_action,
            e.pa_trade_setup,
            {event_col("sector_strength_score")},
            {event_col("stock_sector_fit_score")},
            {event_col("sector_alignment_score")},
            {event_col("sector_phase")},
            {event_col("market_regime")},
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
          AND e.recommendation_price IS NOT NULL
          AND e.recommendation_price > 0
    """), engine, params={"days": int(days)})
    if df.empty:
        return df
    for horizon in [1, 3, 5, 10]:
        df[f"ret_{horizon}d"] = (
            pd.to_numeric(df[f"close_{horizon}d"], errors="coerce") - pd.to_numeric(df["price"], errors="coerce")
        ) / pd.to_numeric(df["price"], errors="coerce") * 100
    return df


def _load_real_trade_performance_df(days: int) -> pd.DataFrame:
    engine = get_db_engine()
    if not engine:
        return pd.DataFrame()
    df = pd.read_sql(text("""
        SELECT
            'real_trade' AS source,
            p.code,
            p.name,
            p.entry_date AS signal_date,
            COALESCE(p.actual_entry_price, p.entry_price) AS price,
            p.planned_entry_price,
            p.actual_entry_price,
            p.entry_slippage_pct,
            p.plan_adherence,
            p.entry_source,
            p.entry_signal_date,
            p.strategy_type,
            p.status,
            h1.close AS close_1d,
            h3.close AS close_3d,
            h5.close AS close_5d,
            h10.close AS close_10d
        FROM paper_trading p
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d WHERE d.code = p.code AND d.date > p.entry_date ORDER BY d.date ASC OFFSET 0 LIMIT 1
        ) h1 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d WHERE d.code = p.code AND d.date > p.entry_date ORDER BY d.date ASC OFFSET 2 LIMIT 1
        ) h3 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d WHERE d.code = p.code AND d.date > p.entry_date ORDER BY d.date ASC OFFSET 4 LIMIT 1
        ) h5 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d WHERE d.code = p.code AND d.date > p.entry_date ORDER BY d.date ASC OFFSET 9 LIMIT 1
        ) h10 ON true
        WHERE p.trade_mode = 'REAL'
          AND p.entry_date >= CURRENT_DATE - (:days || ' days')::interval
          AND COALESCE(p.actual_entry_price, p.entry_price) IS NOT NULL
          AND COALESCE(p.actual_entry_price, p.entry_price) > 0
    """), engine, params={"days": int(days)})
    if df.empty:
        return df
    for horizon in [1, 3, 5, 10]:
        df[f"ret_{horizon}d"] = (
            pd.to_numeric(df[f"close_{horizon}d"], errors="coerce") - pd.to_numeric(df["price"], errors="coerce")
        ) / pd.to_numeric(df["price"], errors="coerce") * 100
    return df


def _metric_summary(returns: pd.Series) -> Dict[str, Any]:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return {"signals": 0, "win_rate": 0, "avg_return": None, "best_return": None, "worst_return": None}
    return {
        "signals": int(len(clean)),
        "win_rate": round(float((clean > 0).mean() * 100), 1),
        "avg_return": round(float(clean.mean()), 2),
        "best_return": round(float(clean.max()), 2),
        "worst_return": round(float(clean.min()), 2),
    }


def _outcome_adjustment_rows(
    rows: List[Dict[str, Any]],
    value_key: str,
    dimension: str,
    min_samples: int = 10,
) -> List[Dict[str, Any]]:
    adjustments: List[Dict[str, Any]] = []
    for row in rows:
        metric = (row.get("metrics") or {}).get("5d") or {}
        signals = int(metric.get("signals") or 0)
        win_rate = float(metric.get("win_rate") or 0)
        avg_return = metric.get("avg_return")
        value = str(row.get(value_key) or "UNKNOWN")
        if signals < min_samples:
            action = "OBSERVE"
            score_delta = 0
            reason = f"5日成熟样本 {signals} < {min_samples}，只观察不调权"
        elif avg_return is not None and avg_return <= -0.3:
            action = "DOWNWEIGHT"
            score_delta = -5
            reason = f"5日均收 {avg_return}% 为负，建议降权"
        elif win_rate < 45:
            action = "DOWNWEIGHT"
            score_delta = -3
            reason = f"5日胜率 {win_rate}% 偏低，建议降低优先级"
        elif avg_return is not None and avg_return >= 0.8 and win_rate >= 52:
            action = "BOOST"
            score_delta = 4
            reason = f"5日均收 {avg_return}% 且胜率 {win_rate}%，可小幅加权"
        else:
            action = "KEEP"
            score_delta = 0
            reason = "表现接近中性，暂不调权"
        adjustments.append({
            "dimension": dimension,
            "value": value,
            "action": action,
            "score_delta": score_delta,
            "mature_5d": signals,
            "win_rate_5d": win_rate,
            "avg_return_5d": avg_return,
            "reason": reason,
        })
    priority = {"DOWNWEIGHT": 0, "BOOST": 1, "OBSERVE": 2, "KEEP": 3}
    adjustments.sort(key=lambda item: (priority.get(item["action"], 9), -abs(int(item["score_delta"])), -int(item["mature_5d"])))
    return adjustments


def _build_recommendation_outcome_loop(event_df: pd.DataFrame) -> Dict[str, Any]:
    """Summarize post-recommendation returns so Bark/scan pushes form a feedback loop."""
    if event_df.empty:
        return {
            "summary": {"events": 0, "mature_5d": 0, "best_source": "暂无", "worst_source": "暂无"},
            "horizons": {},
            "by_source": [],
            "by_strategy": [],
            "adjustments": {"by_source": [], "by_strategy": [], "rules": []},
            "recent_events": [],
            "suggestions": ["暂无推荐事件样本，先积累 Bark/扫描推荐记录"],
        }

    df = event_df.copy()
    df["source"] = df.get("source", pd.Series(dtype=str)).fillna("UNKNOWN").astype(str)
    df["strategy_type"] = df.get("strategy_type", pd.Series(dtype=str)).fillna("UNKNOWN").astype(str)
    df["signal_date"] = df.get("signal_date", df.get("event_date", pd.Series(dtype=object)))
    for horizon in [1, 3, 5, 10]:
        df[f"ret_{horizon}d"] = pd.to_numeric(df.get(f"ret_{horizon}d"), errors="coerce")

    horizons = {f"{horizon}d": _metric_summary(df[f"ret_{horizon}d"]) for horizon in [1, 3, 5, 10]}

    def grouped_rows(group_col: str, label_col: str) -> List[Dict[str, Any]]:
        if group_col not in df.columns:
            return []
        rows: List[Dict[str, Any]] = []
        for value, group in df.groupby(df[group_col].fillna("UNKNOWN").astype(str), dropna=False):
            row = {
                label_col: value,
                "events": int(len(group)),
                "mature_5d": int(group["ret_5d"].notna().sum()),
                "metrics": {f"{horizon}d": _metric_summary(group[f"ret_{horizon}d"]) for horizon in [1, 3, 5, 10]},
            }
            rows.append(row)
        rows.sort(
            key=lambda row: (
                row["metrics"]["5d"]["avg_return"] if row["metrics"]["5d"]["avg_return"] is not None else -999,
                row["metrics"]["5d"]["win_rate"],
                row["mature_5d"],
            ),
            reverse=True,
        )
        return rows

    by_source = grouped_rows("source", "source")
    by_strategy = grouped_rows("strategy_type", "strategy_type")
    source_adjustments = _outcome_adjustment_rows(by_source, "source", "推荐来源")
    strategy_adjustments = _outcome_adjustment_rows(by_strategy, "strategy_type", "策略")
    mature_sources = [row for row in by_source if row["metrics"]["5d"]["signals"] > 0]
    best_source = mature_sources[0]["source"] if mature_sources else "样本不足"
    worst_source = mature_sources[-1]["source"] if mature_sources else "样本不足"

    suggestions = []
    five_day = horizons["5d"]
    if five_day["signals"] < 10:
        suggestions.append("5日成熟样本不足10个，暂时只做观察，不建议大幅调参")
    elif (five_day["avg_return"] or 0) > 0 and five_day["win_rate"] >= 55:
        suggestions.append("推荐事件5日表现为正，可继续强化当前高胜率画像")
    else:
        suggestions.append("推荐事件5日表现偏弱，应检查推送是否过晚、是否追高或板块联动不足")
    for row in by_source:
        metric = row["metrics"]["5d"]
        if metric["signals"] >= 3 and metric["avg_return"] is not None and metric["avg_return"] < 0:
            suggestions.append(f"{row['source']} 5日均收为负，建议降低展示优先级或延后确认")
    for item in source_adjustments + strategy_adjustments:
        if item["action"] in {"DOWNWEIGHT", "BOOST"}:
            suggestions.append(f"{item['dimension']} {item['value']}：{item['reason']}")

    recent_cols = [
        "signal_date", "source", "code", "name", "strategy_type", "price",
        "ret_1d", "ret_3d", "ret_5d", "ret_10d", "trade_bucket", "pa_trade_setup",
    ]
    recent_df = df[[c for c in recent_cols if c in df.columns]].copy()
    if "signal_date" in recent_df.columns:
        recent_df = recent_df.sort_values("signal_date", ascending=False)
    recent_events = recent_df.head(12).where(pd.notna(recent_df), None).to_dict(orient="records")
    for row in recent_events:
        if row.get("signal_date") is not None:
            row["signal_date"] = str(row["signal_date"])[:10]

    return {
        "summary": {
            "events": int(len(df)),
            "mature_5d": int(df["ret_5d"].notna().sum()),
            "best_source": best_source,
            "worst_source": worst_source,
            "downweight_count": sum(1 for item in source_adjustments + strategy_adjustments if item["action"] == "DOWNWEIGHT"),
            "boost_count": sum(1 for item in source_adjustments + strategy_adjustments if item["action"] == "BOOST"),
        },
        "horizons": horizons,
        "by_source": by_source,
        "by_strategy": by_strategy,
        "adjustments": {
            "by_source": source_adjustments,
            "by_strategy": strategy_adjustments,
            "rules": [
                "5日成熟样本少于10个：只观察不调权",
                "5日均收<=-0.3%或胜率<45%：建议降权",
                "5日均收>=0.8%且胜率>=52%：建议小幅加权",
            ],
        },
        "recent_events": recent_events,
        "suggestions": suggestions[:5],
    }


def _safe_date(value: Any):
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _payload_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _build_sector_watch_event_performance(event_df: pd.DataFrame, daily_df: pd.DataFrame) -> Dict[str, Any]:
    if event_df.empty:
        return _build_sector_watch_performance(pd.DataFrame(), daily_df)

    daily = daily_df.copy()
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["date"]).dt.date
        daily = daily.sort_values(["code", "date"])

    items: List[Dict[str, Any]] = []
    for _, row in event_df.iterrows():
        payload = _payload_dict(row.get("payload"))
        code = str(row.get("code") or "").zfill(6)
        signal_date = _safe_date(row.get("event_time"))
        event_price = float(payload.get("current_price") or payload.get("watch_price") or 0)
        if not code or not signal_date or event_price <= 0:
            continue
        path = daily[(daily["code"].astype(str).str.zfill(6) == code) & (daily["date"] > signal_date)] if not daily.empty else pd.DataFrame()
        latest = path.iloc[-1].to_dict() if not path.empty else {}
        item = {
            "code": code,
            "name": row.get("name") or "",
            "theme": row.get("theme") or "",
            "signal_date": str(signal_date),
            "watch_price": round(event_price, 2),
            "latest_close": round(float(latest.get("close") or event_price), 2),
            "latest_date": str(latest.get("date") or "") if latest else "",
            "state": payload.get("state") or "UNKNOWN",
            "label": payload.get("label") or payload.get("state") or "未知",
            "action": payload.get("action") or "",
            "days_tracked": int(len(path)),
        }
        for horizon in [1, 3, 5, 10]:
            if len(path) >= horizon:
                close = float(path.iloc[horizon - 1]["close"])
                item[f"ret_{horizon}d"] = round((close - event_price) / event_price * 100, 2)
            else:
                item[f"ret_{horizon}d"] = None
        items.append(item)

    item_df = pd.DataFrame(items)
    if item_df.empty:
        return {
            "summary": {"items": 0, "mature_5d": 0, "best_state": "暂无"},
            "by_state": [],
            "items": [],
            "notes": ["题材状态事件缺少有效价格或后续日线"],
            "suggestions": [],
            "source": "state_events",
        }

    by_state: List[Dict[str, Any]] = []
    for state, group in item_df.groupby("state", dropna=False):
        row = {
            "state": state,
            "label": str(group["label"].iloc[0] or state),
            "items": int(len(group)),
            "mature_5d": int(pd.to_numeric(group["ret_5d"], errors="coerce").notna().sum()),
            "metrics": {
                "1d": _metric_summary(group["ret_1d"]),
                "3d": _metric_summary(group["ret_3d"]),
                "5d": _metric_summary(group["ret_5d"]),
                "10d": _metric_summary(group["ret_10d"]),
            },
        }
        by_state.append(row)
    by_state.sort(
        key=lambda r: (
            r["metrics"]["5d"]["avg_return"] if r["metrics"]["5d"]["avg_return"] is not None else -999,
            r["metrics"]["5d"]["win_rate"],
            r["mature_5d"],
        ),
        reverse=True,
    )
    best_state = by_state[0]["label"] if by_state and by_state[0]["mature_5d"] > 0 else "样本不足"
    suggestions = []
    for row in by_state:
        metric = row["metrics"]["5d"]
        if metric["signals"] >= 3 and metric["avg_return"] is not None and metric["avg_return"] > 1 and metric["win_rate"] >= 55:
            suggestions.append(f"{row['label']} 5日表现较好，可提高Bark展示优先级")
        if metric["signals"] >= 3 and metric["avg_return"] is not None and metric["avg_return"] < 0:
            suggestions.append(f"{row['label']} 5日均值为负，应继续降权或延后确认")

    return {
        "summary": {
            "items": int(len(item_df)),
            "mature_5d": int(pd.to_numeric(item_df["ret_5d"], errors="coerce").notna().sum()),
            "best_state": best_state,
        },
        "by_state": by_state,
        "items": sorted(items, key=lambda x: (x.get("ret_5d") is not None, x.get("ret_5d") or -999), reverse=True)[:30],
        "notes": ["按题材状态事件出现日和当时价格计算后续表现。"],
        "suggestions": suggestions,
        "source": "state_events",
    }


def _build_sector_watch_performance(watch_df: pd.DataFrame, daily_df: pd.DataFrame) -> Dict[str, Any]:
    if watch_df.empty:
        return {
            "summary": {"items": 0, "mature_5d": 0, "best_state": "暂无"},
            "by_state": [],
            "items": [],
            "notes": ["暂无题材观察池样本"],
        }

    from routers.watchlist import _sector_watch_state

    daily = daily_df.copy()
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["date"]).dt.date
        daily = daily.sort_values(["code", "date"])

    items: List[Dict[str, Any]] = []
    for _, row in watch_df.iterrows():
        code = str(row.get("code") or "").zfill(6)
        watch_price = float(row.get("watch_price") or 0)
        signal_date = _safe_date(row.get("created_at") or row.get("updated_at"))
        if not code or watch_price <= 0 or not signal_date:
            continue
        path = daily[(daily["code"].astype(str).str.zfill(6) == code) & (daily["date"] > signal_date)] if not daily.empty else pd.DataFrame()
        latest = path.iloc[-1].to_dict() if not path.empty else {}
        current_price = float(latest.get("close") or watch_price)
        target_price = row.get("target_price")
        stop_price = row.get("stop_price")
        state_input = {
            "strategy_type": row.get("strategy_type") or "",
            "source": row.get("source") or "",
            "current_price": current_price,
            "watch_price": watch_price,
            "target_price": target_price if pd.notna(target_price) else None,
            "stop_price": stop_price if pd.notna(stop_price) else None,
            "target_hit": pd.notna(target_price) and current_price >= float(target_price),
            "stop_hit": pd.notna(stop_price) and current_price <= float(stop_price),
            "intraday_high": float(latest.get("high") or 0),
            "pa_trade_action": row.get("pa_trade_action") or "",
        }
        state = _sector_watch_state(state_input)
        item = {
            "code": code,
            "name": row.get("name") or "",
            "theme": row.get("theme") or row.get("industry") or "",
            "signal_date": str(signal_date),
            "watch_price": round(watch_price, 2),
            "latest_close": round(current_price, 2),
            "latest_date": str(latest.get("date") or "") if latest else "",
            "state": state.get("state") or "UNKNOWN",
            "label": state.get("label") or "未知",
            "action": state.get("action") or "",
            "days_tracked": int(len(path)),
        }
        for horizon in [1, 3, 5, 10]:
            if len(path) >= horizon:
                close = float(path.iloc[horizon - 1]["close"])
                item[f"ret_{horizon}d"] = round((close - watch_price) / watch_price * 100, 2)
            else:
                item[f"ret_{horizon}d"] = None
        items.append(item)

    item_df = pd.DataFrame(items)
    if item_df.empty:
        return {
            "summary": {"items": 0, "mature_5d": 0, "best_state": "暂无"},
            "by_state": [],
            "items": [],
            "notes": ["题材观察池样本缺少有效观察价或后续日线"],
        }

    by_state: List[Dict[str, Any]] = []
    for state, group in item_df.groupby("state", dropna=False):
        row = {
            "state": state,
            "label": str(group["label"].iloc[0] or state),
            "items": int(len(group)),
            "mature_5d": int(pd.to_numeric(group["ret_5d"], errors="coerce").notna().sum()),
            "metrics": {
                "1d": _metric_summary(group["ret_1d"]),
                "3d": _metric_summary(group["ret_3d"]),
                "5d": _metric_summary(group["ret_5d"]),
                "10d": _metric_summary(group["ret_10d"]),
            },
        }
        by_state.append(row)
    by_state.sort(
        key=lambda r: (
            r["metrics"]["5d"]["avg_return"] if r["metrics"]["5d"]["avg_return"] is not None else -999,
            r["metrics"]["5d"]["win_rate"],
            r["mature_5d"],
        ),
        reverse=True,
    )
    best_state = by_state[0]["label"] if by_state and by_state[0]["mature_5d"] > 0 else "样本不足"
    suggestions = []
    for row in by_state:
        metric = row["metrics"]["5d"]
        if metric["signals"] >= 3 and metric["avg_return"] is not None and metric["avg_return"] > 1 and metric["win_rate"] >= 55:
            suggestions.append(f"{row['label']} 5日表现较好，可提高Bark展示优先级")
        if metric["signals"] >= 3 and metric["avg_return"] is not None and metric["avg_return"] < 0:
            suggestions.append(f"{row['label']} 5日均值为负，应继续降权或延后确认")

    return {
        "summary": {
            "items": int(len(item_df)),
            "mature_5d": int(pd.to_numeric(item_df["ret_5d"], errors="coerce").notna().sum()),
            "best_state": best_state,
        },
        "by_state": by_state,
        "items": sorted(items, key=lambda x: (x.get("ret_5d") is not None, x.get("ret_5d") or -999), reverse=True)[:30],
        "notes": [
            "按当前可计算题材状态分组，收益从加入观察池的观察价开始计算。",
            "历史未记录状态切换事件，因此这是当前状态画像，不是逐日状态事件回放。",
        ],
        "suggestions": suggestions,
    }


def _build_real_trade_execution_review(real_trade_df: pd.DataFrame, event_df: pd.DataFrame) -> Dict[str, Any]:
    if real_trade_df.empty:
        return {"sample": 0, "notes": ["暂无实盘买入样本"]}
    real = real_trade_df.copy()
    real["ret_5d"] = pd.to_numeric(real.get("ret_5d"), errors="coerce")
    real["entry_slippage_pct"] = pd.to_numeric(real.get("entry_slippage_pct"), errors="coerce")
    planned = pd.to_numeric(real.get("planned_entry_price"), errors="coerce")
    actual = pd.to_numeric(real.get("actual_entry_price"), errors="coerce")
    missing_slip = real["entry_slippage_pct"].isna() & planned.gt(0) & actual.gt(0)
    real.loc[missing_slip, "entry_slippage_pct"] = (actual[missing_slip] - planned[missing_slip]) / planned[missing_slip] * 100
    signal_dates = pd.to_datetime(
        real.get("entry_signal_date", pd.Series(index=real.index, dtype=object)), errors="coerce"
    )
    entry_dates = pd.to_datetime(
        real.get("entry_date", pd.Series(index=real.index, dtype=object)), errors="coerce"
    )
    real["entry_delay_calendar_days"] = (entry_dates - signal_dates).dt.days
    real.loc[real["entry_delay_calendar_days"] < 0, "entry_delay_calendar_days"] = pd.NA
    real["entry_delay_bucket"] = pd.cut(
        real["entry_delay_calendar_days"],
        bins=[-1, 0, 1, 99999],
        labels=["信号当日", "延迟1日", "延迟2日以上"],
    ).astype(str).replace("nan", "未知")

    bark = pd.DataFrame()
    if not event_df.empty:
        bark = event_df[event_df["source"].astype(str).str.startswith("bark")].copy()
        bark["ret_5d"] = pd.to_numeric(bark.get("ret_5d"), errors="coerce")

    real_ret = real["ret_5d"].dropna()
    bark_ret = bark["ret_5d"].dropna() if not bark.empty else pd.Series(dtype=float)
    by_adherence: List[Dict[str, Any]] = []
    if "plan_adherence" in real.columns:
        for value, group in real.groupby(real["plan_adherence"].fillna("UNKNOWN").astype(str)):
            returns = pd.to_numeric(group.get("ret_5d"), errors="coerce").dropna()
            by_adherence.append({
                "plan_adherence": value,
                "trades": int(len(group)),
                "avg_return_5d": round(float(returns.mean()), 2) if not returns.empty else None,
                "win_rate_5d": round(float((returns > 0).mean() * 100), 1) if not returns.empty else None,
            })

    by_entry_source: List[Dict[str, Any]] = []
    if "entry_source" in real.columns:
        for value, group in real.groupby(real["entry_source"].fillna("UNKNOWN").astype(str)):
            returns = pd.to_numeric(group.get("ret_5d"), errors="coerce").dropna()
            by_entry_source.append({
                "entry_source": value,
                "trades": int(len(group)),
                "avg_return_5d": round(float(returns.mean()), 2) if not returns.empty else None,
                "win_rate_5d": round(float((returns > 0).mean() * 100), 1) if not returns.empty else None,
            })

    slip_series = pd.to_numeric(real["entry_slippage_pct"], errors="coerce")
    real["slippage_bucket"] = pd.cut(
        slip_series,
        bins=[-999, -0.01, 0.5, 2.0, 999],
        labels=["优于/低于计划价", "轻微滑点0~0.5%", "高滑点0.5~2%", "严重滑点>2%"],
    ).astype(str).replace("nan", "未知")
    by_slippage_bucket: List[Dict[str, Any]] = []
    for value, group in real.groupby("slippage_bucket", dropna=False):
        returns = pd.to_numeric(group.get("ret_5d"), errors="coerce").dropna()
        by_slippage_bucket.append({
            "slippage_bucket": value,
            "trades": int(len(group)),
            "avg_return_5d": round(float(returns.mean()), 2) if not returns.empty else None,
            "win_rate_5d": round(float((returns > 0).mean() * 100), 1) if not returns.empty else None,
        })

    by_entry_delay_bucket: List[Dict[str, Any]] = []
    for value, group in real.groupby("entry_delay_bucket", dropna=False):
        returns = pd.to_numeric(group.get("ret_5d"), errors="coerce").dropna()
        by_entry_delay_bucket.append({
            "entry_delay_bucket": value,
            "trades": int(len(group)),
            "avg_return_5d": round(float(returns.mean()), 2) if not returns.empty else None,
            "win_rate_5d": round(float((returns > 0).mean() * 100), 1) if not returns.empty else None,
        })

    real_avg = float(real_ret.mean()) if not real_ret.empty else None
    bark_avg = float(bark_ret.mean()) if not bark_ret.empty else None
    avg_slip = float(real["entry_slippage_pct"].dropna().mean()) if not real["entry_slippage_pct"].dropna().empty else None
    unknown_adherence = int(real.get("plan_adherence", pd.Series(dtype=str)).fillna("UNKNOWN").astype(str).eq("UNKNOWN").sum())
    missing_planned_entry = int(planned.isna().sum() + planned.eq(0).sum())
    missing_signal_date = int(real.get("entry_signal_date", pd.Series(dtype=object)).isna().sum()) if "entry_signal_date" in real.columns else int(len(real))
    high_slippage_trades = int(slip_series.gt(0.5).sum())
    delayed_trades = int(real["entry_delay_calendar_days"].ge(2).sum())
    execution_quality_score = 100
    execution_quality_score -= min(40, unknown_adherence * 10)
    execution_quality_score -= min(25, high_slippage_trades * 8)
    execution_quality_score -= min(20, missing_planned_entry * 8)
    execution_quality_score -= min(15, missing_signal_date * 5)
    execution_quality_score = max(0, execution_quality_score)

    diagnostics: List[str] = []
    if real_avg is not None and bark_avg is not None and real_avg < bark_avg:
        diagnostics.append("实盘执行收益跑输系统Bark样本，优先复盘买入时点和卖出纪律")
    if avg_slip is not None and avg_slip > 0.5:
        diagnostics.append("平均买入滑点偏高，避免高开追价或盘中急单")
    if unknown_adherence:
        diagnostics.append(f"{unknown_adherence}笔实盘缺少计划遵守标记，影响执行胜率归因")
    if missing_planned_entry:
        diagnostics.append(f"{missing_planned_entry}笔实盘缺少计划价，无法判断是否按确认价执行")
    if missing_signal_date:
        diagnostics.append(f"{missing_signal_date}笔实盘缺少信号日期，无法精确匹配系统推荐")
    if delayed_trades:
        diagnostics.append(f"{delayed_trades}笔实盘在信号两日后才入场，需结合收益分桶复盘等待成本")
    return {
        "sample": int(len(real)),
        "system_bark_sample": int(len(bark_ret)),
        "real_trade_avg_return_5d": round(real_avg, 2) if real_avg is not None else None,
        "system_bark_avg_return_5d": round(bark_avg, 2) if bark_avg is not None else None,
        "execution_gap_5d": round(real_avg - bark_avg, 2) if real_avg is not None and bark_avg is not None else None,
        "avg_entry_slippage_pct": round(avg_slip, 2) if avg_slip is not None else None,
        "unknown_plan_adherence": unknown_adherence,
        "missing_planned_entry": missing_planned_entry,
        "missing_signal_date": missing_signal_date,
        "high_slippage_trades": high_slippage_trades,
        "delayed_trades_2d_plus": delayed_trades,
        "execution_quality_score": execution_quality_score,
        "by_plan_adherence": by_adherence,
        "by_entry_source": by_entry_source,
        "by_slippage_bucket": by_slippage_bucket,
        "by_entry_delay_bucket": by_entry_delay_bucket,
        "diagnostics": diagnostics,
        "notes": [
            "execution_gap_5d = 实盘买入5日均值 - Bark系统样本5日均值，用于区分系统胜率和执行胜率",
            "entry_delay_bucket 按信号日至实际买入日的自然日差分组，节假日不计为交易日。",
        ],
    }


def _high_open_buyability(
    first_open_gap: Optional[float],
    entry_line: float,
    stop_line: float,
    base_price: float,
    min_low: float,
    latest_close: float,
    latest_gain: float,
) -> Optional[Dict[str, Any]]:
    if first_open_gap is None or first_open_gap <= 3:
        return None
    if first_open_gap <= 5:
        gap_bucket = "高开3~5%"
    elif first_open_gap < 9.5:
        gap_bucket = "高开5~9.5%"
    else:
        gap_bucket = "涨停/一字高开"
    if first_open_gap >= 9.5:
        return {
            "state": "LIMIT_OPEN",
            "gap_bucket": gap_bucket,
            "buyable": False,
            "action": "不追：涨停/一字高开",
            "required_confirmation": "隔日回踩不破支撑后再观察",
            "risk": "极易追在情绪高点",
        }
    confirm_line = entry_line if entry_line > 0 else base_price
    support_line = stop_line if stop_line > 0 else base_price * 0.97
    pulled_back = min_low <= confirm_line * 1.01
    held_support = min_low >= support_line * 0.995
    stood_back = latest_close >= confirm_line and latest_gain >= 0
    if not held_support:
        return {
            "state": "FAILED",
            "gap_bucket": gap_bucket,
            "buyable": False,
            "action": "高开回落跌破支撑：取消",
            "required_confirmation": "重新站回确认价且修复结构前不买",
            "risk": "高开低走破坏结构",
        }
    if pulled_back and stood_back:
        return {
            "state": "CONFIRMED",
            "gap_bucket": gap_bucket,
            "buyable": True,
            "action": "高开回踩后站稳：尾盘小仓确认",
            "required_confirmation": "贴近确认价小仓，跌破支撑取消",
            "risk": "仍需控制仓位，避免二次冲高回落",
        }
    if pulled_back:
        return {
            "state": "PENDING_CONFIRM",
            "gap_bucket": gap_bucket,
            "buyable": False,
            "action": "高开已回踩：等重新站回确认价",
            "required_confirmation": "放量站回确认价后再复核",
            "risk": "回踩尚未重新转强",
        }
    return {
        "state": "WAIT_PULLBACK",
        "gap_bucket": gap_bucket,
        "buyable": False,
        "action": "高开不追：等回踩确认或尾盘站稳",
        "required_confirmation": "回踩接近确认价/支撑位且尾盘站稳",
        "risk": "未回踩时追价风险高",
    }


@router.get("/daily-strategy-report")
def get_daily_strategy_report(date: str = "", limit: int = 8) -> Dict[str, Any]:
    """After-close strategy report: scan buckets, Bark events, hot-sector misses, and next actions."""
    try:
        dates = get_scan_dates()
        scan_date = date or (dates[0] if dates else "")
        scan_results = get_scan_history_by_date(scan_date) if scan_date else []
        try:
            from routers.market import get_sector_push_gaps

            sector_gap_payload = get_sector_push_gaps(limit=limit, date=scan_date)
            sector_gaps = sector_gap_payload.get("items", [])
        except Exception as exc:
            logger.warning(f"Daily strategy sector gap unavailable: {exc}")
            sector_gaps = []

        report = build_daily_strategy_report(
            scan_results,
            scan_date=scan_date,
            sector_gap_analysis=sector_gaps,
            bark_push_count=_count_recommendation_events_for_date(scan_date),
        )
        return {
            **report,
            "body": build_daily_strategy_report_body(report),
        }
    except Exception as exc:
        logger.error(f"Daily strategy report error: {exc}")
        return {
            "scan_date": date,
            "summary": {"scan_count": 0, "stance": "日报生成失败"},
            "body": "",
            "error": str(exc),
        }


@router.get("/profitability-dashboard")
def get_profitability_dashboard(days: int = 120) -> Dict[str, Any]:
    """Layered profitability view for scan, Bark, and real-trade outcomes."""
    engine = get_db_engine()
    if not engine:
        return {"summary": {"days": int(days), "layers": 0}, "layers": [], "notes": ["数据库未连接"]}
    try:
        scan_df = _load_scan_performance_df(days)
        event_df = _load_recommendation_event_performance_df(days)
        real_trade_df = _load_real_trade_performance_df(days)
        layers = _build_profitability_layers(scan_df, event_df)
        if not real_trade_df.empty:
            layers.append(_profitability_layer_row("real_trade", "实盘买入", real_trade_df, source="paper_trading"))
        bark_success_profile = _build_bark_success_profile(event_df)
        real_trade_execution = _build_real_trade_execution_review(real_trade_df, event_df)

        ranked = [
            layer for layer in layers
            if int((layer.get("metrics", {}).get("5d", {}) or {}).get("signals") or 0) >= 10
        ]
        ranked.sort(
            key=lambda layer: float((layer["metrics"]["5d"] or {}).get("expected_return") or 0),
            reverse=True,
        )
        summary = {
            "days": int(days),
            "layers": len(layers),
            "best_layer": ranked[0]["label"] if ranked else "样本不足",
            "positive_layers": sum(1 for layer in layers if layer.get("verdict", {}).get("status") == "POSITIVE"),
            "weak_layers": sum(1 for layer in layers if layer.get("verdict", {}).get("status") == "WEAK"),
            "execution_gap_5d": real_trade_execution.get("execution_gap_5d"),
        }
        notes = [
            "5日收益是主要判断口径；1/3日用于看买点效率，10日用于看持有延展。",
            "A-提前复核为新规则，历史样本可能不足；下一个交易日开始重点观察。",
            "回测统计不等于未来收益，仍需结合仓位、滑点和实际执行纪律。",
        ]
        return {
            "summary": summary,
            "layers": layers,
            "bark_success_profile": bark_success_profile,
            "real_trade_execution": real_trade_execution,
            "measurement_contract": _measurement_contract("profitability_layers"),
            "notes": notes,
        }
    except Exception as exc:
        logger.error(f"Profitability dashboard error: {exc}")
        return {"summary": {"days": int(days), "layers": 0}, "layers": [], "notes": [], "error": str(exc)}


@router.get("/research-context")
def get_research_context(force_refresh: bool = False) -> Dict[str, Any]:
    """Build a model-neutral, read-only snapshot for AI-assisted market review."""
    engine = get_db_engine()
    daily_report = get_daily_strategy_report()
    return build_ai_research_context(
        domestic_indices=get_index_data(),
        global_market=get_global_market_context(force_refresh=force_refresh),
        daily_report=daily_report,
        strategy_health=build_strategy_health(engine),
        research_radar=build_candidate_research_radar(engine, limit=10, force_refresh=force_refresh),
    )


@router.get("/recommendation-outcome-loop")
def get_recommendation_outcome_loop(days: int = 120) -> Dict[str, Any]:
    """Closed-loop outcome view for Bark/scan recommendations after 1/3/5/10 trading days."""
    engine = get_db_engine()
    if not engine:
        payload = _build_recommendation_outcome_loop(pd.DataFrame())
        payload["summary"]["days"] = int(days)
        return payload
    try:
        event_df = _load_recommendation_event_performance_df(days)
        payload = _build_recommendation_outcome_loop(event_df)
        payload["summary"]["days"] = int(days)
        payload["measurement_contract"] = _measurement_contract("recommendation_event")
        return payload
    except Exception as exc:
        logger.error(f"Recommendation outcome loop error: {exc}")
        payload = _build_recommendation_outcome_loop(pd.DataFrame())
        payload["summary"]["days"] = int(days)
        payload["error"] = str(exc)
        return payload


@router.get("/strategy-calibration-report")
def get_strategy_calibration_report(
    days: int = 120,
    min_grade_samples: int = 30,
    min_blocker_samples: int = 10,
) -> Dict[str, Any]:
    """Point-in-time strategy, grade, bucket, regime, and blocker outcome calibration."""
    days = max(1, min(int(days), 3650))
    min_grade_samples = max(1, int(min_grade_samples))
    min_blocker_samples = max(1, int(min_blocker_samples))
    engine = get_db_engine()
    if not engine:
        payload = build_calibration_report(pd.DataFrame(), min_samples=min_grade_samples)
        payload["summary"]["days"] = days
        payload["blocker_analysis"] = build_blocker_report(pd.DataFrame(), min_samples=min_blocker_samples)
        payload["notes"] = ["数据库未连接"]
        return payload
    try:
        outcomes = load_scan_outcomes(engine, days=days)
        payload = build_calibration_report(outcomes, min_samples=min_grade_samples)
        payload["summary"]["days"] = days
        payload["blocker_analysis"] = build_blocker_report(outcomes, min_samples=min_blocker_samples)
        payload["measurement_contract"] = _measurement_contract("calibration")
        payload["notes"] = [
            "信号价统一使用 daily_k 信号日收盘，避免扫描快照价与历史复权口径混用。",
            "未来第N个交易日收盘不存在时，该信号不计入N日成熟样本。",
            "blocker 对照是相关性诊断，不应未经样本外验证直接改动策略。",
        ]
        return payload
    except Exception as exc:
        logger.error(f"Strategy calibration report error: {exc}")
        payload = build_calibration_report(pd.DataFrame(), min_samples=min_grade_samples)
        payload["summary"]["days"] = days
        payload["blocker_analysis"] = build_blocker_report(pd.DataFrame(), min_samples=min_blocker_samples)
        payload["notes"] = []
        payload["error"] = str(exc)
        return payload


@router.get("/sector-watch-performance")
def get_sector_watch_performance(days: int = 120) -> Dict[str, Any]:
    engine = get_db_engine()
    if not engine:
        return _build_sector_watch_performance(pd.DataFrame(), pd.DataFrame())

    days = max(1, min(int(days), 365))
    try:
        cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=days)
        event_df = pd.read_sql(text("""
            SELECT event_time, code, name, theme, payload
            FROM lifecycle_events
            WHERE event_type = 'WATCHLIST_THEME_STATE_CHANGED'
              AND event_time >= :cutoff
            ORDER BY event_time DESC
        """), engine, params={"cutoff": cutoff.to_pydatetime()})
        if not event_df.empty:
            codes = sorted({str(code).zfill(6) for code in event_df["code"].dropna().astype(str)})
            stmt = text("""
                SELECT code, date, open, high, low, close, vol
                FROM daily_k
                WHERE code IN :codes
                ORDER BY code, date
            """).bindparams(bindparam("codes", expanding=True))
            daily_df = pd.read_sql(stmt, engine, params={"codes": codes}) if codes else pd.DataFrame()
            return _build_sector_watch_event_performance(event_df, daily_df)

        watch_df = pd.read_sql(text("""
            SELECT *
            FROM watchlist
            WHERE strategy_type = 'sector_watch'
               OR source = 'sector_push_gap'
        """), engine)
        if watch_df.empty:
            return _build_sector_watch_performance(watch_df, pd.DataFrame())

        created = pd.to_datetime(watch_df["created_at"], errors="coerce")
        watch_df = watch_df[created >= cutoff].copy()
        if watch_df.empty:
            return _build_sector_watch_performance(watch_df, pd.DataFrame())

        codes = sorted({str(code).zfill(6) for code in watch_df["code"].dropna().astype(str)})
        stmt = text("""
            SELECT code, date, open, high, low, close, vol
            FROM daily_k
            WHERE code IN :codes
            ORDER BY code, date
        """).bindparams(bindparam("codes", expanding=True))
        daily_df = pd.read_sql(stmt, engine, params={"codes": codes}) if codes else pd.DataFrame()
        return _build_sector_watch_performance(watch_df, daily_df)
    except Exception as exc:
        logger.error(f"Sector watch performance error: {exc}")
        return {
            "summary": {"items": 0, "mature_5d": 0, "best_state": "错误"},
            "by_state": [],
            "items": [],
            "notes": ["题材观察池复盘失败"],
            "error": str(exc),
        }


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

        def metric_frame(grouped: pd.DataFrame, key: str, return_col: str = "ret_5d") -> List[Dict[str, Any]]:
            rows = []
            for value, group in grouped:
                returns = group[return_col].dropna()
                if returns.empty:
                    continue
                metrics = return_metrics(returns)
                rows.append({
                    key: value or "未知",
                    **metrics,
                    "best_return": round(float(returns.max()), 2),
                    "worst_return": round(float(returns.min()), 2),
                })
            rows.sort(key=lambda r: (r["expected_return"], r["win_rate"], r["signals"]), reverse=True)
            return rows[:12]

        # Legacy rows often lack market/sector metadata but still contain valid
        # price-action signals and future prices. Keep them in price-action
        # outcome analysis while reporting metadata completeness separately.
        research_df = df[df["performance_eligible"].fillna(False)].copy()
        horizons = []
        for horizon in [1, 3, 5, 10, 20]:
            returns = research_df[f"ret_{horizon}d"].dropna()
            if returns.empty:
                horizons.append({"horizon": f"{horizon}日", "signals": 0, "win_rate": 0, "avg_return": 0})
            else:
                horizons.append({
                    "horizon": f"{horizon}日",
                    "signals": int(len(returns)),
                    "win_rate": round(float((returns > 0).mean() * 100), 1),
                    "avg_return": round(float(returns.mean()), 2),
                })

        by_strategy = metric_frame(research_df.groupby("strategy_type", dropna=False), "strategy")
        for row in by_strategy:
            row["health"] = classify_strategy_health(row)
        by_industry = metric_frame(research_df.groupby("industry", dropna=False), "industry")
        by_price_action = metric_frame(research_df.groupby("pa_trade_setup", dropna=False), "setup")
        by_pa_action = metric_frame(research_df.groupby("pa_trade_action", dropna=False), "action")
        by_pa_h2_quality = metric_frame(research_df.groupby("pa_h2_quality", dropna=False), "quality")
        by_pa_volume_pattern = metric_frame(research_df.groupby("pa_volume_pattern", dropna=False), "pattern")
        by_pa_trend_phase = metric_frame(research_df.groupby("pa_trend_phase", dropna=False), "phase")
        by_pa_weekly_context = metric_frame(research_df.groupby("pa_weekly_context", dropna=False), "context")
        by_pa_trap_risk = metric_frame(research_df.groupby("pa_trap_risk_bucket", dropna=False), "risk")
        by_trade_bucket = metric_frame(research_df.groupby("trade_bucket", dropna=False), "bucket")
        by_market_regime = metric_frame(research_df.groupby("market_regime", dropna=False), "regime")
        by_market_sentiment = metric_frame(research_df.groupby("market_sentiment_stage", dropna=False), "stage")
        by_next_open_gap = metric_frame(research_df.groupby("next_open_gap_bucket", dropna=False), "bucket")
        by_sector_phase = metric_frame(research_df.groupby("sector_phase", dropna=False), "phase")
        by_sector_role = metric_frame(research_df.groupby("sector_role", dropna=False), "role")
        by_sector_mainline = metric_frame(research_df.groupby("sector_mainline", dropna=False), "mainline")
        by_trade_state = metric_frame(research_df.groupby("trade_state", dropna=False), "state")
        research_df["opportunity_bucket"] = pd.cut(
            pd.to_numeric(research_df["trade_opportunity_score"], errors="coerce").fillna(0),
            bins=[-1, 59.99, 69.99, 79.99, 89.99, 1000],
            labels=["<60", "60-69", "70-79", "80-89", "90+"],
        )
        by_opportunity_bucket = metric_frame(research_df.groupby("opportunity_bucket", observed=False), "bucket")
        by_sector_alignment = metric_frame(research_df.groupby("sector_alignment_bucket", dropna=False), "bucket")
        by_sector_strength = metric_frame(research_df.groupby("sector_strength_bucket", dropna=False), "bucket")
        by_stock_sector_fit = metric_frame(research_df.groupby("stock_sector_fit_bucket", dropna=False), "bucket")

        ret_1d_all = research_df["ret_1d"].dropna()
        trade_ret_1d = research_df.loc[research_df["trade_bucket"].eq("TRADE"), "ret_1d"].dropna()
        blocked_ret_1d = research_df.loc[research_df["trade_bucket"].eq("BLOCK"), "ret_1d"].dropna()
        watch_ret_1d = research_df.loc[research_df["trade_bucket"].eq("WATCH"), "ret_1d"].dropna()
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
        data_quality = {
            "excluded_adjustment_gap_returns": int(df["data_quality_excluded"].fillna(False).sum()),
            "incomplete_research_samples": int((~df["research_eligible"].fillna(False)).sum()),
            "effective_research_samples": int(df["research_eligible"].fillna(False).sum()),
            "performance_eligible_samples": int(df["performance_eligible"].fillna(False).sum()),
            "rule": "次日开盘跳空>=14%且1日收益跳变>=20%的样本不参与收益统计",
        }
        path_df = research_df[research_df["mfe_20d_pct"].notna()].copy()
        path_metrics = {
            "tracked_signals": int(len(path_df)),
            "triggered_signals": int(research_df["triggered"].sum()),
            "trigger_rate": round(float(research_df["triggered"].mean() * 100), 1) if not research_df.empty else 0,
            "avg_mfe_20d_pct": round(float(path_df["mfe_20d_pct"].mean()), 2) if not path_df.empty else 0,
            "avg_mae_20d_pct": round(float(path_df["mae_20d_pct"].mean()), 2) if not path_df.empty else 0,
            "target_hit_rate_20d": round(float(path_df["target_hit_20d"].mean() * 100), 1) if not path_df.empty else 0,
            "stop_hit_rate_20d": round(float(path_df["stop_hit_20d"].mean() * 100), 1) if not path_df.empty else 0,
            "target_before_stop_rate": round(float(path_df["path_outcome"].eq("TARGET_FIRST").mean() * 100), 1) if not path_df.empty else 0,
            "ambiguous_paths": int(path_df["path_outcome"].eq("AMBIGUOUS").sum()) if not path_df.empty else 0,
            "triggered_avg_return_1d": round(float(research_df["triggered_ret_1d"].mean()), 2) if research_df["triggered_ret_1d"].notna().any() else 0,
            "triggered_avg_return_5d": round(float(research_df["triggered_ret_5d"].mean()), 2) if research_df["triggered_ret_5d"].notna().any() else 0,
            "triggered_win_rate_5d": round(float((research_df["triggered_ret_5d"].dropna() > 0).mean() * 100), 1) if research_df["triggered_ret_5d"].notna().any() else 0,
        }
        triggered_df = research_df[research_df["triggered"]].copy()
        score_buckets = {}
        for column in ("pa_structure_score", "pa_execution_score", "pa_risk_score"):
            triggered_df[f"{column}_bucket"] = pd.cut(
                pd.to_numeric(triggered_df[column], errors="coerce"),
                bins=[-1, 34.99, 49.99, 64.99, 79.99, 100],
                labels=["<35", "35-49", "50-64", "65-79", "80+"],
            )
            score_buckets[column] = metric_frame(
                triggered_df.groupby(f"{column}_bucket", observed=False),
                "bucket",
                return_col="triggered_ret_5d",
            )
        by_price_action_version = metric_frame(
            triggered_df.groupby("price_action_version", dropna=False),
            "version",
            return_col="triggered_ret_5d",
        )

        def strategy_backtest(name: str, mask: pd.Series) -> Dict[str, Any]:
            returns = research_df.loc[mask.reindex(research_df.index, fill_value=False), "ret_5d"].dropna()
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
        for date_value, group in research_df.groupby("signal_date"):
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

        ret_5d = research_df["ret_5d"].dropna()
        summary_metrics = return_metrics(ret_5d)
        best_bucket = by_industry[0]["industry"] if by_industry else "暂无"
        worst_bucket = by_industry[-1]["industry"] if by_industry else "暂无"
        return {
            "summary": {
                "signals": summary_metrics["signals"],
                "win_rate_5d": summary_metrics["win_rate"],
                "avg_return_5d": summary_metrics["avg_return"],
                "expected_return_5d": summary_metrics["expected_return"],
                "profit_loss_ratio_5d": summary_metrics["profit_loss_ratio"],
                "ci95_low_5d": summary_metrics["ci95_low"],
                "ci95_high_5d": summary_metrics["ci95_high"],
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
            "by_market_sentiment": by_market_sentiment,
            "by_next_open_gap": by_next_open_gap,
            "by_sector_phase": by_sector_phase,
            "by_sector_role": by_sector_role,
            "by_sector_mainline": by_sector_mainline,
            "by_trade_state": by_trade_state,
            "by_opportunity_bucket": by_opportunity_bucket,
            "by_sector_alignment": by_sector_alignment,
            "by_sector_strength": by_sector_strength,
            "by_stock_sector_fit": by_stock_sector_fit,
            "data_quality": data_quality,
            "path_metrics": path_metrics,
            "score_buckets": score_buckets,
            "by_price_action_version": by_price_action_version,
            "execution_summary": execution_summary,
            "recommendation_events": get_recommendation_event_review(days=days, limit=12).get("items", []),
            "portfolio_sim": get_portfolio_simulation(days=days, max_daily=3, hold_days=5),
            "brooks_backtests": brooks_backtests,
            "by_industry": by_industry,
            "recent_dates": recent[:20],
            "measurement_contract": _measurement_contract("scan"),
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
        eligible = df[df["research_eligible"].fillna(False) & df["trade_bucket"].eq("TRADE")].copy()
        eligible["rank_score"] = pd.to_numeric(eligible["calibrated_score"], errors="coerce").fillna(0)
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
                    SELECT * FROM (
                        SELECT DISTINCT ON (code)
                               code, name, industry, date, price, score, strategy_type,
                               pa_trade_action, pa_trade_setup, pa_entry_price, pa_stop_price,
                               COALESCE(price_action_detail->>'trade_bucket', 'UNKNOWN') AS trade_bucket,
                               COALESCE(price_action_detail->>'trade_eligible', 'false') AS trade_eligible,
                               COALESCE(price_action_detail->>'trade_blockers', '') AS trade_blockers
                        FROM scan_history
                        WHERE date = :date
                        ORDER BY code, score DESC
                    ) t
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
            high_open_buyability = _high_open_buyability(
                first_open_gap,
                entry_line,
                stop_line,
                base_price,
                min_low,
                latest_close,
                latest_gain,
            )

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
            elif high_open_buyability is not None:
                action = high_open_buyability["action"]
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
                "high_open_buyability": high_open_buyability,
                "high_open_state": high_open_buyability.get("state") if high_open_buyability else None,
                "high_open_gap_bucket": high_open_buyability.get("gap_bucket") if high_open_buyability else None,
                "high_open_buyable": high_open_buyability.get("buyable") if high_open_buyability else None,
                "high_open_required_confirmation": high_open_buyability.get("required_confirmation") if high_open_buyability else None,
                "high_open_risk": high_open_buyability.get("risk") if high_open_buyability else None,
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


def _daily_ops_bucket(item: Dict[str, Any]) -> str:
    action = str(item.get("execution_action") or "")
    status = str(item.get("followup_status") or "")
    bucket = str(item.get("trade_bucket") or "")
    blockers = str(item.get("trade_blockers") or "")
    if "风控" in status or "取消" in action:
        return "invalidated"
    if bucket == "BLOCK" or "禁止" in action or "不追" in action or "冲高回落" in action:
        return "no_chase"
    if "尾盘确认可试" in action:
        return "confirm_candidate"
    if "回踩" in action or "观察" in action or "涨幅偏高" in blockers:
        return "wait_pullback"
    return "watch"


def _daily_ops_instruction(item: Dict[str, Any]) -> str:
    entry = item.get("entry_line")
    stop = item.get("stop_line")
    bucket = _daily_ops_bucket(item)
    if bucket == "confirm_candidate":
        return f"只在站稳确认价 {entry or '--'} 且量能确认时小仓复核；跌破 {stop or '--'} 取消。"
    if bucket == "wait_pullback":
        return f"不追高；等回踩不破支撑/失效价 {stop or '--'} 后，重新站上确认价 {entry or '--'}。"
    if bucket == "no_chase":
        return "只复盘不交易；若次日高开或冲高回落，继续排除，等待二次确认。"
    if bucket == "invalidated":
        return f"已触发风控或结构失效；跌破 {stop or '--'} 后移出主动买入计划。"
    return f"继续观察；未站稳确认价 {entry or '--'} 前不下单。"


def _summarize_daily_ops_items(items: List[Dict[str, Any]], limit_per_bucket: int = 8) -> Dict[str, Any]:
    buckets = {
        "confirm_candidate": [],
        "wait_pullback": [],
        "no_chase": [],
        "invalidated": [],
        "watch": [],
    }
    for item in items:
        enriched = dict(item)
        enriched["ops_bucket"] = _daily_ops_bucket(enriched)
        enriched["ops_instruction"] = _daily_ops_instruction(enriched)
        buckets.setdefault(enriched["ops_bucket"], []).append(enriched)

    def rank_key(row: Dict[str, Any]) -> tuple:
        return (
            -float(row.get("max_gain_pct") or 0),
            -float(row.get("score") or 0),
            str(row.get("code") or ""),
        )

    return {
        key: sorted(value, key=rank_key)[:limit_per_bucket]
        for key, value in buckets.items()
    }


def _daily_ops_suggestions(summary: Dict[str, Any]) -> List[str]:
    counts = summary.get("bucket_counts") or {}
    suggestions = []
    if counts.get("no_chase", 0) >= counts.get("confirm_candidate", 0):
        suggestions.append("继续强化不追高规则：高开、涨停附近、冲高回落票默认进入观察，不给买入动作。")
    if counts.get("wait_pullback", 0) > 0:
        suggestions.append("把回踩确认作为主要执行入口：支撑不破、放量站上确认价、尾盘不回落三项同时满足。")
    if counts.get("confirm_candidate", 0) == 0:
        suggestions.append("今日没有明确买点，次日以观察池复核为主，不为了交易而交易。")
    else:
        suggestions.append("可试票只允许小仓，且必须写明确认价和失效价，避免信号变成追单。")
    if counts.get("invalidated", 0) > 0:
        suggestions.append("失效票应自动降权，后续重新入选必须等待新的策略信号，而不是凭题材反抽。")
    return suggestions[:5]


def _find_missed_strong_stocks(engine, date: Optional[str], selected_codes: set[str], limit: int = 12) -> List[Dict[str, Any]]:
    if not engine or not date:
        return []
    try:
        if engine.dialect.name == "sqlite":
            return []
        df = pd.read_sql(text("""
            WITH priced AS (
                SELECT
                    code, date, close,
                    LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close
                FROM daily_k
                WHERE date <= CAST(:date AS DATE)
                  AND date >= CAST(:date AS DATE) - INTERVAL '10 days'
            )
            SELECT p.code, COALESCE(b.name, p.code) AS name, p.close, p.prev_close,
                   (close - prev_close) / NULLIF(prev_close, 0) * 100 AS pct_chg
            FROM priced p
            LEFT JOIN stock_basic b ON b.code = p.code
            WHERE p.date = CAST(:date AS DATE)
              AND p.prev_close IS NOT NULL
              AND p.prev_close > 0
              AND (p.close - p.prev_close) / p.prev_close * 100 >= 7
            ORDER BY pct_chg DESC
            LIMIT :limit
        """), engine, params={"date": date, "limit": max(1, min(int(limit) * 3, 100))})
        if df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            code = str(row.get("code") or "").zfill(6)
            if code in selected_codes:
                continue
            rows.append({
                "code": code,
                "name": row.get("name"),
                "close": round(float(row.get("close") or 0), 2),
                "pct_chg": round(float(row.get("pct_chg") or 0), 2),
                "miss_reason": "当日涨幅>=7%但未进入扫描/推送候选，建议复盘是否属于强趋势加速或题材扩散。",
            })
            if len(rows) >= limit:
                break
        return rows
    except Exception as exc:
        logger.warning(f"Missed strong stock review skipped: {exc}")
        return []


@router.get("/daily-ops-review")
def get_daily_ops_review(date: Optional[str] = None, limit: int = 80) -> Dict[str, Any]:
    """A concise end-of-day decision report for improving selection and execution quality."""
    followup = get_next_day_followup(date=date, limit=limit)
    items = followup.get("items") or []
    grouped = _summarize_daily_ops_items(items)
    bucket_counts = {key: len(value) for key, value in grouped.items()}
    engine = get_db_engine()
    selected_codes = {str(item.get("code") or "").zfill(6) for item in items}
    missed = _find_missed_strong_stocks(engine, followup.get("date"), selected_codes)
    summary = {
        "date": followup.get("date"),
        "signals": len(items),
        "tracked": int((followup.get("summary") or {}).get("tracked") or 0),
        "bucket_counts": bucket_counts,
        "missed_strong_count": len(missed),
        "principle": "主线优先、买点确认、不追高、失效即取消",
    }
    return {
        "date": followup.get("date"),
        "summary": summary,
        "groups": grouped,
        "missed_strong": missed,
        "suggestions": _daily_ops_suggestions(summary),
        "source": "next_day_followup",
        "error": followup.get("error"),
    }


@router.get("/scan-performance/export")
def export_scan_performance(days: int = 120):
    df = _load_scan_performance_df(days)
    if df.empty:
        df = pd.DataFrame(columns=[
            "code", "name", "industry", "strategy_type", "signal_date", "price",
            "pa_trade_action", "pa_trade_setup", "pa_risk_pct",
            "pa_h2_quality", "pa_volume_pattern", "pa_trend_phase", "pa_weekly_context", "pa_trap_risk_bucket",
            "trade_bucket", "trade_eligible", "final_trade_score", "trade_blockers",
            "sector_phase", "sector_role", "sector_alignment_score", "sector_alignment_bucket",
            "data_quality_excluded",
            "triggered", "trigger_date", "actual_entry_price", "path_outcome",
            "mfe_20d_pct", "mae_20d_pct", "target_hit_20d", "stop_hit_20d", "target_before_stop",
            "triggered_ret_1d", "triggered_ret_3d", "triggered_ret_5d", "triggered_ret_10d", "triggered_ret_20d",
            "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
        ])

    export_cols = [
        "code", "name", "industry", "strategy_type", "signal_date", "price",
        "pa_trade_action", "pa_trade_setup", "pa_risk_pct",
        "pa_h2_quality", "pa_volume_pattern", "pa_trend_phase", "pa_weekly_context", "pa_trap_risk_bucket",
        "trade_bucket", "trade_eligible", "final_trade_score", "trade_blockers",
        "sector_phase", "sector_role", "sector_alignment_score", "sector_alignment_bucket",
        "data_quality_excluded",
        "triggered", "trigger_date", "actual_entry_price", "path_outcome",
        "mfe_20d_pct", "mae_20d_pct", "target_hit_20d", "stop_hit_20d", "target_before_stop",
        "triggered_ret_1d", "triggered_ret_3d", "triggered_ret_5d", "triggered_ret_10d", "triggered_ret_20d",
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
        "sector_phase": "板块阶段",
        "sector_role": "板块角色",
        "sector_alignment_score": "板块联动分",
        "sector_alignment_bucket": "板块联动分桶",
        "data_quality_excluded": "数据异常已剔除",
        "triggered": "真实触发",
        "trigger_date": "触发日期",
        "actual_entry_price": "实际入场价",
        "path_outcome": "路径结果",
        "mfe_20d_pct": "20日最大有利波动%",
        "mae_20d_pct": "20日最大不利波动%",
        "target_hit_20d": "20日内触达目标",
        "stop_hit_20d": "20日内触达止损",
        "target_before_stop": "目标先于止损",
        "triggered_ret_1d": "触发后1日收益%",
        "triggered_ret_3d": "触发后3日收益%",
        "triggered_ret_5d": "触发后5日收益%",
        "triggered_ret_10d": "触发后10日收益%",
        "triggered_ret_20d": "触发后20日收益%",
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
