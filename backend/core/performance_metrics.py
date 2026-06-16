import math
from typing import Any, Dict

import pandas as pd


def return_metrics(values: pd.Series) -> Dict[str, Any]:
    returns = pd.to_numeric(values, errors="coerce").dropna()
    if returns.empty:
        return {
            "signals": 0, "win_rate": 0, "avg_return": 0, "avg_win": 0,
            "avg_loss": 0, "profit_loss_ratio": 0, "expected_return": 0,
            "ci95_low": 0, "ci95_high": 0,
        }

    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    avg_win = float(wins.mean()) if not wins.empty else 0.0
    avg_loss = float(losses.mean()) if not losses.empty else 0.0
    win_rate = float((returns > 0).mean())
    expected = win_rate * avg_win + (1 - win_rate) * avg_loss
    stderr = float(returns.std(ddof=1) / math.sqrt(len(returns))) if len(returns) > 1 else 0.0
    ratio = avg_win / abs(avg_loss) if avg_loss < 0 else 0.0
    return {
        "signals": int(len(returns)),
        "win_rate": round(win_rate * 100, 1),
        "avg_return": round(float(returns.mean()), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_loss_ratio": round(ratio, 2),
        "expected_return": round(expected, 2),
        "ci95_low": round(float(returns.mean()) - 1.96 * stderr, 2),
        "ci95_high": round(float(returns.mean()) + 1.96 * stderr, 2),
    }
