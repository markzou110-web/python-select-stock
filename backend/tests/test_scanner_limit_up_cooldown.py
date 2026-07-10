"""Tests for scanner 涨停冷静期增强（改动 #13 扫描端）。

回归：涨停/近涨停默认加 blocker（等待隔日确认，防追高）；但若 apply_limit_up_features
注入的 limit_up_status='BROKEN'（曾封板但已开板=抛压释放），不加 blocker 且标记
limit_up_unsealed，后续加分鼓励低吸。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import _apply_trade_execution_profile, _near_limit_pct


def _res(**overrides):
    """构造一个能通过基础检查的扫描结果（主板代码 000001，近涨停 = +9.8%）。"""
    near_limit = _near_limit_pct("000001")  # 主板 10% - 0.2 = 9.8
    item = {
        "代码": "000001",
        "名称": "测试票",
        "涨幅%": near_limit,  # 近涨停
        "Score": 80,
        "final_rank_score": 80,
        "strategy_type": "squeeze",
        "pa_trade_plan": {"action": "READY"},
        "pa_entry_price": 10.0,
        "pa_stop_price": 9.0,
        "pa_target_price": 12.0,
        "sop_grade": "B",
        "pct_5d": 5,
    }
    item.update(overrides)
    return item


def test_near_limit_adds_blocker_without_unsealed_status():
    """近涨停且无开板标记 → 加"涨停/近涨停"blocker（防追高）。"""
    res = _res()
    _apply_trade_execution_profile(res)
    blockers = res.get("trade_blockers", [])
    assert any("涨停" in b for b in blockers), f"近涨停应有 blocker，实际 {blockers}"
    assert not res.get("limit_up_unsealed"), "未开板不应标记 unsealed"


def test_broken_limit_up_removes_blocker_and_marks_unsealed():
    """改动 #13：limit_up_status='BROKEN'（曾封板但已开板）→ 不加涨停 blocker，标记 unsealed。

    封板失败=抛压释放，反而是低吸机会，不应被"等待隔日确认"挡掉。
    """
    res = _res(limit_up_status="BROKEN")
    _apply_trade_execution_profile(res)
    blockers = res.get("trade_blockers", [])
    assert not any("涨停" in b for b in blockers), f"开板票不应有涨停 blocker，实际 {blockers}"
    assert res.get("limit_up_unsealed") is True, "开板应标记 limit_up_unsealed"


def test_sealed_limit_up_keeps_blocker():
    """limit_up_status='SEALED'（封死涨停）→ 仍加 blocker（买不到，等待隔日）。"""
    res = _res(limit_up_status="SEALED")
    _apply_trade_execution_profile(res)
    blockers = res.get("trade_blockers", [])
    assert any("涨停" in b for b in blockers), f"封死涨停应有 blocker，实际 {blockers}"
    assert not res.get("limit_up_unsealed")


def test_unsealed_gets_score_bonus():
    """开板标记的票应获得加分（低吸机会），final_trade_score 高于被封板挡掉的票。"""
    res_broken = _res(limit_up_status="BROKEN")
    _apply_trade_execution_profile(res_broken)
    res_sealed = _res(limit_up_status="SEALED")
    _apply_trade_execution_profile(res_sealed)
    # 开板加分 +4，封板扣分 → 开板分数应更高
    assert res_broken["final_trade_score"] > res_sealed["final_trade_score"], (
        f"开板票应加分高于封板票，broken={res_broken['final_trade_score']} "
        f"sealed={res_sealed['final_trade_score']}"
    )
