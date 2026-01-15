# 资金流向分析功能设计文档

**日期**: 2025-01-15
**版本**: v1.0
**作者**: Claude & User

---

## 1. 概述

本文档描述了为 Alpha Vision Pro A 股量化交易终端添加资金流向分析功能的完整设计方案。该功能将主力大单资金流向数据融入现有的均线粘合策略，提供更精准的选股信号。

### 1.1 核心目标

- 获取个股主力大单资金流向数据（超大单、大单、中单、小单）
- 将资金流向作为新的筛选条件融入现有策略
- 提供资金流数据的可视化和历史查询

### 1.2 技术选型

- **数据源**: akshare (免费接口)
- **后端**: FastAPI + PostgreSQL
- **前端**: Next.js + TypeScript + Tailwind CSS

---

## 2. 架构设计

采用**模块化扩展**方式，在不破坏现有结构的前提下添加资金流向分析功能：

### 2.1 模块结构

```
backend/
├── core/
│   ├── money_flow.py          [新增] 资金流向数据获取与处理
│   ├── strategy.py            [修改] 集成资金流过滤条件
│   ├── data.py                [修改] 添加混合同步机制
│   └── db.py                  [修改] 数据库表结构扩展
├── sync_data.py               [修改] 扩展同步逻辑
└── api.py                     [修改] API 参数扩展

frontend/
├── lib/
│   └── api.ts                 [修改] 扫描接口参数扩展
└── components/
    ├── FilterModal.tsx        [修改] 新增资金流筛选选项
    └── ResultsTable.tsx       [修改] 新增资金流显示列
```

### 2.2 核心模块职责

#### `backend/core/money_flow.py` (新增)
- 封装所有与资金流向相关的数据获取和处理逻辑
- 使用 akshare 的 `stock_individual_fund_flow` 系列接口获取个股资金流数据
- 提供统一的计算函数：主力净流入、超大单/大单/中单/小单分类统计
- 实现缓存机制（10 分钟 TTL）

#### `backend/core/strategy.py` (修改)
- 在现有的 `check_strategy()` 函数中新增资金流向过滤条件
- 保持向后兼容，通过参数 `use_money_flow_filter` 控制是否启用
- 调整评分权重，将资金流因子纳入综合评分体系

#### `backend/core/db.py` (修改)
- 扩展 `daily_k` 表，添加资金流字段
- 新建 `money_flow_daily` 表存储详细的分档资金流历史

#### `backend/sync_data.py` (修改)
- 实现混合同步模式：
  - 每日定时同步全量数据
  - 扫描时数据缺失或过期触发单只股票快速同步（最近 5 天）
- 利用现有的并发机制（ThreadPoolExecutor）

---

## 3. 数据模型设计

### 3.1 `daily_k` 表扩展

```sql
ALTER TABLE daily_k ADD COLUMN IF NOT EXISTS:
- main_net_inflow DECIMAL(15,2)  -- 主力净流入（万元）
- super_large_net DECIMAL(15,2)  -- 超大单净流入
- large_net DECIMAL(15,2)        -- 大单净流入
- medium_net DECIMAL(15,2)       -- 中单净流入
- small_net DECIMAL(15,2)        -- 小单净流入
```

### 3.2 `money_flow_daily` 表（新建）

```sql
CREATE TABLE money_flow_daily (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10),                    -- 股票代码
    date DATE,                           -- 日期
    main_net_inflow DECIMAL(15,2),       -- 主力净流入（万元）
    super_large_net DECIMAL(15,2),       -- 超大单净流入（万元）
    large_net DECIMAL(15,2),             -- 大单净流入（万元）
    medium_net DECIMAL(15,2),            -- 中单净流入（万元）
    small_net DECIMAL(15,2),             -- 小单净流入（万元）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, date)
);

CREATE INDEX idx_money_flow_code_date ON money_flow_daily(code, date);
CREATE INDEX idx_money_flow_date ON money_flow_daily(date);
```

### 3.3 数据示例

| code | date | main_net_inflow | super_large_net | large_net | medium_net | small_net |
|------|------|-----------------|-----------------|-----------|------------|-----------|
| 000001 | 2025-01-15 | 12500.50 | 8000.00 | 4500.50 | -2000.00 | -500.00 |
| 000001 | 2025-01-14 | -3200.00 | -2000.00 | -1200.00 | 1500.00 | 200.00 |

