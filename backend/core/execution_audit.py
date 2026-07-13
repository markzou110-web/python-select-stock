"""Execution-decision audit labels; classification does not alter stock selection."""
from typing import Any, Dict, Iterable, List


HARD_BLOCKER_MARKERS = (
    "回避", "结构失效", "结构不进入交易池", "冲高回落", "涨停/近涨停", "高开",
    "异常价格", "板块下跌", "禁止实盘", "关键点时字段不完整", "仅供研究",
)
WAIT_BLOCKER_MARKERS = (
    "确认价", "量能未确认", "等待", "次日确认", "回踩", "换手不足", "交易计划未确认",
)
SOFT_BLOCKER_MARKERS = (
    "降级观察", "市场退潮", "板块退潮", "策略近期负期望", "综合机会分<",
    "资金流出", "资金流数据缺失", "原始策略分<", "周线中性", "周线交易区间",
)


def classify_trade_blockers(blockers: Iterable[Any]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {"hard": [], "wait": [], "soft": [], "other": []}
    for raw in blockers or []:
        blocker = str(raw).strip()
        if not blocker:
            continue
        if any(marker in blocker for marker in HARD_BLOCKER_MARKERS):
            group = "hard"
        elif any(marker in blocker for marker in WAIT_BLOCKER_MARKERS):
            group = "wait"
        elif any(marker in blocker for marker in SOFT_BLOCKER_MARKERS):
            group = "soft"
        else:
            group = "other"
        groups[group].append(blocker)
    return {key: list(dict.fromkeys(values)) for key, values in groups.items()}
