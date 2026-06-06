import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import _apply_money_flow_to_results, _money_flow_label


def test_money_flow_label_formats_inflow_and_outflow():
    assert _money_flow_label({"main_net_inflow_yi": 1.23}) == "主力流入+1.23亿"
    assert _money_flow_label({"main_net_inflow_yi": -0.8}) == "主力流出-0.80亿"
    assert _money_flow_label({"main_net_inflow_yi": 0.07, "flow_metric": "net_inflow"}) == "资金净流入+0.07亿"
    assert _money_flow_label({"main_net_inflow_yi": -0.07, "flow_metric": "net_inflow"}) == "资金净流出-0.07亿"
    assert _money_flow_label({"main_net_inflow_yi": 0}) == "资金中性"


def test_apply_money_flow_to_results_updates_scan_rows():
    results = [{"代码": "1", "名称": "平安银行"}, {"代码": "000002", "名称": "万科A"}]
    flow_map = {
        "000001": {
            "main_net_inflow_yi": 2.5,
            "main_net_ratio": 7.1,
            "pct": 1.2,
            "source": "eastmoney_akshare",
            "flow_metric": "main_net_inflow",
            "metric_label": "主力净流入",
        }
    }

    _apply_money_flow_to_results(results, flow_map)

    assert results[0]["北向"] == "主力流入+2.50亿"
    assert results[0]["money_flow"]["main_net_ratio"] == 7.1
    assert results[0]["money_flow"]["source"] == "eastmoney_akshare"
    assert results[0]["money_flow"]["metric_label"] == "主力净流入"
    assert results[1]["北向"] == "---"
