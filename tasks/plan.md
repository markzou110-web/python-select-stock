# Alpha Vision 增强计划

## 概述
四大功能模块的实施计划，按优先级排序，每个模块独立可交付。

---

## 模块 A: 回测可视化 (Visual Backtesting) — 最高优先级

### A1: K线图买卖点标注 (Chart Overlays)

**现状**: KLineChart.tsx 使用 Lightweight Charts，只显示价格+EMA，无标记功能。

**方案**:

#### 后端改动
- **文件**: `backend/routers/stock.py`
  - 新增端点 `GET /api/stock/{code}/signals?strategy=squeeze&rsi_min=55`
  - 返回格式:
    ```json
    {
      "buy_signals": [{"time": "2024-03-15", "price": 12.50, "reason": "均线粘合突破+放量"}],
      "sell_signals": [{"time": "2024-03-20", "price": 13.75, "reason": "止盈+5%", "pnl": 10.0}]
    }
    ```
  - 复用 `strategy.py` 中已有的 `_simulate_backtest`，但改为返回逐笔交易明细而非汇总统计

- **文件**: `backend/core/strategy.py`
  - 新增函数 `get_signal_details(code, df, strategy_type, params)` → 返回买卖点列表
  - 复用现有信号判断逻辑 (`check_strategy`, `check_pine_strategy`, `check_consensus_strategy`)
  - 逐日遍历历史数据，记录每次触发信号的日期和价格

#### 前端改动
- **文件**: `frontend/src/components/KLineChart.tsx`
  - 添加 `signals?: {buy: Signal[], sell: Signal[]}` prop
  - 使用 Lightweight Charts 的 `ISeriesApi.setMarkers()` API:
    ```ts
    series.setMarkers([
      { time: '2024-03-15', position: 'belowBar', color: '#22c55e', shape: 'arrowUp', text: 'B' },
      { time: '2024-03-20', position: 'aboveBar', color: '#ef4444', shape: 'arrowDown', text: 'S' },
    ])
    ```

- **文件**: `frontend/src/components/ResultsTable.tsx`
  - 展开行加载K线图时，同时请求 signals 数据
  - 将 signals 传递给 KLineChart 组件

### A2: 参数寻优热力图 (Optimization Grid)

**方案**:

#### 后端改动
- **文件**: `backend/routers/scan.py`
  - 新增端点 `POST /api/scan/optimize`
  - 参数:
    ```json
    {
      "code": "000001",
      "strategy": "squeeze",
      "params_grid": {
        "rsi_min": [50, 55, 60, 65],
        "stop_loss_pct": [-5, -8, -10, -12],
        "vol_multiplier": [1.2, 1.5, 1.8]
      }
    }
    ```
  - 返回二维矩阵结果 (每次选取2个参数做正交):
    ```json
    {
      "x_labels": [50, 55, 60, 65],
      "y_labels": [-5, -8, -10, -12],
      "values": [[68, 72, 65, 60], [72, 75, 70, 62], ...],
      "metric": "win_rate"
    }
    ```

- **文件**: `backend/core/strategy.py`
  - 新增函数 `run_optimization_grid(code, df, strategy, param_grid)` → 矩阵结果

#### 前端改动
- **文件**: `frontend/src/components/HeatmapOptimizer.tsx` (新建)
  - 使用 recharts 的 `<HeatChart>` 或自定义 Canvas 渲染
  - 参数选择器 (dropdown 选择2个维度)
  - 热力图 + 颜色梯度 (绿=高胜率, 红=低胜率)
  - 点击单元格显示详细回测统计

---

## 模块 B: 全自动追踪与推送 (Automation & Notification)

### B1: 定时扫描雷达

**现状**: `IntradaySentinel` 用 `threading.Thread` + `time.sleep(30)` 轮询，仅支持单次触发。

**方案**:

- **文件**: `backend/api.py`
  - 重构 `IntradaySentinel`，支持多时间点触发:
    ```python
    schedule_times = ["14:50", "15:10"]  # 可配置
    ```
  - 触发逻辑: 检查 `now_str in schedule_times` (精确到分钟)
  - 每个时间点独立去重 (避免同一分钟重复触发)
  - 扫描结果存入 `scan_history` 表 (已实现)

- **文件**: `backend/core/config.py`
  - 新增配置: `SENTINEL_SCHEDULE_TIMES` (逗号分隔的时间点列表)

