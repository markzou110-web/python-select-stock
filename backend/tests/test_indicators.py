from datetime import date

import pandas as pd

from core.indicators import calculate_indicators


def test_calculate_indicators_rs_accepts_mixed_date_types():
    stock_df = pd.DataFrame({
        "日期": [date(2026, 5, 27), date(2026, 5, 28), date(2026, 5, 29)],
        "开盘": [10.0, 10.2, 10.4],
        "收盘": [10.1, 10.3, 10.6],
        "最高": [10.2, 10.4, 10.8],
        "最低": [9.9, 10.1, 10.3],
        "成交量": [1000, 1100, 1200],
    })
    bench_df = pd.DataFrame({
        "日期": ["2026-05-27", "2026-05-28", "2026-05-29"],
        "收盘": [3000.0, 3010.0, 3025.0],
    })

    result = calculate_indicators(stock_df, bench_df=bench_df)

    assert "RS" in result.columns
    assert result["RS"].notna().all()
