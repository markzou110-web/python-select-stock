"""
真实新闻数据获取脚本

功能：
1. 从东方财富抓取市场热点新闻
2. 提取题材关键词
3. 更新题材热度
4. 检测风险事件
5. 保存到数据库

使用方法：
    python backend/scripts/fetch_real_news.py
"""

import sys
import os
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import get_db_engine
from core.news import EastMoneyCrawler, NewsDeduplicator
from core.theme_tracker import ThemeTracker
from core.risk_detector import RiskDetector
from sqlalchemy import text


def fetch_market_news():
    """
    抓取市场新闻（从东方财富首页）
    """
    print("\n📰 开始抓取市场新闻...")

    # 这里我们使用 akshare 获取财经新闻
    try:
        import akshare as ak

        # 获取东方财富网的财经新闻
        print("📡 从东方财富网获取最新新闻...")
        df_news = ak.stock_news_em()

        if df_news is not None and not df_news.empty:
            print(f"✅ 成功获取 {len(df_news)} 条新闻")

            # 显示前几条新闻标题
            print("\n📋 最新新闻标题：")
            for idx, row in df_news.head(10).iterrows():
                print(f"  {idx + 1}. {row['新闻标题']}")

            return df_news
        else:
            print("⚠️ 未能获取新闻数据")
            return None

    except Exception as e:
        print(f"❌ 抓取新闻失败: {e}")
        return None


def extract_themes_from_news(news_df, engine):
    """
    从新闻中提取题材关键词
    """
    print("\n🔍 开始提取题材关键词...")

    if news_df is None or news_df.empty:
        print("⚠️ 没有新闻数据，跳过题材提取")
        return

    # 扩展的关键词提取（基于标题）
    theme_keywords = {
        '人工智能': ['AI', '人工智能', '芯片', '算力', '大模型', '智能', '算法'],
        '新能源汽车': ['新能源', '电动车', '电池', '充电桩', '锂电', '汽车', '整车'],
        '半导体': ['半导体', '集成电路', '芯片', '存储', '晶圆', '封测'],
        '生物医药': ['医药', '生物', '疫苗', '创新药', '医疗', '制药', '研发'],
        '数字经济': ['数字', '云计算', '大数据', '区块链', '元宇宙', '互联网'],
        '军工': ['军工', '国防', '航空航天', '导弹', '雷达', '卫星'],
        '消费': ['消费', '零售', '白酒', '食品', '旅游', '餐饮', '商业'],
        '金融': ['银行', '保险', '证券', '金融', '期货', '信托', '贷款'],
        '房地产': ['房地产', '地产', '住房', '物业', '建筑', '装修'],
        '新能源': ['光伏', '风电', '储能', '硅料', '逆变器', '发电'],
        '低空经济': ['低空', 'eVTOL', '无人机', '飞行汽车', '通航'],
        '量子计算': ['量子', '量子计算', '量子通信', '量子芯片'],
        '机器人': ['机器人', '自动化', '工业机器人', '服务机器人'],
        '白酒': ['白酒', '酿酒', '茅台', '五粮液', '剑南春'],
        '锂电池': ['锂电', '锂电池', '正极', '负极', '电解液'],
        'CXO': ['CXO', '研发', '临床', 'CRO', 'CDMO'],
        '预制菜': ['预制菜', '速食', '方便菜', '半成品'],
    }

    # 统计每个题材的新闻数量
    theme_counts = {}

    for _, row in news_df.iterrows():
        title = str(row['新闻标题'])

        for theme, keywords in theme_keywords.items():
            for keyword in keywords:
                if keyword in title:
                    theme_counts[theme] = theme_counts.get(theme, 0) + 1
                    break  # 每条新闻只计入一个题材

    print(f"\n📊 题材热度统计：")
    for theme, count in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True):
        hotness = count * 10  # 热度 = 新闻数 * 10
        print(f"  {theme}: {count} 条新闻 (热度 {hotness})")

    # 更新到数据库
    if theme_counts:
        print("\n💾 更新题材数据到数据库...")
        tracker = ThemeTracker(engine)

        with engine.connect() as conn:
            for theme_name, news_count in theme_counts.items():
                hotness = float(news_count * 10)

                # 简单的生命周期判断
                if news_count > 5:
                    stage = 'emerging'
                elif news_count > 3:
                    stage = 'growing'
                else:
                    stage = 'mature'

                conn.execute(text("""
                    INSERT INTO themes (name, hotness, life_cycle_stage, updated_at)
                    VALUES (:name, :hotness, :stage, NOW())
                    ON CONFLICT (name) DO UPDATE
                    SET hotness = :hotness, life_cycle_stage = :stage, updated_at = NOW()
                """), {
                    "name": theme_name,
                    "hotness": hotness,
                    "stage": stage
                })

            conn.commit()
            print(f"✅ 成功更新 {len(theme_counts)} 个题材")


def detect_risk_events_from_news(news_df, engine):
    """
    从新闻中检测风险事件
    """
    print("\n⚠️  开始检测风险事件...")

    if news_df is None or news_df.empty:
        print("⚠️ 没有新闻数据，跳过风险检测")
        return

    detector = RiskDetector(engine)

    # 将新闻转换为字典格式
    news_items = []
    for _, row in news_df.iterrows():
        news_items.append({
            'title': row['新闻标题'],
            'url': row.get('新闻链接', ''),
            'publish_time': datetime.now()
        })

    # 检测风险
    risk_events = detector.detect_risks_from_news(news_items)

    if risk_events:
        print(f"\n🚨 检测到 {len(risk_events)} 个风险事件：")

        for event in risk_events:
            print(f"\n  股票: {event['stock_code']}")
            print(f"  类型: {event['risk_type']}")
            print(f"  等级: {event['risk_level']}")
            print(f"  标题: {event['title']}")

        # 保存到数据库
        print("\n💾 保存风险事件到数据库...")
        success = detector.save_risk_events(risk_events)

        if success:
            print(f"✅ 成功保存 {len(risk_events)} 个风险事件")
        else:
            print("❌ 保存风险事件失败")
    else:
        print("✅ 未检测到风险事件")


def main():
    """
    主函数
    """
    print("=" * 60)
    print("🚀 Alpha Vision 新闻舆情数据获取系统")
    print("=" * 60)
    print(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 初始化数据库连接
    print("\n🔧 初始化数据库连接...")
    engine = get_db_engine()

    if not engine:
        print("❌ 数据库连接失败")
        return

    print("✅ 数据库连接成功")

    # 步骤1: 抓取市场新闻
    news_df = fetch_market_news()

    if news_df is not None:
        # 步骤2: 提取题材
        extract_themes_from_news(news_df, engine)

        # 步骤3: 检测风险
        detect_risk_events_from_news(news_df, engine)

    # 显示统计
    print("\n" + "=" * 60)
    print("📊 数据统计")
    print("=" * 60)

    with engine.connect() as conn:
        # 题材统计
        themes = conn.execute(text("""
            SELECT COUNT(*) as count FROM themes
        """)).fetchone()[0]

        print(f"🔥 热点题材: {themes} 个")

        # 风险事件统计
        risks = conn.execute(text("""
            SELECT COUNT(*) as count FROM risk_events
            WHERE event_date >= CURRENT_DATE
        """)).fetchone()[0]

        print(f"⚠️  今日风险事件: {risks} 个")

    print("\n" + "=" * 60)
    print(f"✅ 完成！结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n💡 提示：")
    print("  - 定时运行此脚本可保持数据更新")
    print("  - 建议配置 crontab 每天运行一次")
    print("  - 刷新前端页面查看最新数据")


if __name__ == "__main__":
    main()