---

## 4. API 接口设计

### 4.1 核心函数 (`core/money_flow.py`)

```python
@lru_cache(maxsize=1000)
def get_individual_fund_flow(code: str, days: int = 5) -> pd.DataFrame:
    """
    获取个股最近 N 天的资金流向数据

    Args:
        code: 股票代码
        days: 查询天数

    Returns:
        DataFrame with columns:
        - date: 日期
        - main_net_inflow: 主力净流入（万元）
        - super_large_net: 超大单净流入
        - large_net: 大单净流入
        - medium_net: 中单净流入
        - small_net: 小单净流入
    """
    cache_key = f'money_flow_{code}_{days}'
    cached = get_cached_data(cache_key, 600)  # 10分钟缓存
    if cached is not None:
        return cached

    try:
        df = ak.stock_individual_fund_flow(
            stock=code,
            symbol="个股资金流"
        )

        if df.empty:
            return pd.DataFrame()

        # 数据清洗和重命名
        df = df.head(days)
        df['date'] = pd.to_datetime(df['日期'])

        set_cached_data(cache_key, df)
        return df
    except Exception as e:
        print(f"❌ 获取 {code} 资金流数据失败: {e}")
        return pd.DataFrame()


def calculate_money_flow_score(df_flow: pd.DataFrame) -> float:
    """
    计算资金流评分

    评分规则：
    - 3日主力净流入占比 + 当日主力流入强度
    - 满分 20 分

    Args:
        df_flow: 资金流数据 DataFrame

    Returns:
        float: 评分 (0-20)
    """
    if df_flow.empty:
        return 0.0

    # 最近 3 日主力净流入（万元）
    recent_main_flow = df_flow['main_net_inflow'].head(3).sum()

    # 评分：每 1 亿流入得 20 分，封顶 20 分
    score = min(abs(recent_main_flow) / 10000 * 20, 20)

    return round(score, 2)


def sync_stock_money_flow(code: str, engine=None) -> bool:
    """
    同步单只股票的资金流数据到数据库

    Args:
        code: 股票代码
        engine: 数据库引擎

    Returns:
        bool: 是否成功
    """
    from sqlalchemy import text

    if engine is None:
        engine = get_db_engine()
    if not engine:
        return False

    try:
        # 获取最新资金流日期
        with engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT MAX(date) FROM money_flow_daily WHERE code='{code}'")
            )
            last_date = result.fetchone()[0]

        # 确定需要同步的日期范围
        if last_date:
            fetch_start = (last_date + timedelta(days=1)).strftime("%Y%m%d")
        else:
            fetch_start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

        today_str = datetime.now().strftime("%Y%m%d")
        if last_date and last_date.strftime("%Y%m%d") >= today_str:
            return True  # 已是最新

        # 调用 akshare 获取资金流数据
        df_flow = ak.stock_individual_fund_flow(
            stock=code,
            symbol="个股资金流"
        )

        if df_flow.empty:
            return True  # 无数据不算失败

        # 保存到数据库
        save_money_flow_to_db(df_flow, code, engine)
        return True

    except Exception as e:
        print(f"❌ 同步 {code} 资金流数据失败: {e}")
        return False


def save_money_flow_to_db(df_flow: pd.DataFrame, code: str, engine):
    """将资金流数据保存到数据库"""
    from sqlalchemy import text

    with engine.connect() as conn:
        for _, row in df_flow.iterrows():
            conn.execute(text("""
                INSERT INTO money_flow_daily
                (code, date, main_net_inflow, super_large_net, large_net, medium_net, small_net)
                VALUES (:code, :date, :main, :super, :large, :medium, :small)
                ON CONFLICT (code, date) DO UPDATE SET
                    main_net_inflow = EXCLUDED.main_net_inflow,
                    super_large_net = EXCLUDED.super_large_net,
                    large_net = EXCLUDED.large_net,
                    medium_net = EXCLUDED.medium_net,
                    small_net = EXCLUDED.small_net
            """), {
                'code': code,
                'date': pd.to_datetime(row['日期']).date(),
                'main': row.get('主力净流入', 0),
                'super': row.get('超大单净流入', 0),
                'large': row.get('大单净流入', 0),
                'medium': row.get('中单净流入', 0),
                'small': row.get('小单净流入', 0)
            })
        conn.commit()
```

