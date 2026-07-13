import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.decision_layer import _risk_normalize_position
from core.execution_audit import classify_trade_blockers


def test_blocker_classification_is_audit_only_and_preserves_text():
    blockers = ["回踩结构失效", "未站上确认价，等待突破确认", "市场退潮，暂停新增仓位"]
    groups = classify_trade_blockers(blockers)
    assert groups["hard"] == ["回踩结构失效"]
    assert groups["wait"] == ["未站上确认价，等待突破确认"]
    assert groups["soft"] == ["市场退潮，暂停新增仓位"]


def test_position_is_capped_by_stop_distance_risk_budget():
    position = {"initial_position_pct": 15, "max_position_pct": 20, "label": "重点仓"}
    result = _risk_normalize_position(position, {"pa_risk_pct": 10})
    assert result["initial_position_pct"] == 10
    assert result["max_position_pct"] == 10
    assert result["estimated_initial_risk_pct"] == 1
    assert result["risk_capped"] is True


def test_position_without_stop_distance_is_not_silently_resized():
    position = {"initial_position_pct": 10, "max_position_pct": 15, "label": "标准仓"}
    result = _risk_normalize_position(position, {})
    assert result["initial_position_pct"] == 10
    assert result["stop_risk_pct"] is None
