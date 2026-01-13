from typing import List, Dict, Optional
from sqlalchemy import text

class ThemeTracker:
    """题材热点追踪器"""

    def __init__(self, engine):
        self.engine = engine

    def update_themes(self) -> Dict[str, float]:
        """更新题材热度分数

        Returns:
            题材名称 -> 热度分数的字典
        """
        # 简化版：基于新闻数量计算热度
        # 完整版需要结合股票涨幅、成交额等数据

        try:
            with self.engine.connect() as conn:
                # 从 news_raw 表统计最近 24 小时的新闻
                result = conn.execute(text("""
                    SELECT
                        substring(title from '^(.{5,20})') as theme,  -- 简化提取
                        COUNT(*) as news_count
                    FROM news_raw
                    WHERE publish_time >= NOW() - INTERVAL '24 hours'
                    GROUP BY theme
                    ORDER BY news_count DESC
                    LIMIT 20
                """))

                themes = {}
                for row in result:
                    theme_name = row[0]
                    news_count = row[1]
                    # 简化热度计算：仅基于新闻数量
                    hotness = float(news_count * 10)
                    themes[theme_name] = hotness

                    # 更新或插入题材表
                    conn.execute(text("""
                        INSERT INTO themes (name, hotness, updated_at)
                        VALUES (:name, :hotness, NOW())
                        ON CONFLICT (name) DO UPDATE
                        SET hotness = :hotness, updated_at = NOW()
                    """), {"name": theme_name, "hotness": hotness})

                conn.commit()
                return themes

        except Exception as e:
            print(f"❌ 更新题材失败: {e}")
            return {}

    def get_top_themes(self, limit: int = 10) -> List[Dict]:
        """获取热门题材列表

        Args:
            limit: 返回数量限制

        Returns:
            题材列表，每个包含 id, name, hotness, life_cycle_stage, leader_stock
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT id, name, hotness, life_cycle_stage, leader_stock
                    FROM themes
                    ORDER BY hotness DESC
                    LIMIT :limit
                """), {"limit": limit})

                themes = []
                for row in result:
                    themes.append({
                        "id": row[0],
                        "name": row[1],
                        "hotness": float(row[2]) if row[2] else 0.0,
                        "life_cycle_stage": row[3] or "unknown",
                        "leader_stock": row[4] or ""
                    })

                return themes

        except Exception as e:
            print(f"❌ 获取题材失败: {e}")
            return []