### 4.2 策略接口修改 (`core/strategy.py`)

```python
def check_strategy(
    df,
    threshold=0.12,
    vol_multiplier=1.5,
    rsi_min=55,
    use_macd_filter=True,
    use_bb_sqz=False,
    sqz_lookback=10,
    use_rs_filter=True,
    use_money_flow_filter=False,  # 新增参数
    money_flow_days=3             # 新增参数
):
    """
    执行无门问禅：A股均线粘合战法（集成资金流向）

    新增参数:
    - use_money_flow_filter: 是否启用资金流过滤（默认 False）
    - money_flow_days: 资金流统计天数（默认 3 日）
    """

    # ... 现有的所有逻辑 ...

    # --- 新增：资金流向过滤 ---
    is_money_flow_ok = True
    recent_main_flow = 0

    if use_money_flow_filter:
        # 优先从 DataFrame 中读取（如果已包含资金流数据）
        if 'main_net_inflow' in df.columns:
            recent_main_flow = df['main_net_inflow'].iloc[-money_flow_days:].sum()
            curr_main_flow = df['main_net_inflow'].iloc[-1]

            # 判断条件：
            # 1. 最近 N 日主力净流入为正，或
            # 2. 当日主力大幅流入（> 1000 万元）
            is_money_flow_ok = (recent_main_flow > 0) or (curr_main_flow > 1000)
        else:
            # 如果 DataFrame 中无资金流数据，尝试从数据库获取
            code = df.iloc[-1].get('code', '')
            if code:
                df_flow = get_individual_fund_flow(code, days=money_flow_days)
                if not df_flow.empty:
                    recent_main_flow = df_flow['main_net_inflow'].sum()
                    is_money_flow_ok = (recent_main_flow > 0)
                else:
                    is_money_flow_ok = False  # 无数据时不通过
            else:
                is_money_flow_ok = False

    debug_info["is_money_flow_ok"] = is_money_flow_ok
    debug_info["main_flow_3d"] = round(recent_main_flow, 2) if use_money_flow_filter else 0

    # 综合判断中新增条件
    all_conditions = (
        was_squeeze_recent and
        is_breakout and
        is_ema20_ok and
        is_volume and
        is_rsi_ok and
        is_macd_ok and
        is_bb_ok and
        is_rs_ok and
        is_money_flow_ok  # 新增
    )

    if all_conditions:
        pct_change = (curr['收盘'] - prev['收盘']) / prev['收盘'] * 100
        body = abs(curr['收盘'] - curr['开盘'])
        upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
        shadow_ratio = round(upper_shadow / body, 2) if body > 0 else 0

        # 新权重：量能(20%) + 粘合(40%) + RSI(20%) + 资金流(20%)
        flow_score = 0
        if use_money_flow_filter:
            flow_score = calculate_money_flow_score(
                get_individual_fund_flow(df.iloc[-1].get('code', ''), days=money_flow_days)
            )

        score = (
            (vol_ratio * 20) +
            ((threshold - sqz_ratios.iloc[-1]) * 100 * 40) +
            (curr['RSI'] * 0.20) +
            flow_score
        )

        return True, {
            "Score": round(score, 2),
            "涨幅%": round(pct_change, 2),
            "现价": curr['收盘'],
            "代码": curr.get('code', 'N/A'),
            "名称": curr.get('name', 'N/A'),
            "粘合度": round(sqz_ratios.iloc[-1], 4),
            "RSI": round(curr['RSI'], 1),
            "DIF": round(curr['MACD_DIF'], 3),
            "BB": round(curr['BB_Width'], 4),
            "影线比": shadow_ratio,
            "主力净流入": round(recent_main_flow, 2) if use_money_flow_filter else None
        }

    # ... 现有的失败返回逻辑 ...
```

### 4.3 数据同步实现 (`sync_data.py`)

