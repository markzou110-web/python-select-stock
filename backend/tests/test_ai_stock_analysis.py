from core.ai_stock_analysis import analyze_single_stock, analyze_strategy_candidates
from core.config import config


def _candidate(**overrides):
    item = {
        "代码": "000001",
        "名称": "平安银行",
        "行业": "银行",
        "现价": 12.3,
        "strategy_type": "tv_dual_strict",
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "trade_blockers": [],
        "pa_entry_price": 12.5,
        "pa_stop_price": 11.4,
        "pa_target_price": 14.5,
        "display_signal_score": 78,
        "display_quality_score": 76,
        "display_opportunity_score": 72,
        "as_of": "2026-09-12T14:30:00+08:00",
    }
    item.update(overrides)
    return item


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": (
                        '{"market_summary":"候选偏强但仍需确认",'
                        '"analyses":[{"code":"000001","action":"BUY",'
                        '"confidence":82,"summary":"可进入人工复核",'
                        '"positive_factors":["策略共振"],"risk_factors":["波动"],'
                        '"data_limitations":[]}]}'
                    )
                }
            }],
            "usage": {"total_tokens": 321},
        }


class _TruncatedResponse(_Response):
    def json(self):
        return {
            "choices": [{
                "message": {"content": '{"market_summary":"输出被截断"'},
                "finish_reason": "length",
            }],
            "usage": {
                "completion_tokens": 2400,
                "completion_tokens_details": {"reasoning_tokens": 2200},
                "total_tokens": 3000,
            },
        }


def _configure(monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(config, "AI_MODEL", "test-model")
    monkeypatch.setattr(config, "AI_MAX_CANDIDATES", 10)


def test_ai_review_returns_structured_result(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr("core.ai_stock_analysis.requests.post", lambda *args, **kwargs: _Response())

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    assert result["analyses"][0]["action"] == "BUY"
    assert result["analyses"][0]["system_levels"] == {
        "entry_price": 12.5,
        "stop_price": 11.4,
        "target_price": 14.5,
    }
    assert result["usage"]["total_tokens"] == 321


def test_empty_ai_analyses_falls_back_with_warning(monkeypatch, caplog):
    _configure(monkeypatch)

    class _EmptyAnalysesResponse(_Response):
        def json(self):
            return {
                "choices": [{
                    "message": {"content": '{"market_summary":"","analyses":[]}'},
                }],
                "usage": {"total_tokens": 100},
            }

    monkeypatch.setattr(
        "core.ai_stock_analysis.requests.post",
        lambda *args, **kwargs: _EmptyAnalysesResponse(),
    )

    with caplog.at_level("WARNING", logger="alphavision"):
        result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    assert result["analyses"][0]["action"] == "WAIT"
    assert result["analyses"][0]["summary"] == "AI未提供有效结论"
    assert "AI响应未包含该股结论" in result["analyses"][0]["data_limitations"]
    assert any("no per-stock analyses" in message for message in caplog.messages)


def test_ai_review_exposes_strategy_and_ai_ranks(monkeypatch):
    _configure(monkeypatch)

    class _RankedResponse(_Response):
        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"market_summary":"二号更强", "analyses":['
                            '{"code":"000001","action":"WAIT","confidence":60,'
                            '"summary":"继续观察","positive_factors":[],"risk_factors":[],'
                            '"data_limitations":[]},'
                            '{"code":"000002","action":"BUY","confidence":88,'
                            '"summary":"进入复核","positive_factors":["量价确认"],'
                            '"risk_factors":[],"data_limitations":[]}]} '
                        )
                    }
                }],
                "usage": {"total_tokens": 400},
            }

    monkeypatch.setattr(
        "core.ai_stock_analysis.requests.post",
        lambda *args, **kwargs: _RankedResponse(),
    )

    result = analyze_strategy_candidates([
        _candidate(),
        _candidate(代码="000002", 名称="万科A"),
    ])

    assert [(item["code"], item["strategy_rank"], item["ai_rank"]) for item in result["analyses"]] == [
        ("000002", 2, 1),
        ("000001", 1, 2),
    ]


