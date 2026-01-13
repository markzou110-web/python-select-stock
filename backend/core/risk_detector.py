from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy import text

class RiskDetector:
    """风险事件检测器"""

    # 风险关键词映射
    RISK_KEYWORDS = {
        "financial": ["亏损", "业绩下滑", "债务", "财务造假", "审计非标"],
        "operational": ["立案调查", "处罚", "诉讼", "违规", "造假"],
        "market": ["减持", "质押", "平仓", "解禁", "停牌"],
        "major": ["事故", "停产", "问责", "罢免", "震荡"]
    }

    def __init__(self, engine):
        self.engine = engine

    def detect_risks_from_news(self, news_items: List[Dict]) -> List[Dict]:
        """从新闻中检测风险事件

        Args:
            news_items: 新闻列表

        Returns:
            风险事件列表
        """
        risk_events = []

        for news in news_items:
            title = news.get("title", "")

            # 检查是否包含风险关键词
            for risk_type, keywords in self.RISK_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in title:
                        # 确定风险等级
                        if risk_type in ["financial", "operational"] and ("立案" in title or "造假" in title):
                            level = "high"
                        elif risk_type == "market":
                            level = "medium"
                        else:
                            level = "low"

                        risk_events.append({
                            "stock_code": self._extract_stock_code(title),
                            "risk_type": risk_type,
                            "risk_level": level,
                            "title": title,
                            "description": title[:100],
                            "news_url": news.get("url", ""),
                            "event_date": datetime.now().date()
                        })
                        break  # 每条新闻只识别一个主要风险

        return risk_events

    def save_risk_events(self, risk_events: List[Dict]) -> bool:
        """保存风险事件到数据库

        Args:
            risk_events: 风险事件列表

        Returns:
            是否保存成功
        """
        try:
            with self.engine.connect() as conn:
                # 先清空今日的风险事件（避免重复）
                conn.execute(text("""
                    DELETE FROM risk_events WHERE event_date = CURRENT_DATE
                """))

                for event in risk_events:
                    # 如果股票代码为空，跳过
                    if not event.get("stock_code"):
                        continue

                    conn.execute(text("""
                        INSERT INTO risk_events (stock_code, risk_type, risk_level, title, description, news_url, event_date)
                        VALUES (:stock_code, :risk_type, :risk_level, :title, :description, :news_url, :event_date)
                    """), {
                        "stock_code": event.get("stock_code"),
                        "risk_type": event["risk_type"],
                        "risk_level": event["risk_level"],
                        "title": event["title"],
                        "description": event["description"],
                        "news_url": event["news_url"],
                        "event_date": event["event_date"]
                    })
                conn.commit()
                return True
        except Exception as e:
            print(f"❌ 保存风险事件失败: {e}")
            return False

    def get_risk_events(self, days: int = 30) -> List[Dict]:
        """获取最近N天的风险事件

        Args:
            days: 天数

        Returns:
            风险事件列表
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT stock_code, risk_type, risk_level, title, description, news_url, event_date
                    FROM risk_events
                    WHERE event_date >= NOW() - INTERVAL ':days days'
                    ORDER BY event_date DESC, risk_level
                """).bindparams(days=days))

                risks = []
                for row in result:
                    risks.append({
                        "stock_code": row[0],
                        "risk_type": row[1],
                        "risk_level": row[2],
                        "title": row[3],
                        "description": row[4],
                        "news_url": row[5],
                        "event_date": row[6].isoformat() if row[6] else None
                    })

                return risks

        except Exception as e:
            print(f"❌ 获取风险事件失败: {e}")
            return []

    def _extract_stock_code(self, title: str) -> Optional[str]:
        """从标题中提取股票代码（简化版）

        Args:
            title: 新闻标题

        Returns:
            股票代码或None
        """
        # 简化版：尝试从标题中提取6位数字
        import re
        match = re.search(r'\b\d{6}\b', title)
        if match:
            return match.group()
        return None
