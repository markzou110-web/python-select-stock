from core.bark_scan_selection import run_bark_tv_observation_scan


def test_bark_scan_uses_tv_zp_trend_pool_by_default(monkeypatch):
    calls = []

    def fake_scan(**kwargs):
        calls.append(kwargs)
        return [{"代码": "000001", "strategy_type": "tv_zp"}]

    monkeypatch.setattr("routers.scan.run_market_scan_task", fake_scan)
    monkeypatch.setattr("core.bark_scan_selection.get_setting", lambda key, default="": default)

    results = run_bark_tv_observation_scan(
        local_only=False,
        require_live_snapshot=True,
    )

    assert calls == [{
        "strategy_type": "tv_zp",
        "local_only": False,
        "require_live_snapshot": True,
    }]
    assert results[0]["bark_selection_source_label"] == "TV-ZP趋势信号"
    assert results[0]["bark_scan_strategy_label"] == "TV-ZP趋势信号"


def test_bark_scan_uses_strategy_selected_in_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(kwargs) or [{"代码": "000001"}],
    )
    monkeypatch.setattr(
        "core.bark_scan_selection.get_setting",
        lambda key, default="": "tv_dual_strict",
    )

    results = run_bark_tv_observation_scan(
        local_only=True,
        require_live_snapshot=False,
    )

    assert calls[0]["strategy_type"] == "tv_dual_strict"
    assert results[0]["bark_scan_strategy_label"] == "TV+ 强确认精选"


def test_invalid_saved_bark_strategy_falls_back_to_tv_zp(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "routers.scan.run_market_scan_task",
        lambda **kwargs: calls.append(kwargs) or [],
    )
    monkeypatch.setattr(
        "core.bark_scan_selection.get_setting",
        lambda key, default="": "unknown_strategy",
    )

    run_bark_tv_observation_scan(local_only=True, require_live_snapshot=False)

    assert calls[0]["strategy_type"] == "tv_zp"
