"""多空辩论 AI 复核（借鉴 TradingAgents 的角色化辩论结构，P1）。

对单个候选：bull 角色只看多证据、bear 角色只看空证据与风险，各自独立输出，
再由 judge 合成结论。复用 theme_heat._post_chat_text 纯文本客户端与既有
AI 配置；未配置 AI 或异常时 fail-open 返回 None。
"""
from typing import Any, Dict, List, Optional

from core.logging_config import logger


def _candidate_digest(candidate: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("名称", "代码", "现价", "涨幅%", "strategy_type", "Score",
            "trade_opportunity_score", "sector_mainline", "共振",
            "pa_score", "signal_sources", "tv_execution_tier")
    return {k: candidate.get(k) for k in keys if candidate.get(k) not in (None, "", [])}


def run_bull_bear_debate(candidate: Dict[str, Any],
                         evidence_lines: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """三段调用：bull → bear → judge。返回 {"bull","bear","verdict"} 或 None。"""
    try:
        from core.config import config
        from core.theme_heat import _post_chat_text

        if not config.is_ai_analysis_configured():
            return None
        digest = _candidate_digest(candidate)
        evidence_text = "\n".join(evidence_lines or [])
        base_user = (
            f"候选数据：{_candidate_digest and __import__('json').dumps(digest, ensure_ascii=False)}\n"
            f"补充证据：\n{evidence_text or '（无）'}"
        )

        def _ask(role_hint: str) -> str:
            payload = {
                "model": config.AI_MODEL,
                "messages": [
                    {"role": "system", "content": role_hint},
                    {"role": "user", "content": base_user},
                ],
                "temperature": 0.4,
                "max_tokens": 300,
            }
            text_out, _usage = _post_chat_text(payload)
            return text_out[:400]

        bull = _ask(
            "你是 A 股多空辩论中的多头研究员。只基于候选数据中支持做多的证据"
            "（趋势结构、板块主线、资金、共振），输出不超过80字的多头论证。"
            "禁止编造数据外事实，禁止给出仓位建议。"
        )
        bear = _ask(
            "你是 A 股多空辩论中的空头研究员。只基于候选数据中支持回避的证据"
            "（位置过高、波动、板块走弱、止损结构、市场状态），输出不超过80字的空头论证。"
            "禁止编造数据外事实，禁止给出仓位建议。"
        )
        if not bull or not bear:
            return None
        judge = _ask(
            "你是 A 股多空辩论的裁判。以下是多头与空头论证，输出不超过100字的合成结论："
            "哪一方证据更硬、置信度（高/中/低）与一句话理由。禁止买卖指令。"
            f"\n多头：{bull}\n空头：{bear}"
        )
        if not judge:
            return None
        return {"bull": bull, "bear": bear, "verdict": judge}
    except Exception as exc:
        logger.debug(f"bull-bear debate unavailable: {exc}")
        return None
