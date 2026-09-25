import ast
import json
from typing import Any, Dict, Iterable, List

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from core.risk_constants import (
    SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_MIN_MATURE_SAMPLES,
    SOP_A_GRADE_MIN_PRICE_ACTION_SCORE,
    SOP_A_GRADE_MIN_SCORE,
    SOP_A_GRADE_POLICY_VERSION,
    SOP_A_GRADE_STRATEGIES,
    SOP_GRADE_EXECUTION_MODE,
    TRADE_GATE_MIN_MATURE_SAMPLES_PER_REGIME,
    TRADE_GATE_MIN_PROFIT_FACTOR,
    TRADE_GATE_MIN_SCORE_CORRELATION,
    TRADE_GATE_POLICY_VERSION,
)


HORIZONS = (1, 3, 5, 10)
BOTTOM_FOLLOWUP_WINDOW_DAYS = 30
INDEPENDENT_EVENT_RULE = "同代码同策略在前一事件5个交易日成熟前的重复信号只计一次"
PRICE_ACTION_SHADOW_FIELDS = (
    "pa_h2_state", "pa_follow_through_state", "pa_gap_type_v2", "pa_mtr_state",
    "pa_structure_state", "pa_sr_confluence_grade", "pa_mtf_state",
)


def _as_list(value: Any) -> List[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(raw)
                if isinstance(parsed, (list, tuple, set)):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                continue
        return [part.strip() for part in raw.split(";") if part.strip()]
    return [str(value).strip()]


def _detail_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def mark_independent_signal_events(df: pd.DataFrame) -> pd.DataFrame:
    """Mark non-overlapping code/strategy events using the exact 5-day maturity date."""
    marked = df.copy()
    marked["independent_event"] = False
    if marked.empty:
        return marked
    if "maturity_5d_date" not in marked.columns:
        marked["independent_event"] = True
        return marked

    signal_dates = pd.to_datetime(marked.get("signal_date"), errors="coerce")
    maturity_dates = pd.to_datetime(marked["maturity_5d_date"], errors="coerce")
    strategies = marked.get("strategy_type", pd.Series("UNKNOWN", index=marked.index)).fillna("UNKNOWN").astype(str)
    codes = marked.get("code", pd.Series("UNKNOWN", index=marked.index)).fillna("UNKNOWN").astype(str)
    ordered = marked.assign(_signal_date=signal_dates, _maturity_5d_date=maturity_dates)
    ordered = ordered.assign(_code=codes, _strategy=strategies).sort_values("_signal_date")
    selected: List[Any] = []
    for _, group in ordered.groupby(["_code", "_strategy"], sort=False):
        blocked_until = pd.NaT
        for idx, row in group.iterrows():
            signal_date = row["_signal_date"]
            if pd.isna(signal_date):
                continue
            if pd.isna(blocked_until) or signal_date > blocked_until:
                selected.append(idx)
                blocked_until = row["_maturity_5d_date"]
                if pd.isna(blocked_until):
                    blocked_until = signal_date
    marked.loc[selected, "independent_event"] = True
    return marked


def _independent_event_frame(df: pd.DataFrame) -> pd.DataFrame:
    if "independent_event" not in df.columns:
        return df
    return df[df["independent_event"].fillna(False).astype(bool)].copy()


def load_scan_outcomes(engine: Engine, days: int = 120) -> pd.DataFrame:
    """Load one point-in-time scan signal and future trading-day closes."""
    days = max(1, min(int(days), 3650))
    if engine.dialect.name == "sqlite":
        query = """
        WITH ranked_signals AS (
            SELECT s.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.code, COALESCE(s.data_date, s.date), COALESCE(s.strategy_type, 'squeeze')
                       ORDER BY s.scanned_at DESC, s.signal_id DESC
                   ) AS row_num
            FROM scan_history s
            WHERE date(COALESCE(s.data_date, s.date)) >= date('now', '-' || :days || ' days')
        ), signals AS (
            SELECT signal_id, code, name, COALESCE(data_date, date) AS signal_date,
                   COALESCE(strategy_type, 'squeeze') AS strategy_type,
                   COALESCE(sop_grade, json_extract(price_action_detail, '$.sop_grade'), 'UNKNOWN') AS sop_grade,
                   COALESCE(sop_quality_score, CAST(json_extract(price_action_detail, '$.sop_quality_score') AS REAL)) AS sop_quality_score,
                   COALESCE(json_extract(price_action_detail, '$.trade_bucket'), 'UNKNOWN') AS trade_bucket,
                   COALESCE(json_extract(price_action_detail, '$.trade_eligible'), 0) AS trade_eligible,
                   COALESCE(json_extract(price_action_detail, '$.market_regime'), 'UNKNOWN') AS market_regime,
                   COALESCE(json_extract(price_action_detail, '$.score_model_version'), 'legacy') AS score_model_version,
                   COALESCE(json_extract(price_action_detail, '$.sop_grade_policy_version'), 'legacy') AS sop_grade_policy_version,
                   COALESCE(json_extract(price_action_detail, '$.trade_gate_policy_version'), 'v1') AS trade_gate_policy_version,
                   CAST(json_extract(price_action_detail, '$.price_action_score') AS REAL) AS price_action_score,
                   CAST(json_extract(price_action_detail, '$.pct_5d') AS REAL) AS pct_5d,
                   sop_vetoes, price_action_detail, scanned_at
            FROM ranked_signals WHERE row_num = 1
        )
        SELECT s.*, d0.close AS signal_close,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 0) AS close_1d,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 2) AS close_3d,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 4) AS close_5d,
               (SELECT date FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 4) AS maturity_5d_date,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 9) AS close_10d,
               (SELECT MAX(d.high) FROM daily_k d
                WHERE d.code=s.code AND d.date>s.signal_date
                  AND d.date <= (SELECT d5.date FROM daily_k d5 WHERE d5.code=s.code AND d5.date>s.signal_date ORDER BY d5.date LIMIT 1 OFFSET 4)) AS high_5d,
               (SELECT MIN(d.low) FROM daily_k d
                WHERE d.code=s.code AND d.date>s.signal_date
                  AND d.date <= (SELECT d5.date FROM daily_k d5 WHERE d5.code=s.code AND d5.date>s.signal_date ORDER BY d5.date LIMIT 1 OFFSET 4)) AS low_5d
        FROM signals s JOIN daily_k d0 ON d0.code=s.code AND d0.date=s.signal_date
        """
    else:
        query = """
        WITH signals AS (
            SELECT DISTINCT ON (
                s.code,
                COALESCE(s.data_date, s.date),
                COALESCE(s.strategy_type, 'squeeze')
            )
                s.signal_id,
                s.code,
                s.name,
                COALESCE(s.data_date, s.date) AS signal_date,
                COALESCE(s.strategy_type, 'squeeze') AS strategy_type,
                COALESCE(s.sop_grade, s.price_action_detail->>'sop_grade', 'UNKNOWN') AS sop_grade,
                COALESCE(s.sop_quality_score, (s.price_action_detail->>'sop_quality_score')::float) AS sop_quality_score,
                COALESCE(s.price_action_detail->>'trade_bucket', 'UNKNOWN') AS trade_bucket,
                COALESCE((s.price_action_detail->>'trade_eligible')::boolean, false) AS trade_eligible,
                COALESCE(s.price_action_detail->>'market_regime', 'UNKNOWN') AS market_regime,
                COALESCE(s.price_action_detail->>'score_model_version', 'legacy') AS score_model_version,
                COALESCE(s.price_action_detail->>'sop_grade_policy_version', 'legacy') AS sop_grade_policy_version,
                COALESCE(s.price_action_detail->>'trade_gate_policy_version', 'v1') AS trade_gate_policy_version,
                COALESCE(s.price_action_score, NULLIF(s.price_action_detail->>'price_action_score', '')::float) AS price_action_score,
                NULLIF(s.price_action_detail->>'pct_5d', '')::float AS pct_5d,
                s.sop_vetoes,
                s.price_action_detail,
                s.scanned_at
            FROM scan_history s
            WHERE COALESCE(s.data_date, s.date) >= CURRENT_DATE - (:days || ' days')::interval
            ORDER BY
                s.code,
                COALESCE(s.data_date, s.date),
                COALESCE(s.strategy_type, 'squeeze'),
                s.scanned_at DESC NULLS LAST,
                s.signal_id DESC
        )
        SELECT
            s.*,
            d0.close AS signal_close,
            h1.close AS close_1d,
            h3.close AS close_3d,
            h5.close AS close_5d,
            h5.date AS maturity_5d_date,
            h10.close AS close_10d,
            (SELECT MAX(d.high) FROM daily_k d
             WHERE d.code=s.code AND d.date>s.signal_date
               AND d.date <= (SELECT d5.date FROM daily_k d5 WHERE d5.code=s.code AND d5.date>s.signal_date ORDER BY d5.date OFFSET 4 LIMIT 1)) AS high_5d,
            (SELECT MIN(d.low) FROM daily_k d
             WHERE d.code=s.code AND d.date>s.signal_date
               AND d.date <= (SELECT d5.date FROM daily_k d5 WHERE d5.code=s.code AND d5.date>s.signal_date ORDER BY d5.date OFFSET 4 LIMIT 1)) AS low_5d
        FROM signals s
        JOIN daily_k d0 ON d0.code = s.code AND d0.date = s.signal_date
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d
            WHERE d.code = s.code AND d.date > s.signal_date
            ORDER BY d.date ASC OFFSET 0 LIMIT 1
        ) h1 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d
            WHERE d.code = s.code AND d.date > s.signal_date
            ORDER BY d.date ASC OFFSET 2 LIMIT 1
        ) h3 ON true
        LEFT JOIN LATERAL (
            SELECT date, close FROM daily_k d
            WHERE d.code = s.code AND d.date > s.signal_date
            ORDER BY d.date ASC OFFSET 4 LIMIT 1
        ) h5 ON true
        LEFT JOIN LATERAL (
            SELECT close FROM daily_k d
            WHERE d.code = s.code AND d.date > s.signal_date
            ORDER BY d.date ASC OFFSET 9 LIMIT 1
        ) h10 ON true
        """
    df = pd.read_sql(text(query), engine, params={"days": days})
    if df.empty:
        return df

    signal_close = pd.to_numeric(df["signal_close"], errors="coerce")
    for horizon in HORIZONS:
        future_close = pd.to_numeric(df[f"close_{horizon}d"], errors="coerce")
        df[f"ret_{horizon}d"] = (future_close - signal_close) / signal_close * 100
    df["mature_5d"] = pd.to_numeric(df["close_5d"], errors="coerce").notna()
    df["mfe_5d"] = ((pd.to_numeric(df["high_5d"], errors="coerce") - signal_close) / signal_close * 100).where(df["mature_5d"])
    df["mae_5d"] = ((pd.to_numeric(df["low_5d"], errors="coerce") - signal_close) / signal_close * 100).where(df["mature_5d"])

    blockers: List[List[str]] = []
    extracted = {
        key: [] for key in (
            "grade_stage", "decision_lifecycle_state", "confirmation_event_state",
            "early_value_transition_state", "sector_phase", "bottom_discovery_stage",
            *PRICE_ACTION_SHADOW_FIELDS,
        )
    }
    research_eligible: List[bool] = []
    opportunity_scores: List[Any] = []
    final_trade_scores: List[Any] = []
    for _, row in df.iterrows():
        detail = _detail_dict(row.get("price_action_detail"))
        for key in extracted:
            extracted[key].append(detail.get(key) or "UNKNOWN")
        research_value = detail.get("research_eligible")
        research_eligible.append(
            research_value is True
            or research_value == 1
            or str(research_value).strip().lower() == "true"
        )
        opportunity_scores.append(detail.get("trade_opportunity_score"))
        final_trade_scores.append(detail.get("final_trade_score"))
        values: Iterable[str] = (
            _as_list(detail.get("trade_blockers"))
            + _as_list(row.get("sop_vetoes"))
            + _as_list(detail.get("sop_vetoes"))
        )
        blockers.append(list(dict.fromkeys(item for item in values if item)))
    for key, values in extracted.items():
        df[key] = values
    df["research_eligible"] = pd.Series(research_eligible, index=df.index, dtype=bool)
    df["trade_opportunity_score"] = pd.to_numeric(
        pd.Series(opportunity_scores, index=df.index), errors="coerce"
    )
    df["final_trade_score"] = pd.to_numeric(
        pd.Series(final_trade_scores, index=df.index), errors="coerce"
    )
    df["blockers"] = blockers
    return mark_independent_signal_events(df)


def build_execution_cohort_report(df: pd.DataFrame) -> Dict[str, Any]:
    """Compare research, blocked, and executable cohorts on the same future-return basis."""
    if df.empty:
        return {"cohorts": [], "primary_horizon": "5d", "notes": ["暂无成熟扫描样本"]}
    df = _independent_event_frame(df)
    research = df.get("research_eligible", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    executable = (
        df.get("trade_eligible", pd.Series(False, index=df.index)).fillna(False).astype(bool)
        & df.get("trade_bucket", pd.Series("", index=df.index)).astype(str).str.upper().eq("TRADE")
    )
    masks = {
        "research_candidate": research,
        "blocked_candidate": research & ~executable,
        "executable_candidate": executable,
    }
    labels = {
        "research_candidate": "全部研究候选",
        "blocked_candidate": "被执行门禁拦截",
        "executable_candidate": "Bark可交易候选",
    }
    cohorts = []
    for name, mask in masks.items():
        group = df[mask]
        cohorts.append({
            "cohort": name,
            "label": labels[name],
            "signals": int(len(group)),
            "metrics": {f"{horizon}d": _metric_summary(group[f"ret_{horizon}d"]) for horizon in HORIZONS},
        })
    return {
        "cohorts": cohorts,
        "primary_horizon": "5d",
        "notes": ["各组使用同一信号日收盘和未来交易日口径；相关性诊断不等于因果。"],
    }


def build_opportunity_threshold_report(
    df: pd.DataFrame, thresholds: Iterable[float] = (55, 60, 65, 70),
) -> Dict[str, Any]:
    """Report threshold sensitivity without changing the production threshold."""
    if df.empty or "trade_opportunity_score" not in df.columns:
        return {"thresholds": [], "production_threshold": 60, "status": "INSUFFICIENT"}
    df = _independent_event_frame(df)
    research = df.get("research_eligible", pd.Series(False, index=df.index)).fillna(False).astype(bool)
    scores = pd.to_numeric(df["trade_opportunity_score"], errors="coerce")
    rows = []
    for threshold in thresholds:
        group = df[research & scores.ge(float(threshold))]
        rows.append({
            "threshold": float(threshold),
            "signals": int(len(group)),
            "metrics_5d": _metric_summary(group["ret_5d"]),
        })
    return {
        "thresholds": rows,
        "production_threshold": 60,
        "status": "DIAGNOSTIC_ONLY",
        "selection_rule": "research_eligible=true and trade_opportunity_score>=threshold",
    }


def _metric_summary(values: pd.Series) -> Dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return {
            "signals": 0,
            "win_rate": 0.0,
            "avg_return": None,
            "median_return": None,
            "profit_factor": None,
            "best_return": None,
            "worst_return": None,
        }
    gains = clean[clean > 0].sum()
    losses = abs(clean[clean < 0].sum())
    return {
        "signals": int(len(clean)),
        "win_rate": round(float((clean > 0).mean() * 100), 1),
        "avg_return": round(float(clean.mean()), 2),
        "median_return": round(float(clean.median()), 2),
        "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
        "best_return": round(float(clean.max()), 2),
        "worst_return": round(float(clean.min()), 2),
    }


def build_a_grade_policy_report(
    df: pd.DataFrame,
    min_samples: int = SOP_A_GRADE_MIN_MATURE_SAMPLES,
) -> Dict[str, Any]:
    """Compare persisted A grades with the current strategy-calibrated A gate."""
    min_samples = max(1, int(min_samples))

    def summarize(group: pd.DataFrame) -> Dict[str, Any]:
        ret_5d = group.get("ret_5d", pd.Series(index=group.index, dtype=float))
        return {
            "signals": int(len(group)),
            "mature_5d": int(pd.to_numeric(ret_5d, errors="coerce").notna().sum()),
            "metrics": {
                f"{horizon}d": _metric_summary(group.get(f"ret_{horizon}d", pd.Series(dtype=float)))
                for horizon in HORIZONS
            },
        }

    if df.empty:
        empty = summarize(pd.DataFrame())
        return {
            "status": "INSUFFICIENT_DATA",
            "policy_version": SOP_A_GRADE_POLICY_VERSION,
            "min_mature_samples": min_samples,
            "baseline": empty,
            "proposed": empty,
        }

    grades = df.get("sop_grade", pd.Series("UNKNOWN", index=df.index)).astype(str)
    strategies = df.get("strategy_type", pd.Series("", index=df.index)).astype(str)
    scores = pd.to_numeric(df.get("sop_quality_score", pd.Series(index=df.index, dtype=float)), errors="coerce")
    pa_scores = pd.to_numeric(df.get("price_action_score", pd.Series(index=df.index, dtype=float)), errors="coerce")
    pct_5d = pd.to_numeric(df.get("pct_5d", pd.Series(index=df.index, dtype=float)), errors="coerce")
    vetoes = df.get("sop_vetoes", pd.Series([[] for _ in range(len(df))], index=df.index)).apply(_as_list)
    baseline = df[grades.eq("A")]
    proposed = df[
        strategies.isin(SOP_A_GRADE_STRATEGIES)
        & scores.ge(SOP_A_GRADE_MIN_SCORE)
        & pa_scores.ge(SOP_A_GRADE_MIN_PRICE_ACTION_SCORE)
        & pct_5d.le(SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT)
        & vetoes.apply(lambda items: not items)
    ]
    proposed_summary = summarize(proposed)
    metrics_5d = proposed_summary["metrics"]["5d"]
    if proposed_summary["mature_5d"] < min_samples:
        status = "INSUFFICIENT_DATA"
    elif (
        metrics_5d["win_rate"] >= 55
        and (metrics_5d["avg_return"] or 0) >= 1.5
        and (metrics_5d["profit_factor"] or 0) >= 1.5
    ):
        status = "VALIDATED"
    else:
        status = "NOT_SUPPORTED"
    return {
        "status": status,
        "policy_version": SOP_A_GRADE_POLICY_VERSION,
        "min_mature_samples": min_samples,
        "selection_rule": (
            f"strategy_type in {list(SOP_A_GRADE_STRATEGIES)}, "
            f"sop_quality_score>={SOP_A_GRADE_MIN_SCORE:g}, "
            f"price_action_score>={SOP_A_GRADE_MIN_PRICE_ACTION_SCORE:g}, "
            f"pct_5d<={SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT:g}, sop_vetoes=[]"
        ),
        "validation_targets": {"win_rate_5d": 55, "avg_return_5d": 1.5, "profit_factor_5d": 1.5},
        "baseline": summarize(baseline),
        "proposed": proposed_summary,
        "measurement_note": "按信号日收盘到未来交易日收盘衡量结构质量；不等同于可成交收益。",
    }


def _group_rows(df: pd.DataFrame, column: str) -> List[Dict[str, Any]]:
    if column not in df.columns or df.empty:
        return []
    rows: List[Dict[str, Any]] = []
    for value, group in df.groupby(df[column].fillna("UNKNOWN").astype(str), dropna=False):
        metrics = {f"{horizon}d": _metric_summary(group[f"ret_{horizon}d"]) for horizon in HORIZONS}
        rows.append({
            "value": value,
            "signals": int(len(group)),
            "mature_5d": metrics["5d"]["signals"],
            "metrics": metrics,
        })
    rows.sort(
        key=lambda row: (
            row["metrics"]["5d"]["avg_return"]
            if row["metrics"]["5d"]["avg_return"] is not None else -999,
            row["mature_5d"],
        ),
        reverse=True,
    )
    return rows


def build_bottom_discovery_report(df: pd.DataFrame, min_samples: int = 30) -> Dict[str, Any]:
    """Evaluate observation-only B0/B1 signals without treating them as executions."""
    min_samples = max(1, int(min_samples))
    if df.empty or "strategy_type" not in df.columns:
        raw_bottom = pd.DataFrame()
    else:
        raw_bottom = df[df["strategy_type"].astype(str).eq("bottom_discovery")].copy()
    bottom = _independent_event_frame(raw_bottom)

    stage_rows = _group_rows(bottom, "bottom_discovery_stage")
    for row in stage_rows:
        stage = bottom[bottom["bottom_discovery_stage"].fillna("UNKNOWN").astype(str).eq(row["value"])]
        mfe = pd.to_numeric(stage.get("mfe_5d", pd.Series(dtype=float)), errors="coerce").dropna()
        mae = pd.to_numeric(stage.get("mae_5d", pd.Series(dtype=float)), errors="coerce").dropna()
        row["excursion_5d"] = {
            "samples": int(min(len(mfe), len(mae))),
            "avg_mfe": round(float(mfe.mean()), 2) if not mfe.empty else None,
            "avg_mae": round(float(mae.mean()), 2) if not mae.empty else None,
            "mfe_ge_5_rate": round(float(mfe.ge(5).mean() * 100), 1) if not mfe.empty else None,
            "mae_le_minus_4_rate": round(float(mae.le(-4).mean() * 100), 1) if not mae.empty else None,
        }

    conversions = []
    if not raw_bottom.empty and "bottom_discovery_stage" in raw_bottom.columns:
        dated = raw_bottom.assign(_signal_date=pd.to_datetime(raw_bottom["signal_date"], errors="coerce")).dropna(subset=["_signal_date"])
        for code, group in dated.groupby(dated["code"].astype(str)):
            b0_dates = group.loc[group["bottom_discovery_stage"].eq("B0_BASE"), "_signal_date"]
            b1_dates = group.loc[group["bottom_discovery_stage"].eq("B1_REVERSAL"), "_signal_date"]
            if b0_dates.empty:
                continue
            first_b0 = b0_dates.min()
            later_b1 = b1_dates[b1_dates > first_b0]
            conversions.append({
                "code": code,
                "converted": not later_b1.empty,
                "wait_days": int((later_b1.min() - first_b0).days) if not later_b1.empty else None,
            })
    converted = [item for item in conversions if item["converted"]]
    wait_days = pd.Series([item["wait_days"] for item in converted], dtype=float)

    confirmation_window_days = BOTTOM_FOLLOWUP_WINDOW_DAYS
    confirmation_rows = []
    strict_rows = []
    b1_events = pd.DataFrame()
    b1_total = 0
    if not bottom.empty and "bottom_discovery_stage" in bottom.columns and "signal_date" in df.columns:
        events = df.copy()
        signal_time = pd.to_datetime(events["signal_date"], errors="coerce", utc=True)
        scanned_time = pd.to_datetime(
            events.get("scanned_at", pd.Series(index=events.index, dtype=object)), errors="coerce", utc=True,
        )
        events["_event_time"] = scanned_time.fillna(signal_time)
        events = events.dropna(subset=["_event_time"])
        b1_events = events[
            events["strategy_type"].astype(str).eq("bottom_discovery")
            & events.get("bottom_discovery_stage", pd.Series(index=events.index, dtype=object)).eq("B1_REVERSAL")
        ].sort_values("_event_time").drop_duplicates(subset=["code"], keep="first")
        b1_total = int(len(b1_events))
        evaluation_time = events["_event_time"].max()
        b1_events = b1_events[
            b1_events["_event_time"].le(evaluation_time - pd.Timedelta(days=confirmation_window_days))
        ]
        eligible = events.get("trade_eligible", pd.Series(False, index=events.index)).fillna(False).astype(bool)
        trade_bucket = events.get("trade_bucket", pd.Series("", index=events.index)).fillna("").astype(str).str.upper()
        strategy = events["strategy_type"].fillna("").astype(str)
        for _, b1 in b1_events.iterrows():
            deadline = b1["_event_time"] + pd.Timedelta(days=confirmation_window_days)
            later = events[
                events["code"].astype(str).eq(str(b1["code"]))
                & events["_event_time"].gt(b1["_event_time"])
                & events["_event_time"].le(deadline)
            ].sort_values("_event_time")
            formal = later[
                eligible.reindex(later.index).fillna(False)
                & trade_bucket.reindex(later.index).eq("TRADE")
                & ~strategy.reindex(later.index).eq("bottom_discovery")
            ]
            strict = later[strategy.reindex(later.index).eq("tv_dual_strict")]
            if not formal.empty:
                first = formal.iloc[0]
                confirmation_rows.append({
                    "wait_days": float((first["_event_time"] - b1["_event_time"]).total_seconds() / 86400),
                    "strategy_type": str(first["strategy_type"]),
                })
            if not strict.empty:
                first = strict.iloc[0]
                strict_rows.append(float((first["_event_time"] - b1["_event_time"]).total_seconds() / 86400))

    b1_count = int(len(b1_events))
    confirmation_waits = pd.Series([row["wait_days"] for row in confirmation_rows], dtype=float)
    strict_waits = pd.Series(strict_rows, dtype=float)
    confirmed_by_strategy = pd.Series([row["strategy_type"] for row in confirmation_rows], dtype=str).value_counts().to_dict()
    mature_by_stage = {row["value"]: row["mature_5d"] for row in stage_rows}
    validated = mature_by_stage.get("B1_REVERSAL", 0) >= min_samples
    return {
        "status": "VALIDATED" if validated else "INSUFFICIENT_DATA",
        "min_mature_b1_samples": min_samples,
        "signals": int(len(bottom)),
        "raw_signals": int(len(raw_bottom)),
        "by_stage": stage_rows,
        "conversion": {
            "b0_unique_stocks": len(conversions),
            "converted_to_b1": len(converted),
            "conversion_rate": round(len(converted) / len(conversions) * 100, 1) if conversions else None,
            "median_wait_calendar_days": round(float(wait_days.median()), 1) if not wait_days.empty else None,
        },
        "formal_confirmation": {
            "window_calendar_days": confirmation_window_days,
            "b1_unique_stocks": b1_total,
            "mature_b1_followups": b1_count,
            "confirmed_stocks": len(confirmation_rows),
            "confirmation_rate": round(len(confirmation_rows) / b1_count * 100, 1) if b1_count else None,
            "median_wait_calendar_days": round(float(confirmation_waits.median()), 1) if not confirmation_waits.empty else None,
            "within_10_days": int(confirmation_waits.le(10).sum()),
            "confirmed_by_strategy": confirmed_by_strategy,
        },
        "strict_strategy_lead": {
            "window_calendar_days": confirmation_window_days,
            "mature_b1_followups": b1_count,
            "matched_stocks": len(strict_rows),
            "match_rate": round(len(strict_rows) / b1_count * 100, 1) if b1_count else None,
            "median_lead_calendar_days": round(float(strict_waits.median()), 1) if not strict_waits.empty else None,
        },
        "measurement_note": "MFE/MAE使用信号后5个交易日最高/最低价；事件漏斗只向前关联30个自然日内的后续信号，不代表可交易收益。",
    }


def _grade_monotonicity(by_grade: List[Dict[str, Any]], min_samples: int) -> Dict[str, Any]:
    grades = {row["value"]: row for row in by_grade}
    required = ("A", "B", "C")
    sample_counts = {grade: int((grades.get(grade) or {}).get("mature_5d") or 0) for grade in required}
    if any(sample_counts[grade] < min_samples for grade in required):
        return {
            "status": "INSUFFICIENT",
            "metric": "avg_return_5d",
            "min_samples": min_samples,
            "samples": sample_counts,
            "reason": "A/B/C 至少一个等级的5日成熟样本不足",
        }
    values = {grade: grades[grade]["metrics"]["5d"]["avg_return"] for grade in required}
    passed = values["A"] > values["B"] > values["C"]
    return {
        "status": "PASS" if passed else "FAIL",
        "metric": "avg_return_5d",
        "min_samples": min_samples,
        "samples": sample_counts,
        "values": values,
        "reason": "质量层级与5日平均收益保持单调" if passed else "质量分层与5日平均收益未形成单调关系",
    }


def build_price_action_shadow_calibration(df: pd.DataFrame, min_samples: int = 30) -> Dict[str, Any]:
    """Measure new price-action states without allowing them to affect live eligibility."""
    min_samples = max(1, int(min_samples))
    independent = _independent_event_frame(df)
    mature = int(pd.to_numeric(independent.get("ret_5d", pd.Series(dtype=float)), errors="coerce").notna().sum())
    return {
        "status": "READY_FOR_OOS_REVIEW" if mature >= min_samples else "INSUFFICIENT_DATA",
        "production_effect": False,
        "min_mature_samples": min_samples,
        "mature_5d": mature,
        "dimensions": {
            field: _group_rows(independent, field)
            for field in PRICE_ACTION_SHADOW_FIELDS
        },
        "promotion_rule": "累计足够成熟独立样本后，仍需滚动样本外验证；当前不自动调权。",
    }


def build_calibration_report(df: pd.DataFrame, min_samples: int = 30) -> Dict[str, Any]:
    min_samples = max(1, int(min_samples))
    if df.empty:
        return {
            "summary": {"raw_signals": 0, "signals": 0, "mature_5d": 0, "mature_10d": 0},
            "horizons": {f"{horizon}d": _metric_summary(pd.Series(dtype=float)) for horizon in HORIZONS},
            "by_strategy": [],
            "by_grade": [],
            "by_trade_bucket": [],
            "by_market_regime": [],
            "by_score_model_version": [],
            "by_sop_grade_policy_version": [],
            "by_grade_stage": [],
            "by_lifecycle_state": [],
            "by_confirmation_event": [],
            "by_early_value_transition": [],
            "bottom_discovery_analysis": build_bottom_discovery_report(pd.DataFrame(), min_samples),
            "price_action_shadow_calibration": build_price_action_shadow_calibration(pd.DataFrame(), min_samples),
            "grade_monotonicity": _grade_monotonicity([], min_samples),
            "a_grade_policy": build_a_grade_policy_report(pd.DataFrame(), min_samples),
            "grade_usage": {"mode": "SHADOW_ONLY", "production_effect": False, "reason": "暂无可校准样本"},
        }

    raw_df = df
    df = _independent_event_frame(df)
    horizons = {f"{horizon}d": _metric_summary(df[f"ret_{horizon}d"]) for horizon in HORIZONS}
    by_grade = _group_rows(df, "sop_grade")
    signal_dates = pd.to_datetime(raw_df.get("signal_date"), errors="coerce")
    grade_monotonicity = _grade_monotonicity(by_grade, min_samples)
    a_grade_policy = build_a_grade_policy_report(df, min_samples)
    grade_validated = grade_monotonicity["status"] == "PASS" and a_grade_policy["status"] == "VALIDATED"
    grade_active = SOP_GRADE_EXECUTION_MODE == "ACTIVE" and grade_validated
    report = {
        "summary": {
            "raw_signals": int(len(raw_df)),
            "signals": int(len(df)),
            "mature_5d": horizons["5d"]["signals"],
            "mature_10d": horizons["10d"]["signals"],
            "latest_signal_date": str(signal_dates.max().date()) if signal_dates.notna().any() else None,
            "price_basis": "daily_k_signal_close",
            "maturity_rule": "未来第N个交易日收盘存在时才计入N日样本",
            "event_dedup_rule": INDEPENDENT_EVENT_RULE,
        },
        "horizons": horizons,
        "by_strategy": _group_rows(df, "strategy_type"),
        "by_grade": by_grade,
        "by_trade_bucket": _group_rows(df, "trade_bucket"),
        "by_market_regime": _group_rows(df, "market_regime"),
        "by_score_model_version": _group_rows(df, "score_model_version"),
        "by_sop_grade_policy_version": _group_rows(df, "sop_grade_policy_version"),
        "by_trade_gate_policy_version": _group_rows(df, "trade_gate_policy_version"),
        "by_grade_stage": _group_rows(df, "grade_stage"),
        "by_lifecycle_state": _group_rows(df, "decision_lifecycle_state"),
        "by_confirmation_event": _group_rows(df, "confirmation_event_state"),
        "by_early_value_transition": _group_rows(df, "early_value_transition_state"),
        "bottom_discovery_analysis": build_bottom_discovery_report(raw_df, min_samples),
        "price_action_shadow_calibration": build_price_action_shadow_calibration(raw_df, min_samples),
        "grade_monotonicity": grade_monotonicity,
        "a_grade_policy": a_grade_policy,
        "grade_usage": {
            "mode": "ACTIVE" if grade_active else "SHADOW_ONLY",
            "production_effect": grade_active,
            "reason": "历史质量顺序通过成熟样本验证" if grade_active else "历史分层只作结构描述，不作为新增可交易资格证据",
            "configured_mode": SOP_GRADE_EXECUTION_MODE,
            "validation_passed": grade_validated,
        },
    }
    if "exec_return_pct" in df.columns:
        filled = df["exec_filled"].fillna(False).astype(bool) if "exec_filled" in df.columns else pd.Series(False, index=df.index)
        mature = df["exec_mature"].fillna(False).astype(bool) if "exec_mature" in df.columns else pd.Series(False, index=df.index)
        executable = df[filled]
        report["executable"] = _metric_summary(executable["exec_return_pct"])
        report["summary"]["executable_mature"] = int(mature.sum())
        report["summary"]["executable_filled"] = int(len(executable))
    return report


def build_blocker_report(df: pd.DataFrame, min_samples: int = 10) -> Dict[str, Any]:
    min_samples = max(1, int(min_samples))
    if df.empty or "blockers" not in df.columns:
        return {"summary": {"blockers": 0, "mature_5d": 0}, "items": []}

    normalized = _independent_event_frame(df).copy()
    normalized["blockers"] = normalized["blockers"].apply(_as_list)
    all_blockers = sorted({item for values in normalized["blockers"] for item in values})
    control_columns = [
        column for column in ("strategy_type", "market_regime", "score_model_version")
        if column in normalized.columns
    ]
    items: List[Dict[str, Any]] = []
    for blocker in all_blockers:
        hit_mask = normalized["blockers"].apply(lambda values: blocker in values)
        hit = _metric_summary(normalized.loc[hit_mask, "ret_5d"])
        miss = _metric_summary(normalized.loc[~hit_mask, "ret_5d"])
        hit_avg = hit["avg_return"]
        miss_avg = miss["avg_return"]
        avg_delta = round(float(hit_avg - miss_avg), 2) if hit_avg is not None and miss_avg is not None else None
        win_delta = round(float(hit["win_rate"] - miss["win_rate"]), 1)
        controlled_rows = []
        if control_columns:
            for values, group in normalized.groupby(control_columns, dropna=False):
                values = values if isinstance(values, tuple) else (values,)
                group_hit_mask = group["blockers"].apply(lambda items: blocker in items)
                hit_returns = pd.to_numeric(group.loc[group_hit_mask, "ret_5d"], errors="coerce").dropna()
                miss_returns = pd.to_numeric(group.loc[~group_hit_mask, "ret_5d"], errors="coerce").dropna()
                if hit_returns.empty or miss_returns.empty:
                    continue
                weight = min(len(hit_returns), len(miss_returns))
                controlled_rows.append({
                    "segment": dict(zip(control_columns, (str(value) for value in values))),
                    "weight": weight,
                    "avg_delta": float(hit_returns.mean() - miss_returns.mean()),
                    "win_delta": float((hit_returns.gt(0).mean() - miss_returns.gt(0).mean()) * 100),
                })
        controlled_weight = sum(row["weight"] for row in controlled_rows)
        controlled_avg_delta = (
            round(sum(row["avg_delta"] * row["weight"] for row in controlled_rows) / controlled_weight, 2)
            if controlled_weight else None
        )
        controlled_win_delta = (
            round(sum(row["win_delta"] * row["weight"] for row in controlled_rows) / controlled_weight, 1)
            if controlled_weight else None
        )
        decision_avg_delta = controlled_avg_delta if controlled_avg_delta is not None else avg_delta
        decision_win_delta = controlled_win_delta if controlled_win_delta is not None else win_delta
        if hit["signals"] < min_samples or miss["signals"] < min_samples:
            recommendation = "RESEARCH_ONLY"
        elif decision_avg_delta is not None and decision_avg_delta <= -0.5 and decision_win_delta <= -5:
            recommendation = "VALID_FILTER"
        elif decision_avg_delta is not None and (decision_avg_delta >= 0.5 or decision_win_delta >= 5):
            recommendation = "REVIEW_RULE"
        else:
            recommendation = "EXPLANATION_ONLY"
        items.append({
            "blocker": blocker,
            "occurrences": int(hit_mask.sum()),
            "hit": hit,
            "miss": miss,
            "hit_minus_miss_avg_return": avg_delta,
            "hit_minus_miss_win_rate": win_delta,
            "controlled_comparison": {
                "dimensions": control_columns,
                "segments": len(controlled_rows),
                "paired_samples": controlled_weight,
                "hit_minus_miss_avg_return": controlled_avg_delta,
                "hit_minus_miss_win_rate": controlled_win_delta,
            },
            "recommendation": recommendation,
        })
    items.sort(key=lambda row: (row["recommendation"] != "REVIEW_RULE", row["hit_minus_miss_avg_return"] or 0))
    return {
        "summary": {
            "blockers": len(items),
            "mature_5d": int(pd.to_numeric(normalized["ret_5d"], errors="coerce").notna().sum()),
            "min_samples": min_samples,
            "valid_filters": sum(1 for row in items if row["recommendation"] == "VALID_FILTER"),
            "review_rules": sum(1 for row in items if row["recommendation"] == "REVIEW_RULE"),
        },
        "items": items,
        "recommendation_labels": {
            "VALID_FILTER": "命中组明显更弱，保留过滤价值",
            "REVIEW_RULE": "命中组不弱于未命中组，建议复核规则",
            "EXPLANATION_ONLY": "区分度不足，建议只解释不扣分",
            "RESEARCH_ONLY": "成熟样本不足，只研究不调权",
        },
        "control_note": "优先按策略、市场状态和评分版本分层比较；无可配对分层时回退到全样本相关性。",
    }


def build_feature_ablation_report(
    df: pd.DataFrame,
    features: Iterable[str],
    outcome: str = "exec_return_pct",
    min_samples: int = 30,
) -> Dict[str, Any]:
    """Report marginal rank association; causal promotion still requires rolling OOS."""
    df = _independent_event_frame(df)
    if outcome not in df.columns:
        return {"outcome": outcome, "samples": 0, "items": []}
    items = []
    for feature in features:
        if feature not in df.columns:
            continue
        pair = df[[feature, outcome]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(pair) < min_samples or pair[feature].nunique() < 2:
            items.append({"feature": feature, "samples": len(pair), "status": "RESEARCH_ONLY", "rank_correlation": None})
            continue
        correlation = float(pair[feature].rank().corr(pair[outcome].rank()))
        items.append({
            "feature": feature, "samples": len(pair), "status": "OOS_REQUIRED",
            "rank_correlation": round(correlation, 4),
            "direction": "POSITIVE" if correlation > 0 else "NEGATIVE" if correlation < 0 else "NONE",
        })
    return {"outcome": outcome, "samples": int(pd.to_numeric(df[outcome], errors="coerce").notna().sum()), "items": items}


def build_trade_gate_readiness_report(df: pd.DataFrame, min_samples: int = 30) -> Dict[str, Any]:
    """Validate trade-gate v2 against the agreed pre-production criteria.

    检查项（用户确认的上线门槛）：
      1. 每个市场状态至少 N 个成熟 TRADE 5日样本
      2. TRADE 盈利因子 > 1.2
      3. TRADE 5日平均收益高于 OBSERVE
      4. 各评分维度与未来5日收益秩相关为正
      5. 按政策版本(v1/v2)分组对照
    """
    if df.empty or "ret_5d" not in df.columns:
        return {
            "policy_version": TRADE_GATE_POLICY_VERSION,
            "gates_ready": False,
            "checks": [],
            "notes": ["暂无成熟扫描样本，v2 需先积累样本再验证"],
        }
    version_col = df.get("trade_gate_policy_version", pd.Series("v1", index=df.index)).fillna("v1").astype(str)
    checks: List[Dict[str, Any]] = []

    for version in sorted(version_col.unique()):
        version_df = _independent_event_frame(df[version_col == version])
        trade = version_df[
            version_df.get("trade_bucket", pd.Series("", index=version_df.index)).astype(str).str.upper().eq("TRADE")
            & version_df.get("trade_eligible", pd.Series(False, index=version_df.index)).fillna(False).astype(bool)
        ]
        observe = version_df[
            version_df.get("trade_bucket", pd.Series("", index=version_df.index)).astype(str).str.upper().eq("OBSERVE")
        ]

        # 1) 每个市场状态的成熟 TRADE 样本量
        regime_col = version_df.get("market_regime", pd.Series("UNKNOWN", index=version_df.index)).astype(str)
        mature_col = trade.get("mature_5d", pd.Series(False, index=trade.index)).fillna(False).astype(bool)
        regime_col_trade = regime_col.reindex(trade.index, fill_value="UNKNOWN")
        required_regimes = ("CRITICAL", "DEFENSIVE", "OFFENSIVE")
        per_regime = {
            regime: int(((regime_col_trade == regime) & mature_col).sum())
            for regime in required_regimes
        }
        min_regime = min(per_regime.values()) if per_regime else 0
        checks.append({
            "version": version, "name": "regime_trade_samples",
            "label": f"每个市场状态成熟TRADE样本≥{min_samples}",
            "status": "PASS" if min_regime >= min_samples else "FAIL",
            "detail": {"per_regime_mature_5d": per_regime},
        })

        # 2) TRADE 盈利因子
        trade_metrics = _metric_summary(trade["ret_5d"])
        pf = trade_metrics.get("profit_factor")
        checks.append({
            "version": version, "name": "trade_profit_factor",
            "label": f"TRADE 盈利因子>{TRADE_GATE_MIN_PROFIT_FACTOR}",
            "status": "PASS" if pf is not None and pf > TRADE_GATE_MIN_PROFIT_FACTOR else "FAIL",
            "detail": trade_metrics,
        })

        # 3) TRADE 收益梯度高于 OBSERVE
        observe_metrics = _metric_summary(observe["ret_5d"])
        trade_avg = trade_metrics.get("avg_return")
        observe_avg = observe_metrics.get("avg_return")
        gradient_pass = (
            trade_avg is not None and observe_avg is not None and trade_avg > observe_avg
        )
        checks.append({
            "version": version, "name": "trade_beats_observe",
            "label": "TRADE 5日平均收益高于OBSERVE",
            "status": "PASS" if gradient_pass else "FAIL",
            "detail": {"trade_5d": trade_metrics, "observe_5d": observe_metrics},
        })

        # 4) 评分与未来收益秩相关为正
        correlations = {}
        for feature in ("sop_quality_score", "trade_opportunity_score", "final_trade_score"):
            if feature not in version_df.columns:
                continue
            pair = version_df[[feature, "ret_5d"]].apply(pd.to_numeric, errors="coerce").dropna()
            if len(pair) < min_samples:
                correlations[feature] = {"samples": int(len(pair)), "rank_correlation": None}
                continue
            correlations[feature] = {
                "samples": int(len(pair)),
                "rank_correlation": round(float(pair[feature].rank().corr(pair["ret_5d"].rank())), 4),
            }
        corr_pass = all(
            item.get("rank_correlation") is not None
            and item["rank_correlation"] > TRADE_GATE_MIN_SCORE_CORRELATION
            for item in correlations.values()
        ) and bool(correlations)
        checks.append({
            "version": version, "name": "score_return_correlation",
            "label": "评分与未来5日收益秩相关为正",
            "status": "PASS" if corr_pass else "FAIL",
            "detail": correlations,
        })

    v2_checks = [item for item in checks if item["version"] == TRADE_GATE_POLICY_VERSION]
    notes = [
        "v1 行为基线对照组；gates_ready 只由当前v2检查项决定。",
        "初期 v2 样本不足属预期：门槛的目的就是先积累再放行。",
        "样本外/滑点/手续费口径见 /api/review/strategy-calibration-report?executable=true。",
    ]
    if not v2_checks:
        notes.append("当前v2策略版本暂无样本")
    return {
        "policy_version": TRADE_GATE_POLICY_VERSION,
        "min_samples_per_regime": min_samples,
        "min_profit_factor": TRADE_GATE_MIN_PROFIT_FACTOR,
        "gates_ready": bool(v2_checks) and all(item["status"] == "PASS" for item in v2_checks),
        "checks": checks,
        "notes": notes,
    }