### B2: 多渠道 WebHook 推送

**方案**:

- **文件**: `backend/core/notifier.py` (新建)
  - 统一通知接口:
    ```python
    class Notifier:
        async def send(title, body, channels=["bark"])

        def _send_bark(title, body)
        def _send_feishu(title, body, webhook_url)
        def _send_dingtalk(title, body, webhook_url)
        def _send_wecom(title, body, webhook_url)
    ```
  - 飞书/钉钉/企业微信均使用 WebHook URL + JSON POST
  - 配置存储在 `system_settings` 表

- **文件**: `backend/routers/settings.py`
  - 新增端点配置 WebHook URLs:
    - `POST /api/settings/webhook` — 保存渠道配置
    - `GET /api/settings/webhook` — 获取渠道配置
    - `POST /api/settings/webhook/test` — 发送测试消息

- **文件**: `backend/api.py`
  - Sentinel 触发后调用 `Notifier.send()` 替代当前的直接 Bark 调用

- **文件**: `frontend/src/app/page.tsx` (设置面板)
  - 在 Settings 视图中添加 WebHook 配置 UI

---

## 模块 C: 基本面因子融合 (Fundamental Alpha)

### C1: 财务指标接入

**方案**:

- **文件**: `backend/core/fundamental.py` (新建)
  - 使用 akshare 获取:
    - ROE: `ak.stock_financial_analysis_indicator()`
    - 净利润增长率: `ak.stock_financial_report_sina()`
    - PE 分位数: 从 `ak.stock_a_indicator_lg()` 计算 5 年百分位
  - 新增数据库表 `stock_fundamentals`:
    ```sql
    CREATE TABLE stock_fundamentals (
      code VARCHAR(20) PRIMARY KEY,
      roe FLOAT,           -- 最近季度 ROE
      net_profit_yoy FLOAT, -- 净利润同比增速
      pe_ttm FLOAT,        -- 滚动 PE
      pe_percentile FLOAT,  -- PE 5年分位数
      revenue_yoy FLOAT,   -- 营收同比
      updated_at DATE
    );
    ```
  - 周度同步 (财报数据不需日更)

- **文件**: `backend/routers/sync.py`
  - 新增端点 `POST /api/sync/fundamentals` — 批量同步基本面数据

### C2: 基本面+技术面融合评分

- **文件**: `backend/core/strategy.py`
  - 修改 Score 计算公式:
    ```python
    # 现有: 纯技术面 Score
    # 新增: 基本面加权
    fundamental_score = 0
    if roe > 15: fundamental_score += 15
    if net_profit_yoy > 20: fundamental_score += 15  # 净利润断层
    if pe_percentile < 30: fundamental_score += 10     # 低估值

    total_score = tech_score + fundamental_score
    ```
  - 当 "净利润断层 + Pine 底部突破" 同时满足 → Score * 1.5 权重

- **文件**: `backend/routers/scan.py`
  - 扫描结果新增 `fundamental` 字段:
    ```json
    {"roe": 18.5, "pe_pct": 25.3, "profit_yoy": 35.2, "label": "戴维斯双击"}
    ```

---

## 模块 D: 代码质量与基础设施 (从上一轮优化延续)

- **已完成**: 缓存 LRU+TTL、批量价格更新、前端 useMemo、复合索引、debug 清理
- **待做** (低优先级):
  - 统一异常类 `StockAnalysisError` → `DatabaseError` / `ScanError`
  - 添加 API 限流 (FastAPI `RateLimiter` middleware)
  - 单元测试骨架 (pytest fixtures for DB engine mock)

---

## 实施顺序 (推荐)

| 阶段 | 模块 | 预计改动 | 依赖 |
|------|------|----------|------|
| **Phase 1** | A1: 买卖点标注 | 后端1个新端点 + 前端KLineChart改 | 无 |
| **Phase 2** | B1+B2: 定时+推送 | 后端2个文件 + 前端设置UI | 无 |
| **Phase 3** | A2: 参数寻优 | 后端1个端点 + 前端新组件 | Phase 1 |
| **Phase 4** | C1+C2: 基本面 | 后端新模块 + DB schema + 策略改 | 无 |

Phase 1 和 Phase 2 可并行开发，Phase 3 依赖 Phase 1 的信号接口。
