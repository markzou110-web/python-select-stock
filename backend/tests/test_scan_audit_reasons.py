import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.scanner import _normalize_rejection_reason, _summarize_rejection_reasons


def test_rejection_reason_normalization_removes_dynamic_values():
    first = _normalize_rejection_reason("距60日低点涨幅不在0%-12%区间(23.4%)")
    second = _normalize_rejection_reason("距60日低点涨幅不在0%-12%区间(58.5%)")

    assert first[0] == second[0] == "DISTANCE_FROM_60D_LOW_OUT_OF_RANGE"
    assert first[1] == second[1] == "距60日低点涨幅不在0%-12%区间"
    assert first[2] == "距60日低点涨幅不在0%-12%区间(23.4%)"


def test_rejection_reason_summary_aggregates_and_bounds_samples():
    raw = {
        "底部量能尚未收缩(1.01x)": 2,
        "底部量能尚未收缩(1.02x)": 3,
        "底部量能尚未收缩(1.03x)": 4,
        "底部量能尚未收缩(1.04x)": 5,
        "最近3日仍在创新低，暂按下跌中继处理": 7,
    }

    reasons, details = _summarize_rejection_reasons(raw)

    assert reasons == {
        "底部量能尚未收缩": 14,
        "最近3日仍在创新低，暂按下跌中继处理": 7,
    }
    assert details["total_rejections"] == 21
    assert details["unique_raw"] == 5
    assert details["unique_normalized"] == 2
    volume = next(item for item in details["reasons"] if item["reason_code"] == "BOTTOM_VOLUME_NOT_CONTRACTED")
    assert volume["count"] == 14
    assert len(volume["samples"]) == 3
