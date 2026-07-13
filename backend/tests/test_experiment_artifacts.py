import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.experiment_artifacts import get_experiment, list_experiments, record_backtest_experiment
from core.models import Base


def _frame():
    return pd.DataFrame([{
        "code": "000001", "日期": "2026-01-05", "开盘": 10.0, "最高": 10.5,
        "最低": 9.8, "收盘": 10.2, "成交量": 10000,
    }])


def test_experiment_is_content_addressed_and_immutable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = {"code": "000001", "strategy_type": "squeeze", "start_date": "2026-01-01"}
    result = {"summary": {"signal_count": 3, "total_return": 2.5}}

    first = record_backtest_experiment(engine, "single", request, result, data_frame=_frame())
    duplicate = record_backtest_experiment(engine, "single", request, result, data_frame=_frame())

    assert first["experiment_id"] == duplicate["experiment_id"]
    assert first["created"] is True
    assert duplicate["created"] is False
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM backtest_experiments")).scalar() == 1


def test_changed_result_creates_new_artifact_and_can_be_read():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    request = {"code": "000001", "strategy_type": "squeeze"}
    first = record_backtest_experiment(engine, "single", request, {"summary": {"total_return": 1}}, data_frame=_frame())
    second = record_backtest_experiment(engine, "single", request, {"summary": {"total_return": 2}}, data_frame=_frame())

    assert first["experiment_id"] != second["experiment_id"]
    assert len(list_experiments(engine)) == 2
    artifact = get_experiment(engine, first["experiment_id"])
    assert artifact["content_hash"] == first["content_hash"]
    assert artifact["data_hash"] == first["data_hash"]


def test_request_artifact_uses_allowlist_and_drops_unknown_secret_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    saved = record_backtest_experiment(
        engine, "single", {"code": "000001", "strategy_type": "squeeze", "api_token": "must-not-persist"},
        {"summary": {}}, data_frame=_frame(),
    )
    artifact = get_experiment(engine, saved["experiment_id"])
    assert "must-not-persist" not in str(artifact["request_payload"])
    assert "api_token" not in str(artifact["request_payload"])