```python
def sync_stock_with_money_flow(code: str, name: str, engine=None) -> bool:
    """
    同步单只股票的 K 线 + 资金流数据

    Args:
        code: 股票代码
        name: 股票名称
        engine: 数据库引擎

    Returns:
        bool: 是否成功
    """
    from .money_flow import sync_stock_money_flow

    # 1. 同步 K 线数据（现有逻辑）
    success = sync_stock(code, name, engine)
    if not success:
        return False

    # 2. 同步资金流数据（新增）
    return sync_stock_money_flow(code, engine)


def sync_all_money_flow(workers: int = 5, force_full: bool = False):
    """
    批量同步所有股票的资金流数据

    Args:
        workers: 并发线程数
        force_full: 是否强制全量同步
    """
    from .db import get_all_stocks

    stock_list = get_all_stocks()

    print(f"🔄 开始同步 {len(stock_list)} 只股票的资金流数据...")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_code = {
            executor.submit(sync_stock_with_money_flow, s['code'], s['name']): s['code']
            for s in stock_list
        }

        completed = 0
        failed = 0

        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                result = future.result(timeout=30)
                if result:
                    completed += 1
                    print(f"✅ [{completed}/{len(stock_list)}] {code} 资金流数据同步完成")
                else:
                    failed += 1
                    print(f"⚠️ {code} 资金流同步失败")
            except Exception as e:
                failed += 1
                print(f"❌ {code} 资金流同步异常: {e}")

    print(f"\n📊 同步完成: 成功 {completed}, 失败 {failed}")


# 混合同步模式：扫描时按需同步
def ensure_money_flow_available(code: str, days: int = 5):
    """
    确保指定股票的资金流数据可用

    如果数据缺失或过期（> 24 小时），触发快速同步（最近 N 天）

    Args:
        code: 股票代码
        days: 同步天数
    """
    from sqlalchemy import text
    from .money_flow import get_individual_fund_flow, sync_stock_money_flow

    engine = get_db_engine()
    if not engine:
        return False

    try:
        # 检查数据库中最新的资金流数据日期
        with engine.connect() as conn:
            result = conn.execute(text(
                f"SELECT MAX(date) FROM money_flow_daily WHERE code='{code}'"
            ))
            last_date = result.fetchone()[0]

        # 判断是否需要同步
        need_sync = False
        if last_date is None:
            need_sync = True
        else:
            days_diff = (datetime.now().date() - last_date).days
            if days_diff > 1:  # 超过 1 天未更新
                need_sync = True

        if need_sync:
            print(f"🔄 {code} 资金流数据过期，触发快速同步...")
            sync_stock_money_flow(code, engine)

        return True

    except Exception as e:
        print(f"❌ 检查 {code} 资金流数据状态失败: {e}")
        return False
```

### 4.4 API 层修改 (`api.py`)

```python
@app.get("/api/scan")
def scan_stocks(
    code: Optional[str] = None,
    sector: Optional[str] = None,
    min_score: float = 60.0,
    min_volume: float = 1.5,
    use_money_flow: bool = False,  # 新增参数
):
    """
    执行市场扫描

    新增参数:
    - use_money_flow: 是否启用资金流向过滤
    """
    try:
        from core.strategy import check_strategy
        from core.money_flow import ensure_money_flow_available

        # 获取股票列表
        if code:
            stocks = [get_stock_info(code)]
        elif sector:
            stocks = get_stocks_by_sector(sector)
        else:
            stocks = get_all_stocks()

        results = []

        for stock in stocks:
            stock_code = stock['code']

            # 如果启用资金流过滤，确保数据可用
            if use_money_flow:
                ensure_money_flow_available(stock_code, days=5)

            # 获取 K 线数据
            df = get_stock_daily_data(stock_code)

            if df.empty or len(df) < 120:
                continue

            # 执行策略检查
            match, info = check_strategy(
                df,
                use_money_flow_filter=use_money_flow
            )

            if match:
                results.append(info)

        # 按评分排序
        results.sort(key=lambda x: x.get('Score', 0), reverse=True)

        return {
            "success": True,
            "data": results,
            "count": len(results)
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
```

---

## 5. 前端集成

### 5.1 API 客户端 (`frontend/lib/api.ts`)

```typescript
export interface ScanParams {
  code?: string;
  sector?: string;
  min_score?: number;
  min_volume?: number;
  use_money_flow?: boolean;  // 新增
}

export const scanMarket = async (params: ScanParams) => {
  const response = await axios.get('/api/scan', { params });
  return response.data;
};
```

### 5.2 筛选弹窗 (`frontend/components/FilterModal.tsx`)

