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
                # 改进提取：寻找标题中的【】或第一个冒号前的内容，或者直接使用前 N 个字
                result = conn.execute(text("""
                    SELECT theme, COUNT(*) as news_count
                    FROM (
                        SELECT
                            CASE 
                                WHEN title ~ '【(.*?)】' THEN (regexp_match(title, '【(.*?)】'))[1]
                                WHEN title ~ '：' THEN split_part(title, '：', 1)
                                ELSE substring(title from 1 for 15)
                            END as theme
                        FROM news_raw
                        WHERE publish_time >= NOW() - INTERVAL '24 hours'
                    ) t
                    GROUP BY theme
                    HAVING length(theme) >= 2
                    ORDER BY news_count DESC
                    LIMIT 30
                """))

                themes = {}
                for row in result:
                    theme_name = row[0].strip()
                    if not theme_name: continue
                    news_count = row[1]
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

    def get_theme_stocks(self, theme_id: int) -> List[Dict]:
        """获取题材关联的股票列表
        
        通过 themes -> news_raw -> news_stocks -> stock_basic 关联
        """
        try:
            with self.engine.connect() as conn:
                # 1. 先找到题材名称
                theme_res = conn.execute(text("SELECT name FROM themes WHERE id = :id"), {"id": theme_id}).fetchone()
                if not theme_res: return []
                theme_name = theme_res[0]
                
                # 2. 找到关联的股票
                # 策略 A: 匹配行业名称
                # 策略 B: 匹配 news_raw.title 包含 theme_name 的新闻，再找其关联股票
                result = conn.execute(text("""
                    WITH theme_news AS (
                        SELECT id FROM news_raw nr WHERE nr.title LIKE :theme_pattern
                    ),
                    industry_stocks AS (
                        SELECT code, name, 0.8 as relevance 
                        FROM stock_basic 
                        WHERE industry = :name OR industry LIKE :industry_pattern
                    ),
                    news_associated_stocks AS (
                        SELECT DISTINCT sb.code, sb.name, ns.relevance
                        FROM theme_news tn
                        JOIN news_stocks ns ON tn.id = ns.news_id
                        JOIN stock_basic sb ON ns.stock_code = sb.code
                    )
                    SELECT code, name, MAX(relevance) as relevance 
                    FROM (
                        SELECT * FROM industry_stocks
                        UNION ALL
                        SELECT * FROM news_associated_stocks
                    ) combined
                    GROUP BY code, name
                    ORDER BY relevance DESC
                    LIMIT 30
                """), {
                    "theme_pattern": f"%{theme_name}%",
                    "name": theme_name,
                    "industry_pattern": f"%{theme_name}%"
                })
                
                stocks = []
                for row in result:
                    stocks.append({
                        "code": row[0],
                        "name": row[1],
                        "relevance": row[2] or 1.0
                    })
                return stocks
        except Exception as e:
            print(f"❌ 获取题材成分股失败: {e}")
            return []
