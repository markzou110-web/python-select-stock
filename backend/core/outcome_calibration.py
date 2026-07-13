import ast
import json
from typing import Any, Dict, Iterable, List

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine


HORIZONS = (1, 3, 5, 10)


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
                   sop_vetoes, price_action_detail, scanned_at
            FROM ranked_signals WHERE row_num = 1
        )
        SELECT s.*, d0.close AS signal_close,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 0) AS close_1d,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 2) AS close_3d,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 4) AS close_5d,
               (SELECT close FROM daily_k d WHERE d.code=s.code AND d.date>s.signal_date ORDER BY d.date LIMIT 1 OFFSET 9) AS close_10d
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
            h10.close AS close_10d
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
            SELECT close FROM daily_k d
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

    blockers: List[List[str]] = []
    for _, row in df.iterrows():
        detail = _detail_dict(row.get("price_action_detail"))
        for key in (
            "grade_stage", "decision_lifecycle_state", "confirmation_event_state",
            "early_value_transition_state", "sector_phase", "research_eligible",
            "trade_opportunity_score",
        ):
            value = detail.get(key)
            df.loc[row.name, key] = "UNKNOWN" if value is None else value
        values: Iterable[str] = (
            _as_list(detail.get("trade_blockers"))
            + _as_list(row.get("sop_vetoes"))
            + _as_list(detail.get("sop_vetoes"))
        )
        blockers.append(list(dict.fromkeys(item for item in values if item)))
    df["blockers"] = blockers
    return df


def build_execution_cohort_report(df: pd.DataFrame) -> Dict[str, Any]:
    """Compare research, blocked, and executable cohorts on the same future-return basis."""
    if df.empty:
        return {"cohorts": [], "primary_horizon": "5d", "notes": ["暂无成熟扫描样本"]}
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
        "reason": "A级优于B级且B级优于C级" if passed else "Grade 与5日平均收益未形成单调关系",
    }


def build_calibration_report(df: pd.DataFrame, min_samples: int = 30) -> Dict[str, Any]:
    min_samples = max(1, int(min_samples))
    if df.empty:
        return {
            "summary": {"signals": 0, "mature_5d": 0, "mature_10d": 0},
            "horizons": {f"{horizon}d": _metric_summary(pd.Series(dtype=float)) for horizon in HORIZONS},
            "by_strategy": [],
            "by_grade": [],
            "by_trade_bucket": [],
            "by_market_regime": [],
            "by_score_model_version": [],
            "by_grade_stage": [],
            "by_lifecycle_state": [],
            "by_confirmation_event": [],
            "by_early_value_transition": [],
            "grade_monotonicity": _grade_monotonicity([], min_samples),
        }

    horizons = {f"{horizon}d": _metric_summary(df[f"ret_{horizon}d"]) for horizon in HORIZONS}
    by_grade = _group_rows(df, "sop_grade")
    signal_dates = pd.to_datetime(df.get("signal_date"), errors="coerce")
    report = {
        "summary": {
            "signals": int(len(df)),
            "mature_5d": horizons["5d"]["signals"],
            "mature_10d": horizons["10d"]["signals"],
            "latest_signal_date": str(signal_dates.max().date()) if signal_dates.notna().any() else None,
            "price_basis": "daily_k_signal_close",
            "maturity_rule": "未来第N个交易日收盘存在时才计入N日样本",
        },
        "horizons": horizons,
        "by_strategy": _group_rows(df, "strategy_type"),
        "by_grade": by_grade,
        "by_trade_bucket": _group_rows(df, "trade_bucket"),
        "by_market_regime": _group_rows(df, "market_regime"),
        "by_score_model_version": _group_rows(df, "score_model_version"),
        "by_grade_stage": _group_rows(df, "grade_stage"),
        "by_lifecycle_state": _group_rows(df, "decision_lifecycle_state"),
        "by_confirmation_event": _group_rows(df, "confirmation_event_state"),
        "by_early_value_transition": _group_rows(df, "early_value_transition_state"),
        "grade_monotonicity": _grade_monotonicity(by_grade, min_samples),
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

    normalized = df.copy()
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
