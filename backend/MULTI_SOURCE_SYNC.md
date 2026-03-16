# 多数据源同步系统使用文档

## 概述

系统支持配置多个数据源，当一个数据源不可用时会自动切换到其他可用数据源。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    DataSourceManager                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  东方财富      │  │  新浪财经      │  │  腾讯财经      │          │
│  │  (优先级1)    │  │  (优先级2)    │  │  (优先级3)    │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    MultiSourceSync                            │
│  - 自动选择可用数据源                                          │
│  - 失败自动切换                                                 │
│  - 健康检查                                                   │
└─────────────────────────────────────────────────────────────┘
```

## 数据源配置

### 1. 东方财富
- **优先级**: 1（最高）
- **接口**: `stock_zh_a_hist_em()`
- **优点**: 数据最全
- **缺点**: 不稳定，易被反爬虫

### 2. 新浪财经
- **优先级**: 2
- **接口**: `stock_zh_a_daily()`
- **优点**: 数据格式标准
- **缺点**: 大批量请求会被封IP

### 3. 腾讯财经
- **优先级**: 3（最低）
- **接口**: `stock_zh_a_hist_tx()`
- **优点**: 最稳定，不易被封
- **缺点**: 只有成交额，没有成交量

## 命令行使用

### 查看数据源状态
```bash
cd backend
venv_new/bin/python sync_cli.py status
```

### 检查单个股票
```bash
venv_new/bin/python sync_cli.py check --code 000001
```

### 同步数据
```bash
# 同步所有股票
venv_new/bin/python sync_cli.py sync

# 同步前100只股票
venv_new/bin/python sync_cli.py sync --limit 100

# 同步指定股票
venv_new/bin/python sync_cli.py sync --code 000001
```

## 代码集成

### 基础用法
```python
from core.multi_source_sync import MultiSourceSync

# 初始化同步器
syncer = MultiSourceSync()

# 同步单只股票
result = syncer.sync_single_stock("000001")
print(result)
# {'code': '000001', 'success': True, 'source': '腾讯财经', ...}

# 批量同步
codes = ["000001", "000002", "600000"]
results = syncer.sync_batch(codes)
```

### 高级用法
```python
from core.multi_source_sync import DataSourceManager, MultiSourceSync

# 创建管理器
manager = DataSourceManager()

# 获取状态报告
report = manager.get_status_report()
for name, info in report.items():
    print(f"{name}: {info['status']}")

# 获取特定数据源
source = manager.get_source("腾讯财经")
if source:
    df = source.get_hist_data("000001", "20240101")
```

## 配置文件

编辑 `sync_config.py` 来自定义配置：

```python
DATA_SOURCES = {
    "sources": [
        {
            "name": "东方财富",
            "enabled": True,
            "priority": 1,
            "timeout": 30,
            "retry": 3,
        },
        # ...
    ]
}

SYNC_CONFIG = {
    "max_workers": 5,
    "delay_min": 0.3,
    "delay_max": 0.8,
    # ...
}
```

## API 集成

### 更新现有代码

```python
# 旧代码（单数据源）
df = ak.stock_zh_a_hist(symbol=code, ...)

# 新代码（多数据源）
from core.multi_source_sync import MultiSourceSync

syncer = MultiSourceSync()
for source in syncer.manager.sources:
    if source.is_available():
        try:
            df = source.get_hist_data(code, start_date)
            break
        except Exception:
            continue
```

## 故障处理

| 错误类型 | 处理方式 |
|----------|----------|
| 连接失败 | 切换到下一个数据源 |
| 超时 | 重试3次后切换 |
| 限流 | 标记为 rate_limited，5分钟后重试 |
| 连续失败3次 | 标记为 unavailable |

## 最佳实践

1. **优先使用腾讯财经**：最稳定
2. **设置合理延迟**：避免被封IP
3. **监控数据源状态**：定期检查健康状态
4. **批量同步分批**：每批100只股票
5. **使用命令行工具**：便于调试和监控
