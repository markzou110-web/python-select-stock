"""Tests for 买入时点周报（entry_timing_report）.

验证两种 entry_mode（signal_close 尾盘买 / next_open_confirm 次日买）的
对比报告生成逻辑，以及 celery 任务调度与 bark 推送。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import entry_timing_report as etr


def _fake_summary(signal_count, win_rate, avg_return, profit_factor, pl_pcts):
    """构造一个假的 backtest summary + trades pl_pcts。"""
    return {
        "signal_count": signal_count,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "profit_factor": profit_factor,
        "trade_count": len(pl_pcts),
        "pl_pcts": pl_pcts,
    }


def test_build_report_body_formats_aggregate_and_top5():
    """_build_report_body 应输出整体对比 + Top5 个股 + 偏好分布。"""
    aggregate = {
        "signal_close": {"label": "尾盘买", "total_signals": 30, "win_rate": 50.0,
                         "avg_return": 1.5, "profit_factor": 1.8},
        "next_open_confirm": {"label": "次日买", "total_signals": 28, "win_rate": 45.0,
                              "avg_return": 0.8, "profit_factor": 1.5},
    }
    per_stock = [
        {"code": "600460", "strategy_type": "pine", "winner": "尾盘买",
         "close": {"signal_count": 15, "win_rate": 73.3, "avg_return": 1.55, "profit_factor": 2.0},
         "open": {"signal_count": 14, "win_rate": 64.3, "avg_return": 2.9, "profit_factor": 1.5}},
        {"code": "601138", "strategy_type": "pine", "winner": "次日买",
         "close": {"signal_count": 15, "win_rate": 46.7, "avg_return": 4.05, "profit_factor": 1.2},
         "open": {"signal_count": 14, "win_rate": 50.0, "avg_return": 6.39, "profit_factor": 1.6}},
    ]
    body = etr._build_report_body(aggregate, per_stock)

    assert "买入时点周报" in body
    assert "尾盘买：胜率 50.0%" in body
    assert "次日买：胜率 45.0%" in body
    assert "尾盘买入更优" in body  # close avg 1.5 > open avg 0.8
    assert "600460" in body and "601138" in body
    assert "个股偏好：尾盘更优 1 | 次日更优 1" in body


def test_build_report_body_handles_empty_per_stock():
    """无有效样本时输出提示，不崩。"""
    aggregate = {
        "signal_close": {"label": "尾盘买", "total_signals": 0, "win_rate": 0,
                         "avg_return": 0, "profit_factor": 0},
        "next_open_confirm": {"label": "次日买", "total_signals": 0, "win_rate": 0,
                              "avg_return": 0, "profit_factor": 0},
    }
    body = etr._build_report_body(aggregate, [])
    assert "无有效回测样本" in body


def test_build_report_body_declares_tie_when_diff_small():
    """两种策略 avg_return 差距 <0.5% 时判"持平"。"""
    aggregate = {
        "signal_close": {"total_signals": 10, "win_rate": 50, "avg_return": 1.0, "profit_factor": 1},
        "next_open_confirm": {"total_signals": 10, "win_rate": 48, "avg_return": 0.8, "profit_factor": 1},
    }
    per_stock = [{"code": "000001", "strategy_type": "pine", "winner": "持平",
                  "close": {"signal_count": 5, "win_rate": 50, "avg_return": 1.0, "profit_factor": 1},
                  "open": {"signal_count": 5, "win_rate": 48, "avg_return": 0.8, "profit_factor": 1}}]
    body = etr._build_report_body(aggregate, per_stock)
    # diff = |1.0 - 0.8| = 0.2 < 0.5 → 持平
    assert "两种策略持平" in body


def test_build_entry_timing_report_with_mocked_pipeline(monkeypatch):
    """全流程 mock：universe → prepare_df → backtest → body。"""
    fake_universe = [{"code": "600460", "strategy_type": "pine"}]
    monkeypatch.setattr(etr, "_load_universe", lambda engine, days=30, limit=50: fake_universe)
    monkeypatch.setattr(etr, "_prepare_df", lambda code, strat, engine, lookback_days=500: pd.DataFrame())
    monkeypatch.setattr(etr, "_backtest_both_arms", lambda df, strat, params: {
        "signal_close": _fake_summary(15, 73.3, 1.55, 2.0, [1.5, -2, 3, 4, -1]),
        "next_open_confirm": _fake_summary(14, 64.3, 2.9, 1.5, [2.9, -1, 5, 1, -2]),
    })
    monkeypatch.setattr(etr, "get_db_engine", lambda: object())  # 假 engine

    report = etr.build_entry_timing_report(days=30, max_codes=10)

    assert report["meta"]["effective_size"] == 1
    # 聚合胜率从 pl_pcts 重算（[1.5,-2,3,4,-1] → 3/5 胜 = 60%），不是用 mock 的 73.3
    assert report["aggregate"]["signal_close"]["win_rate"] == 60.0
    assert report["aggregate"]["signal_close"]["total_signals"] == 5
    assert "买入时点周报" in report["body"]
    assert "600460" in report["body"]


def test_build_entry_timing_report_skips_zero_signal_stocks(monkeypatch):
    """无信号的票应被跳过（不计入 effective）。"""
    fake_universe = [{"code": "000001", "strategy_type": "pine"}]
    monkeypatch.setattr(etr, "_load_universe", lambda engine, days=30, limit=50: fake_universe)
    monkeypatch.setattr(etr, "_prepare_df", lambda code, strat, engine, lookback_days=500: pd.DataFrame())
    monkeypatch.setattr(etr, "_backtest_both_arms", lambda df, strat, params: {
        "signal_close": _fake_summary(0, 0, 0, 0, []),
        "next_open_confirm": _fake_summary(0, 0, 0, 0, []),
    })
    monkeypatch.setattr(etr, "get_db_engine", lambda: object())

    report = etr.build_entry_timing_report(days=30, max_codes=10)

    assert report["meta"]["effective_size"] == 0
    assert "无有效回测样本" in report["body"]


def test_weekly_entry_timing_report_task_sends_bark(monkeypatch):
    """celery 任务应在有样本时推送 bark，无样本时跳过。"""
    from core import tasks

    fake_report = {
        "meta": {"effective_size": 5, "days": 30, "generated_at": "2026-06-22"},
        "aggregate": {},
        "body": "买入时点周报 | 2026-06-22\n样本：5 只票",
    }
    monkeypatch.setattr("core.entry_timing_report.build_entry_timing_report",
                        lambda days=30, max_codes=50: fake_report)

    sent = []
    async def fake_send(title, body, **kwargs):
        sent.append({"title": title, "body": body, **kwargs})
        return {"bark": True}
    monkeypatch.setattr(tasks.notifier, "send", fake_send)

    result = tasks.weekly_entry_timing_report()

    assert result["bark"] is True
    assert len(sent) == 1
    assert "买入时点周报" in sent[0]["title"]
    assert "样本：5 只票" in sent[0]["body"]


def test_weekly_entry_timing_report_task_skips_when_no_samples(monkeypatch):
    """无有效样本时任务应静默跳过，不发 bark。"""
    from core import tasks

    fake_report = {
        "meta": {"effective_size": 0},
        "body": "买入时点周报：本周无有效回测样本",
    }
    monkeypatch.setattr("core.entry_timing_report.build_entry_timing_report",
                        lambda days=30, max_codes=50: fake_report)

    sent = []
    async def fake_send(*a, **k):
        sent.append(a)
        return {"bark": True}
    monkeypatch.setattr(tasks.notifier, "send", fake_send)

    result = tasks.weekly_entry_timing_report()

    assert result["bark"] is False
    assert result["reason"] == "no_effective_samples"
    assert len(sent) == 0  # 没发推送
