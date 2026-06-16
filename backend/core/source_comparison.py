from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List

from core.multi_source_sync import EastMoneyDataSource, SinaDataSource, TencentDataSource


def compare_history_sources(codes: List[str], lookback_days: int = 14) -> Dict[str, Any]:
    start_date = (datetime.now() - timedelta(days=max(7, lookback_days))).strftime("%Y%m%d")
    source_factories = {
        "Tencent": TencentDataSource,
        "Sina": SinaDataSource,
        "EastMoney": EastMoneyDataSource,
    }
    results: Dict[str, Dict[str, Any]] = {code: {} for code in codes}

    def fetch(code: str, source_name: str, factory):
        df = factory().get_hist_data(code, start_date)
        if df is None or df.empty:
            return code, source_name, {"status": "NO_DATA"}
        row = df.sort_values("日期").iloc[-1]
        return code, source_name, {
            "status": "OK",
            "date": str(row["日期"])[:10],
            "close": round(float(row["收盘"]), 3),
            "volume": round(float(row["成交量"]), 2),
        }

    with ThreadPoolExecutor(max_workers=min(6, len(codes) * len(source_factories))) as pool:
        futures = [
            pool.submit(fetch, code, source_name, factory)
            for code in codes
            for source_name, factory in source_factories.items()
        ]
        for future in as_completed(futures):
            try:
                code, source_name, value = future.result()
                results[code][source_name] = value
            except Exception as exc:
                results.setdefault("errors", {})[str(len(results.get("errors", {})))] = str(exc)[:160]

    items = []
    for code in codes:
        sources = results.get(code, {})
        valid = [value for value in sources.values() if value.get("status") == "OK"]
        closes = [value["close"] for value in valid]
        volumes = [value["volume"] for value in valid if value["volume"] > 0]
        close_spread = ((max(closes) - min(closes)) / min(closes) * 100) if len(closes) >= 2 and min(closes) > 0 else None
        volume_spread = ((max(volumes) - min(volumes)) / min(volumes) * 100) if len(volumes) >= 2 and min(volumes) > 0 else None
        items.append({
            "code": code,
            "sources": sources,
            "close_spread_pct": round(close_spread, 3) if close_spread is not None else None,
            "volume_spread_pct": round(volume_spread, 2) if volume_spread is not None else None,
            "status": "WARN" if close_spread is not None and close_spread > 1 else ("OK" if len(valid) >= 2 else "INSUFFICIENT"),
        })
    return {"items": items, "sample_count": len(items), "lookback_days": lookback_days}
