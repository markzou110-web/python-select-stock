from typing import Dict, Optional
import os
import requests
from pydantic import BaseModel

class SentimentResult(BaseModel):
    score: float  # -5 to +5
    label: str  # positive/negative/neutral
    reason: str

class SentimentAnalyzer:
    """AI 情绪分析器"""

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.api_url = "https://api.deepseek.com/v1/chat/completions"

    def analyze_sentiment(self, text: str) -> Optional[SentimentResult]:
        """分析新闻情绪

        Args:
            text: 新闻标题或内容

        Returns:
            情绪分析结果，失败返回 None
        """
        if not self.api_key:
            print("⚠️ DEEPSEEK_API_KEY not set, using keyword matching")
            return self._analyze_by_keywords(text)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            prompt = f"""分析这条财经新闻的情绪（正面/负面/中性），返回 JSON 格式：

新闻：{text}

请返回：
{{
    "score": 情绪评分（-5到+5的浮点数）,
    "label": "positive" 或 "negative" 或 "neutral",
    "reason": 简短理由（不超过20字）
}}

只返回 JSON，不要其他内容。"""

            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }

            response = requests.post(self.api_url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 解析 JSON 响应
            import json
            sentiment_data = json.loads(content)

            return SentimentResult(**sentiment_data)

        except Exception as e:
            print(f"⚠️ DeepSeek API 调用失败: {e}")
            return self._analyze_by_keywords(text)

    def _analyze_by_keywords(self, text: str) -> SentimentResult:
        """回退方案：基于关键词的简单情绪判断"""
        positive_keywords = ["上涨", "增长", "盈利", "突破", "利好", "大涨"]
        negative_keywords = ["下跌", "亏损", "下滑", "暴跌", "利空", "违规"]

        score = 0
        for kw in positive_keywords:
            if kw in text:
                score += 1
        for kw in negative_keywords:
            if kw in text:
                score -= 1

        # 限制分数范围
        score = max(-5, min(5, score))

        if score > 0:
            label = "positive"
        elif score < 0:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            score=float(score),
            label=label,
            reason=f"关键词匹配，得分：{score}"
        )
