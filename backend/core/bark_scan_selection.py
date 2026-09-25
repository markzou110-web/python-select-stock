"""Select Bark candidates from the formal TV execution pool."""

from typing import Any, Dict

from core.db import get_setting


BARK_SCAN_STRATEGIES = {
    "tv_zp": {
        "label": "TV-ZP趋势信号",
        "description": "Range Filter 主导，Volume/QQE 确认",
    },
    "tv_dual": {
        "label": "TV 宽松观察池",
        "description": "均线B共振或 TV-ZP 趋势信号",
    },
    "tv_dual_strict": {
        "label": "TV+ 强确认精选",
        "description": "均线B共振且 TV-ZP 趋势信号",
    },
}
BARK_TV_OBSERVATION_STRATEGY = "tv_zp"
BARK_TV_OBSERVATION_LABEL = BARK_SCAN_STRATEGIES[BARK_TV_OBSERVATION_STRATEGY]["label"]


def normalize_bark_scan_strategy(value: Any) -> str:
    strategy = str(value or "").strip()
    return strategy if strategy in BARK_SCAN_STRATEGIES else BARK_TV_OBSERVATION_STRATEGY


def get_configured_bark_scan_strategy() -> str:
    return normalize_bark_scan_strategy(
        get_setting("bark_scan_strategy", BARK_TV_OBSERVATION_STRATEGY)
    )


def run_bark_tv_observation_scan(
    *,
    local_only: bool,
    require_live_snapshot: bool,
) -> list[Dict[str, Any]]:
    from routers.scan import run_market_scan_task

    strategy = get_configured_bark_scan_strategy()
    strategy_label = BARK_SCAN_STRATEGIES[strategy]["label"]
    results = run_market_scan_task(
        strategy_type=strategy,
        local_only=local_only,
        require_live_snapshot=require_live_snapshot,
    ) or []
    annotated = []
    for source in results:
        candidate = dict(source)
        candidate["bark_selection_source_label"] = strategy_label
        candidate["bark_scan_strategy_label"] = strategy_label
        annotated.append(candidate)
    return annotated
