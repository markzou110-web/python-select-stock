from typing import Dict, Optional
import os
import requests
from pydantic import BaseModel

class SentimentResult(BaseModel):
    score: float  # -5 to +5
    label: str  # positive/negative/neutral
    reason: str

class SentimentAnalyzer:
    """AI 情绪分析器 - 支持智谱AI和DeepSeek"""

    def __init__(self):
        # 优先使用智谱AI，其次DeepSeek
        self.zhipu_api_key = os.getenv("ZHIPUAI_API_KEY", "")
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")

        # 智谱AI配置
        self.zhipu_api_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

        # DeepSeek配置（备用）
        self.deepseek_api_url = "https://api.deepseek.com/v1/chat/completions"

        # 选择使用的API
        if self.zhipu_api_key:
            self.provider = "zhipu"
            self.api_key = self.zhipu_api_key
            self.api_url = self.zhipu_api_url
            self.model = "glm-4-flash"  # 智谱AI的快速模型
        elif self.deepseek_api_key:
            self.provider = "deepseek"
            self.api_key = self.deepseek_api_key
            self.api_url = self.deepseek_api_url
            self.model = "deepseek-chat"
        else:
            self.provider = None
            self.api_key = ""
            self.api_url = ""

    def analyze_sentiment(self, text: str) -> Optional[SentimentResult]:
        """分析新闻情绪

        Args:
            text: 新闻标题或内容

        Returns:
            情绪分析结果，失败返回 None
        """
        if not self.api_key:
            print("⚠️ ZHIPUAI_API_KEY 或 DEEPSEEK_API_KEY 未设置，使用关键词匹配")
            return self._analyze_by_keywords(text)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            # 根据不同provider调整提示词
            if self.provider == "zhipu":
                prompt = f"""请分析以下财经新闻的情绪，返回JSON格式：

新闻标题：{text}

请判断该新闻的情绪（正面/负面/中性），并返回：
{{
    "score": 情绪评分（-5到+5的浮点数，正数表示正面，负数表示负面）,
    "label": "positive" 或 "negative" 或 "neutral",
    "reason": 简短理由（不超过15个字）
}}

只返回JSON，不要其他内容。"""
            else:  # deepseek
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
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }

            response = requests.post(self.api_url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 解析 JSON 响应
            import json
            # 清理可能的markdown代码块标记
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            sentiment_data = json.loads(content)

            return SentimentResult(**sentiment_data)

        except Exception as e:
            print(f"⚠️ {self.provider.upper() if self.provider else 'AI'} API 调用失败: {e}")
            return self._analyze_by_keywords(text)

    def _analyze_by_keywords(self, text: str) -> SentimentResult:
        """回退方案：基于关键词的简单情绪判断"""
        positive_keywords = ["上涨", "增长", "盈利", "突破", "利好", "大涨", "涨停", "回购", "增持", "利好"]
        negative_keywords = ["下跌", "亏损", "下滑", "暴跌", "利空", "违规", "减持", "警示", "问询", "调查"]

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