def test_glm_structured_review_disables_thinking(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(config, "AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setattr(config, "AI_MODEL", "glm-5.3")
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    assert captured["thinking"] == {"type": "disabled"}


def test_invalid_json_is_retried_with_more_output_room(monkeypatch):
    _configure(monkeypatch)
    payloads = []

    def fake_post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return _TruncatedResponse() if len(payloads) == 1 else _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    assert len(payloads) == 2
    assert payloads[1]["max_tokens"] > payloads[0]["max_tokens"]
    assert result["usage"]["attempts"] == 2


def test_ai_cannot_promote_blocked_candidate_to_buy(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr("core.ai_stock_analysis.requests.post", lambda *args, **kwargs: _Response())

    result = analyze_strategy_candidates([
        _candidate(trade_bucket="BLOCK", trade_eligible=False, trade_blockers=["跌破失效位"])
    ])

    review = result["analyses"][0]
    assert review["action"] == "AVOID"
    assert review["guardrail_adjusted"] is True


def test_unconfigured_ai_degrades_without_network(monkeypatch):
    monkeypatch.setattr(config, "AI_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setattr(config, "AI_MODEL", "")

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "disabled"
    assert result["analyses"] == []


def test_non_finite_numbers_are_not_sent(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)
    result = analyze_strategy_candidates([_candidate(现价=float("nan"))])

    assert result["status"] == "success"
    assert '"price":null' in captured["messages"][1]["content"]


def _research_snapshot():
    return {
        "as_of": "2026-09-11",
        "summary": {
            "label": "事件风险",
            "risk_flags": ["新闻/公告命中风险关键词：减持"],
        },
        "news": [{"title": "股东拟减持不超过2%股份"}],
        "announcements": [{"title": "关于股东减持计划的预披露公告"}],
    }


def test_cached_research_snapshot_is_sent_to_ai(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)
    monkeypatch.setattr(
        "core.ai_stock_analysis.get_cached_stock_research_signals",
        lambda code, trade_date=None: _research_snapshot(),
    )

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    content = captured["messages"][1]["content"]
    assert '"label":"事件风险"' in content
    assert "减持计划的预披露公告" in content
    assert "未实时核验" in result["analyses"][0]["data_limitations"][-1]


def test_research_cache_miss_keeps_plain_limitation(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)
    monkeypatch.setattr(
        "core.ai_stock_analysis.get_cached_stock_research_signals",
        lambda code, trade_date=None: None,
    )

    result = analyze_strategy_candidates([_candidate()])

    assert result["status"] == "success"
    assert '"research":null' in captured["messages"][1]["content"]
    assert "仅基于系统提供的结构化数据" in result["analyses"][0]["data_limitations"][-1]


class _SingleResponse:
    status_code = 200
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": (
                        '{"action":"BUY","confidence":76,"trend_view":"震荡 等待突破确认",'
                        '"summary":"结构完好，等待量能确认。","positive_factors":["板块联动强"],'
                        '"risk_factors":["短期涨幅大"],"key_levels":"入场12.5，跌破11.4离场。",'
                        '"catalysts":["减持计划预披露"],"data_limitations":[]}'
                    )
                }
            }],
            "usage": {"total_tokens": 210},
        }


def test_single_stock_analysis_uses_research_snapshot(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return _SingleResponse()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)

    result = analyze_single_stock(
        _candidate(),
        research_snapshot={
            "as_of": "2026-09-11",
            "summary": {"label": "事件风险", "risk_flags": ["新闻/公告命中风险关键词：减持"]},
            "news": [{"title": "股东拟减持不超过2%股份"}],
            "announcements": [],
        },
    )

    assert result["status"] == "success"
    content = captured["messages"][1]["content"]
    assert "减持" in content  # 研究快照已注入
    analysis = result["analysis"]
    assert analysis["action"] == "BUY"
    assert analysis["trend_view"].startswith("震荡")
    assert analysis["catalysts"] == ["减持计划预披露"]
    assert analysis["system_levels"]["entry_price"] == 12.5
    assert "未实时核验" in analysis["data_limitations"][-1]


def test_single_stock_guardrail_blocks_promoted_buy(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr("core.ai_stock_analysis.requests.post", lambda *args, **kwargs: _SingleResponse())

    result = analyze_single_stock(
        _candidate(trade_bucket="BLOCK", trade_eligible=False, trade_blockers=["跌破失效位"]),
    )

    analysis = result["analysis"]
    assert analysis["action"] == "AVOID"
    assert analysis["guardrail_adjusted"] is True


def test_single_stock_invalid_code_raises():
    try:
        analyze_single_stock({"代码": "bad", "名称": "无效"})
    except ValueError as exc:
        assert "股票代码" in str(exc)
    else:
        raise AssertionError("invalid code should raise ValueError")


def test_single_stock_degrades_without_network(monkeypatch):
    _configure(monkeypatch)

    def broken_post(*args, **kwargs):
        import requests as _requests
        raise _requests.ConnectionError("boom")

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", broken_post)

    result = analyze_single_stock(_candidate())

    assert result["status"] == "degraded"
    assert result["analysis"] is None


def test_candidate_payload_carries_industry_prosperity(monkeypatch):
    import json as _json

    _configure(monkeypatch)
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _Response()

    monkeypatch.setattr("core.ai_stock_analysis.requests.post", fake_post)

    candidate = _candidate(
        industry_prosperity={"label": "高景气", "roe_median": 12.0, "yoy_median": 25.0, "sample_count": 3},
    )
    analyze_strategy_candidates([candidate])

    user_content = _json.loads(captured["payload"]["messages"][1]["content"])
    sector = user_content["candidates"][0]["sector"]
    assert sector["industry_prosperity"]["label"] == "高景气"
    assert sector["industry_prosperity"]["roe_median"] == 12.0
    assert sector["industry_prosperity"]["sample_count"] == 3
