"""Evidence-gated lifecycle for shadow and production strategies."""
import json
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import text


STATES = ("DRAFT", "SHADOW", "CHALLENGER", "PAPER", "LIMITED_LIVE", "CHAMPION", "RETIRED")
ALLOWED = {
    "DRAFT": {"SHADOW", "RETIRED"}, "SHADOW": {"CHALLENGER", "RETIRED"},
    "CHALLENGER": {"PAPER", "SHADOW", "RETIRED"}, "PAPER": {"LIMITED_LIVE", "SHADOW", "RETIRED"},
    "LIMITED_LIVE": {"CHAMPION", "PAPER", "RETIRED"}, "CHAMPION": {"LIMITED_LIVE", "RETIRED"},
    "RETIRED": {"DRAFT"},
}
EVIDENCE_REQUIRED_STATES = {"PAPER", "LIMITED_LIVE", "CHAMPION"}


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _artifact_evidence(engine, target: str, evidence: Dict[str, Any]) -> tuple[Dict[str, Any] | None, str]:
    experiment_id = str(evidence.get("experiment_id") or "")
    if not experiment_id:
        return None, "缺少不可变experiment_id"
    with engine.connect() as conn:
        artifact = conn.execute(text("""
            SELECT experiment_id,experiment_type,strategy_type,code_version,data_hash,result_payload
            FROM backtest_experiments WHERE experiment_id=:id
        """), {"id": experiment_id}).mappings().first()
    if not artifact:
        return None, "实验工件不存在"
    allowed_types = {"walk_forward", "rolling_walk_forward"} if target == "PAPER" else {"rolling_walk_forward"}
    if artifact["experiment_type"] not in allowed_types:
        return None, f"{target}不接受{artifact['experiment_type']}实验"
    declared_strategy = str(evidence.get("strategy_type") or "")
    if not declared_strategy or declared_strategy != str(artifact["strategy_type"]):
        return None, "策略类型与实验工件不一致"
    result = _json_dict(artifact["result_payload"])
    summary = _json_dict(result.get("summary"))
    if artifact["experiment_type"] == "rolling_walk_forward":
        mature = int(summary.get("test_signals") or 0)
        expected = float(summary.get("oos_weighted_avg_return") or 0)
        factor = float(summary.get("oos_weighted_profit_factor") or 0)
    else:
        oos_rows = [_json_dict(item.get("out_of_sample")) for item in result.get("items", []) if isinstance(item, dict)]
        mature = sum(int(row.get("signal_count") or 0) for row in oos_rows)
        expected = (
            sum(float(row.get("avg_return") or 0) * int(row.get("signal_count") or 0) for row in oos_rows) / mature
            if mature else 0
        )
        factor = (
            sum(float(row.get("profit_factor") or 0) * int(row.get("signal_count") or 0) for row in oos_rows) / mature
            if mature else 0
        )
    return {
        "experiment_id": experiment_id, "strategy_type": artifact["strategy_type"],
        "experiment_type": artifact["experiment_type"], "code_version": artifact["code_version"],
        "data_hash": artifact["data_hash"], "mature_signals": mature,
        "avg_return": round(expected, 6), "profit_factor": round(factor, 6),
        "source": "immutable_experiment",
    }, "实验工件验证通过"


def _evidence_allows(target: str, evidence: Dict[str, Any]) -> tuple[bool, str]:
    mature = int(evidence.get("mature_signals") or 0)
    expected = float(evidence.get("avg_return") or 0)
    factor = float(evidence.get("profit_factor") or 0)
    if target in {"PAPER", "LIMITED_LIVE", "CHAMPION"} and mature < 30:
        return False, "成熟样本少于30"
    if target in {"LIMITED_LIVE", "CHAMPION"} and (expected <= 0 or factor <= 1):
        return False, "期望收益或盈亏因子未通过"
    return True, "证据门槛通过"


def transition_strategy(engine, strategy_key: str, target: str, evidence: Dict[str, Any], reason: str = "", version: str = "") -> Dict[str, Any]:
    target = target.upper()
    if target not in STATES:
        return {"changed": False, "error": "invalid_target_state"}
    with engine.begin() as conn:
        current = conn.execute(text("SELECT state FROM strategy_release_states WHERE strategy_key=:key"), {"key": strategy_key}).scalar() or "DRAFT"
        if target not in ALLOWED.get(current, set()):
            return {"changed": False, "state": current, "error": "transition_not_allowed"}
        verified_evidence = evidence
        if target in EVIDENCE_REQUIRED_STATES:
            verified_evidence, artifact_reason = _artifact_evidence(engine, target, evidence)
            if verified_evidence is None:
                return {"changed": False, "state": current, "error": "experiment_evidence_not_met", "reason": artifact_reason}
        allowed, evidence_reason = _evidence_allows(target, verified_evidence)
        if not allowed:
            return {"changed": False, "state": current, "error": "evidence_not_met", "reason": evidence_reason}
        evidence_value = ":evidence" if engine.dialect.name == "sqlite" else "CAST(:evidence AS JSON)"
        conn.execute(text(f"""
            INSERT INTO strategy_release_states(strategy_key,state,version,evidence,reason,updated_at)
            VALUES (:key,:state,:version,{evidence_value},:reason,:updated)
            ON CONFLICT(strategy_key) DO UPDATE SET state=EXCLUDED.state,version=EXCLUDED.version,
                evidence=EXCLUDED.evidence,reason=EXCLUDED.reason,updated_at=EXCLUDED.updated_at
        """), {"key": strategy_key, "state": target, "version": version, "evidence": json.dumps(verified_evidence, ensure_ascii=False), "reason": reason, "updated": datetime.now()})
    return {"changed": True, "previous_state": current, "state": target, "reason": evidence_reason, "evidence": verified_evidence}


def list_strategy_states(engine):
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text("SELECT strategy_key,state,version,evidence,reason,updated_at FROM strategy_release_states ORDER BY updated_at DESC")).mappings().all()]
