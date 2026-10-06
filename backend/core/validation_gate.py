"""阈值晋升硬前置（promotion gate）。

背景（量化纪律审查 2026-09-30）：本库纪律是"阈值必须经本库点内样本分层验证
后才能进策略"，但此前只是文档约定——同一份历史被反复迭代调参（v1→v4）而无
样本外强制门，造成 6 个未经本库验证的活阈值（见 risk_constants 验证状态表）。

本模块把"晋升"做成代码判断：任何 SHADOW/advisory 阈值要转为影响仓位或交易
资格的行为，必须通过 walk-forward 证据 + （如适用）trade-gate readiness。
新增阈值上线流程：先 SHADOW 积累 → 调 promotion_allowed() → PASS 才允许改
行为常量。纯函数、无副作用，便于在报告与测试中断言。
"""
from typing import Any, Dict, List, Optional, Tuple

from core.risk_constants import (
    WALK_FORWARD_MIN_POSITIVE_WINDOWS,
    WALK_FORWARD_MIN_SAMPLES_PER_WINDOW,
)

# 需要晋升审批的特性注册表：feature -> 上线门槛描述（新增条目时补充）
PROMOTION_REGISTRY: Dict[str, str] = {
    "a_eod_controlled_trial": "E3 前推走查：≥3 个滚动样本外正期望窗口（当前 SHADOW）",
    "trade_gate_v2_regime_multiplier": "trade-gate readiness gates_ready 全 PASS（每 regime ≥30 成熟样本、PF>1.2）",
    "sector_fund_outflow_blocker": "blocker 对照：hit/miss 分层 5 日前瞻差异显著且方向一致",
    "amp20_gate": "滚动前推：6% 分界各档样本量与 Wilson 下界披露",
    "trend_phase_gate": "同上（道氏阶段分界滚动前推）",
    # 历史证据：regime_attribution 2026-09-30（132,681 事件三段 walk-forward），
    # 拦截交易三段均为负（train -1.72/validation -2.34/test -2.44）；
    # 转正另需 SHADOW 实盘期 ≥3 个月方向一致 + 退潮型月份（ZT_EBB）覆盖评估
    "market_state_gate": "R3 十日动量闸门：历史三段已过；SHADOW 期逐日状态对照 ≥3 个月方向一致",
    "signal_tier_weight": "双确认加权（机会分排序，v1-shadow）：SHADOW 期 ≥3 个月 A/B 层实现收益差方向一致",
}


def walk_forward_evidence(windows: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """评估滚动样本外窗口证据。

    windows: [{label, samples, avg_return}, ...]（每窗口样本外收益摘要）。
    PASS 条件：窗口数 ≥ WALK_FORWARD_MIN_POSITIVE_WINDOWS 且每个窗口
    samples ≥ WALK_FORWARD_MIN_SAMPLES_PER_WINDOW 且 avg_return > 0。
    """
    result: Dict[str, Any] = {
        "windows": [],
        "positive_windows": 0,
        "status": "FAIL",
        "reason": "no_windows",
    }
    windows = list(windows or [])
    if not windows:
        return result
    positive = 0
    for window in windows:
        samples = window.get("samples")
        avg_return = window.get("avg_return")
        ok = (
            isinstance(samples, (int, float)) and samples >= WALK_FORWARD_MIN_SAMPLES_PER_WINDOW
            and isinstance(avg_return, (int, float)) and avg_return > 0
        )
        positive += int(bool(ok))
        result["windows"].append({
            "label": window.get("label"),
            "samples": samples,
            "avg_return": avg_return,
            "positive": bool(ok),
        })
    result["positive_windows"] = positive
    if positive < WALK_FORWARD_MIN_POSITIVE_WINDOWS:
        result["reason"] = f"positive_windows {positive} < {WALK_FORWARD_MIN_POSITIVE_WINDOWS}"
        return result
    result["status"] = "PASS"
    result["reason"] = "ok"
    return result


def promotion_allowed(
    feature: str,
    *,
    gates_ready: Optional[bool] = None,
    walk_forward: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """晋升判定：feature 是否允许从 SHADOW/advisory 转为影响仓位/资格的行为。

    gates_ready：需要 trade-gate readiness 的特性（如 regime 乘数）传入；
    walk_forward：需要 E3 前推证据的特性传入窗口列表。任一必需证据缺失即
    拒绝——fail-closed，宁可不启用也不带病上线。
    """
    requirement = PROMOTION_REGISTRY.get(feature, "未注册特性：先在 PROMOTION_REGISTRY 登记上线门槛")
    detail: Dict[str, Any] = {"feature": feature, "requirement": requirement}
    wf = walk_forward_evidence(walk_forward)
    detail["walk_forward"] = wf
    needs_gates = feature == "trade_gate_v2_regime_multiplier"
    if needs_gates:
        detail["gates_ready"] = bool(gates_ready)
        if not gates_ready:
            detail["reason"] = "trade_gate_readiness_not_passed"
            return False, detail
    if wf["status"] != "PASS" and feature in {"a_eod_controlled_trial", "amp20_gate", "trend_phase_gate", "sector_fund_outflow_blocker"}:
        detail["reason"] = f"walk_forward_{wf['reason']}"
        return False, detail
    detail["reason"] = "approved"
    return True, detail
