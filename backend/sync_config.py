"""
数据同步配置文件
可以配置多个数据源的优先级和参数
"""

# 数据源配置
DATA_SOURCES = {
    "sources": [
        {
            "name": "东方财富",
            "enabled": True,
            "priority": 1,  # 优先级，数字越小优先级越高
            "timeout": 30,
            "retry": 3,
            "description": "东方财富网数据源，数据最全但不稳定"
        },
        {
            "name": "新浪财经",
            "enabled": True,
            "priority": 2,
            "timeout": 30,
            "retry": 3,
            "description": "新浪财经数据源，稳定性一般"
        },
        {
            "name": "腾讯财经",
            "enabled": True,
            "priority": 3,
            "timeout": 30,
            "retry": 3,
            "description": "腾讯财经数据源，最稳定但只有成交额"
        }
    ]
}

# 同步策略配置
SYNC_CONFIG = {
    # 基本设置
    "max_workers": 5,              # 并发线程数
    "batch_size": 100,              # 每批处理数量
    "delay_min": 0.3,               # 最小延迟（秒）
    "delay_max": 0.8,               # 最大延迟（秒）

    # 重试设置
    "max_retries": 3,               # 最大重试次数
    "retry_delay": 1,               # 重试延迟（秒）

    # 数据质量检查
    "validate_data": True,          # 是否验证数据
    "skip_anomalies": True,         # 是否跳过异常数据

    # 日志设置
    "log_level": "INFO",            # 日志级别
    "log_details": True,            # 是否记录详细日志

    # 自动切换设置
    "auto_switch_source": True,     # 是否自动切换数据源
    "source_health_check": True,   # 是否检查数据源健康状态
    "health_check_interval": 300,   # 健康检查间隔（秒）
}

# 市场设置
MARKET_CONFIG = {
    "min_market_cap": 5000000000,   # 最小市值（50亿）
    "include_st": True,             # 是否包含ST股
    "include_new": True,            # 是否包含新股
    "include_bj": False,            # 是否包含北交所
}

# 数据时间范围
DATE_RANGE = {
    "default_days": 365,            # 默认拉取天数
    "full_sync_days": 1825,         # 全量同步天数（5年）
    "incremental_days": 30,         # 增量同步天数
}

# 数据源特定配置
SOURCE_SPECIFIC = {
    "东方财富": {
        "use_spot_em": True,         # 使用实时行情接口
        "adjust": "qfq",             # 复权类型：qfq-前复权，hfq-后复权，""-不复权
    },
    "新浪财经": {
        "adjust": "qfq",
    },
    "腾讯财经": {
        "use_volume": False,         # 腾讯没有成交量，只有成交额
    }
}

# 代理设置（如需要）
PROXY_CONFIG = {
    "enabled": False,
    "http_proxy": None,
    "https_proxy": None,
    "no_proxy": ["localhost", "127.0.0.1"],
}

# 缓存设置
CACHE_CONFIG = {
    "enabled": True,
    "stock_list_ttl": 3600,         # 股票列表缓存时间（秒）
    "quote_ttl": 60,                 # 实时行情缓存时间（秒）
}


def get_config() -> dict:
    """获取完整配置"""
    return {
        "data_sources": DATA_SOURCES,
        "sync": SYNC_CONFIG,
        "market": MARKET_CONFIG,
        "date_range": DATE_RANGE,
        "source_specific": SOURCE_SPECIFIC,
        "proxy": PROXY_CONFIG,
        "cache": CACHE_CONFIG,
    }


def get_enabled_sources() -> list:
    """获取启用的数据源列表（按优先级排序）"""
    enabled = [s for s in DATA_SOURCES["sources"] if s.get("enabled", True)]
    return sorted(enabled, key=lambda x: x.get("priority", 999))


def get_source_config(source_name: str) -> dict:
    """获取特定数据源的配置"""
    for source in DATA_SOURCES["sources"]:
        if source["name"] == source_name:
            return source
    return {}


if __name__ == "__main__":
    # 打印配置信息
    import json

    print("="*60)
    print("数据同步配置")
    print("="*60)

    print("\n启用的数据源:")
    for source in get_enabled_sources():
        print(f"  {source['priority']}. {source['name']} - {source['description']}")

    print("\n同步策略:")
    for key, value in SYNC_CONFIG.items():
        print(f"  {key}: {value}")

    print("\n市场配置:")
    for key, value in MARKET_CONFIG.items():
        print(f"  {key}: {value}")

    print("\n" + "="*60)
