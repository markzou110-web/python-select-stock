"""按行业聚合当日候选池的基本面景气度（ROE / 净利同比）。

只做聚合与标注，供逻辑链展示与 AI 复核参考，不参与风控判定。
样本过少的行业不输出，避免单票噪音被当成行业景气。
"""
from typing import Any, Dict, Iterable, List, Tuple

PROSPERITY_SAMPLE_MIN = 2
_ROE_SCORE_PER_PCT = 4.0  # ROE 中位 15% → 60分
_ROE_SCORE_MAX = 60.0
_YOY_SCORE_PER_PCT = 1.0  # 净利同比中位 40% → 40分
_YOY_SCORE_MAX = 40.0
_HIGH_PROSPERITY_SCORE = 70.0
_WEAK_PROSPERITY_SCORE = 50.0


def build_industry_prosperity(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Tuple[float, float]]] = {}
    for row in rows or []:
        industry = str(row.get("行业") or row.get("industry") or "").strip()
        if not industry:
            continue
        try:
            roe = float(row.get("ROE") or row.get("roe") or 0)
            yoy = float(row.get("净利YOY") or row.get("net_profit_yoy") or 0)
        except (TypeError, ValueError):
            continue
        groups.setdefault(industry, []).append((roe, yoy))

    result: Dict[str, Dict[str, Any]] = {}
    for industry, values in groups.items():
        if len(values) < PROSPERITY_SAMPLE_MIN:
            continue
        roes = sorted(value[0] for value in values)
        yoys = sorted(value[1] for value in values)
        roe_median = roes[len(roes) // 2]
        yoy_median = yoys[len(yoys) // 2]
        positive_ratio = sum(1 for value in values if value[1] > 0) / len(values) * 100
        score = min(_ROE_SCORE_MAX, max(0.0, roe_median) * _ROE_SCORE_PER_PCT) + min(
            _YOY_SCORE_MAX, max(0.0, yoy_median) * _YOY_SCORE_PER_PCT
        )
        label = (
            "高景气"
            if score >= _HIGH_PROSPERITY_SCORE
            else ("弱景气" if score < _WEAK_PROSPERITY_SCORE else "景气")
        )
        result[industry] = {
            "sample_count": len(values),
            "roe_median": round(roe_median, 2),
            "yoy_median": round(yoy_median, 2),
            "positive_yoy_ratio": round(positive_ratio, 1),
            "prosperity_score": round(score, 1),
            "label": label,
        }
    return result


def prosperity_text(prosperity: Dict[str, Any]) -> str:
    if not prosperity:
        return ""
    return (
        f"{prosperity.get('label', '--')}"
        f"(ROE中位{prosperity.get('roe_median', 0):.1f}%,"
        f"净利同比中位{prosperity.get('yoy_median', 0):+.1f}%,"
        f"样本{prosperity.get('sample_count', 0)}只)"
    )
