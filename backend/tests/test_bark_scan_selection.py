from core.bark_scan_selection import run_bark_tv_observation_scan


def test_bark_scan_uses_tv_or_execution_pool(monkeypatch):
    calls = []

    def fake_scan(**kwargs):
        calls.append(kwargs)
        return [{"代码": "000001", "strategy_type": "tv_dual"}]

    monkeypatch.setattr("routers.scan.run_market_scan_task", fake_scan)

    results = run_bark_tv_observation_scan(
        local_only=False,
        require_live_snapshot=True,
    )

    assert calls == [{
        "strategy_type": "tv_dual",
        "local_only": False,
        "require_live_snapshot": True,
    }]
    assert results[0]["bark_selection_source_label"] == "TV均线或ZP执行池"
    assert results[0]["bark_scan_strategy_label"] == "TV均线或ZP执行池"