```tsx
interface FilterState {
  minScore: number;
  minVolume: number;
  sector: string;
  useMoneyFlow: boolean;  // 新增
}

// JSX
<div className="flex items-center space-x-2">
  <input
    type="checkbox"
    id="moneyFlowFilter"
    checked={filters.useMoneyFlow}
    onChange={(e) => setFilters({
      ...filters,
      useMoneyFlow: e.target.checked
    })}
    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
  />
  <label htmlFor="moneyFlowFilter" className="text-sm font-medium text-gray-700">
    主力资金流入（近 3 日）
  </label>
</div>
```

### 5.3 结果表格 (`frontend/components/ResultsTable.tsx`)

```tsx
// 新增列头
<TableHead className="text-right">主力净流入(3日)</TableHead>

// 新增数据列
<TableCell className="text-right">
  {stock.main_flow_3d !== undefined ? (
    <span className={stock.main_flow_3d > 0 ? 'text-red-500' : 'text-green-500'}>
      {stock.main_flow_3d > 0 ? '+' : ''}{stock.main_flow_3d.toFixed(2)}万
    </span>
  ) : (
    <span className="text-gray-400">-</span>
  )}
</TableCell>
```

---

## 6. 错误处理与降级

### 6.1 数据获取失败降级

```python
# 在 check_strategy 中
if use_money_flow_filter:
    try:
        recent_main_flow = get_money_flow(code, days=3)
        if recent_main_flow is None:
            # 降级：不强制阻断，但记录警告
            is_money_flow_ok = True
            debug_info["money_flow_warning"] = "数据获取失败，已跳过资金流检查"
    except Exception as e:
        is_money_flow_ok = True
        debug_info["money_flow_error"] = str(e)
```

### 6.2 API 超时处理

```python
# 在 get_individual_fund_flow 中
try:
    df = ak.stock_individual_fund_flow(stock=code, symbol="个股资金流")
except TimeoutError:
    print(f"⚠️ 获取 {code} 资金流数据超时，使用缓存或返回空")
    return pd.DataFrame()
except Exception as e:
    print(f"❌ 获取 {code} 资金流数据失败: {e}")
    return pd.DataFrame()
```

---

## 7. 实施步骤

1. **数据库迁移**
   - 执行 SQL 扩展 `daily_k` 表
   - 创建 `money_flow_daily` 表

2. **后端开发**
   - 创建 `backend/core/money_flow.py` 模块
   - 修改 `core/strategy.py` 集成资金流过滤
   - 扩展 `sync_data.py` 实现混合同步
   - 修改 `api.py` 添加参数支持

3. **前端开发**
   - 扩展 `lib/api.ts` 扫描接口
   - 修改 `FilterModal.tsx` 添加筛选选项
   - 修改 `ResultsTable.tsx` 添加显示列

4. **测试验证**
   - 单元测试：资金流数据获取和计算
   - 集成测试：策略融合和评分
   - UI 测试：筛选和显示功能
   - 性能测试：同步效率和缓存效果

5. **部署上线**
   - 灰度发布：先在部分股票上测试
   - 监控指标：数据成功率、API 响应时间
   - 用户反馈收集和优化

---

## 8. 后续优化方向

### 8.1 数据质量提升
- 接入 Level-2 行情数据（付费，更精准）
- 添加资金流数据验证和异常检测

### 8.2 策略优化
- 机器学习模型预测资金流向趋势
- 结合板块资金流进行共振分析

### 8.3 功能扩展
- 资金流历史回测
- 资金流异动提醒
- 主力控盘度分析

---

## 9. 附录

### 9.1 akshare 接口参考

```python
# 个股资金流向
ak.stock_individual_fund_flow(stock="000001", symbol="个股资金流")

# 返回字段：
# - 日期
# - 主力净流入-净额
# - 超大单净流入-净额
# - 大单净流入-净额
# - 中单净流入-净额
# - 小单净流入-净额
# - 主力净流入-净占比
# - 超大单净流入-净占比
# - 大单净流入-净占比
# - 中单净流入-净占比
# - 小单净流入-净占比
```

### 9.2 相关文档

- 项目技术规格: `TECHNICAL_SPECS.md`
- 策略文档: `Stock_Strategy.md`
- API 文档: `http://127.0.0.1:8000/docs`

---

**文档版本历史**

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2025-01-15 | 初始版本 |
