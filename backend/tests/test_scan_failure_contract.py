"""Scan task failures retain their details and optimization validates stock codes."""

import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest
from celery import Celery
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import scanner, strategy
from routers import scan


@pytest.mark.parametrize("status_code, detail", [
    (500, "扫描执行失败: name 'hist_days' is not defined"),
    (503, "数据库中没有足够的数据进行扫描"),
])
def test_scan_failure_survives_celery_json_roundtrip(monkeypatch, status_code, detail):
    def fail_scan(**kwargs):
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(scanner, "perform_market_scan", fail_scan)
    with pytest.raises(RuntimeError) as raised:
        scan.run_market_scan_task.run()

    app = Celery("scan-failure-test", broker="memory://", backend="cache+memory://")
    app.conf.result_serializer = "json"
    serialized = app.backend.prepare_exception(raised.value)
    decoded = app.backend.decode(app.backend.encode(serialized))
    restored = app.backend.exception_to_python(decoded)
    assert isinstance(restored, RuntimeError)
    assert str(restored) == f"{status_code}: {detail}"
    assert isinstance(raised.value.__cause__, HTTPException)

    monkeypatch.setattr(
        scan.celery_app, "AsyncResult",
        lambda task_id: SimpleNamespace(state="FAILURE", info=restored),
    )
    assert scan.get_scan_status("failed-scan") == {
        "task_id": "failed-scan",
        "status": "FAILURE",
        "message": f"{status_code}: {detail}",
    }
    app.close()


@pytest.fixture
def scan_client():
    app = FastAPI()
    app.include_router(scan.router)
    with TestClient(app) as client:
        yield client


def test_optimize_rejects_invalid_code_before_loading_data(monkeypatch, scan_client):
    def unexpected_db_access():
        pytest.fail("Invalid stock codes must be rejected before accessing the database")

    monkeypatch.setattr(scan, "get_db_engine", unexpected_db_access)
    response = scan_client.post("/api/scan/optimize", json={"code": "invalid"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid stock code"}


def test_optimize_accepts_valid_code_and_runs_grid(monkeypatch, scan_client):
    engine = object()
    history = pd.DataFrame({"收盘": [10.0, 11.0]})
    calls = {}
    monkeypatch.setattr(scan, "get_db_engine", lambda: engine)

    def load_history(code, start_date, db_engine):
        assert code == "000001"
        assert db_engine is engine
        return history

    def run_grid(df, **kwargs):
        assert df is history
        calls.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(scan, "load_from_db", load_history)
    monkeypatch.setattr(scan, "calculate_indicators", lambda df, **kwargs: df)
    monkeypatch.setattr(strategy, "run_optimization_grid", run_grid)
    response = scan_client.post("/api/scan/optimize", json={"code": "000001"})
    assert response.status_code == 200
    assert response.json() == {"results": []}
    assert calls["code"] == "000001"
    assert calls["strategy_type"] == "squeeze"
