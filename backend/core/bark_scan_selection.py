"""Select Bark candidates from the formal TV execution pool."""

from typing import Any, Dict
from core.risk_constants import PRIMARY_TV_STRATEGY


BARK_TV_OBSERVATION_STRATEGY = PRIMARY_TV_STRATEGY
BARK_TV_OBSERVATION_LABEL = "TV均线或ZP执行池"


def run_bark_tv_observation_scan(
    *,
    local_only: bool,
    require_live_snapshot: bool,
) -> list[Dict[str, Any]]:
    from routers.scan import run_market_scan_task

    results = run_market_scan_task(
        strategy_type=BARK_TV_OBSERVATION_STRATEGY,
        local_only=local_only,
        require_live_snapshot=require_live_snapshot,
    ) or []
    annotated = []
    for source in results:
        candidate = dict(source)
        candidate["bark_selection_source_label"] = BARK_TV_OBSERVATION_LABEL
        candidate["bark_scan_strategy_label"] = BARK_TV_OBSERVATION_LABEL
        annotated.append(candidate)
    return annotated
