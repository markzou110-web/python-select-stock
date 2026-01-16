# 资金流向分析功能实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**目标:** 为 Alpha Vision Pro 添加主力资金流向分析功能，将资金流数据融入现有的均线粘合策略，提升选股精准度。

**架构:** 采用模块化扩展方式，新增 `money_flow.py` 模块处理资金流数据获取和计算，扩展 `daily_k` 表和新建 `money_flow_daily` 表存储资金流历史，在策略检查函数中集成资金流过滤条件（可选启用），实现混合同步机制（定时全量 + 按需增量）。

**技术栈:** Python 3.9+, FastAPI, PostgreSQL, Next.js 16, TypeScript, akshare, SQLAlchemy

---

## 前置准备

### Task 0: 环境验证与依赖检查

**目标:** 确保开发环境就绪，数据库连接正常

**Step 1: 验证 Python 环境和依赖**

```bash
cd backend
python3 --version  # 应该 >= 3.9
python3 -m pip list | grep akshare
```

Expected: `akshare` 已安装

**Step 2: 验证数据库连接**

```bash
cd backend
python3 -c "from core.db import get_db_engine; print(get_db_engine())"
```

Expected: 输出数据库引擎对象，无错误

**Step 3: 验证前端环境**

```bash
cd frontend
npm list --depth=0 | grep axios
npm run dev  # 在另一个终端测试服务是否启动
```

Expected: `axios` 已安装，dev server 启动成功

---

## 阶段一：数据库迁移

### Task 1: 扩展 daily_k 表结构

**目标:** 在现有的 `daily_k` 表中添加资金流相关字段

**Files:**
- Modify: `backend/core/db.py`
- Test: 手动验证

**Step 1: 定位 init_db 函数**

打开 `backend/core/db.py`，找到 `daily_k` 表的定义部分

**Step 2: 在 daily_k 表定义中添加新字段**

找到 `daily_k` 表的 CREATE TABLE 语句或 SQLAlchemy 模型定义，添加：

```python
# 在现有字段后添加
Column('main_net_inflow', Numeric(15, 2)),  # 主力净流入（万元）
Column('super_large_net', Numeric(15, 2)),  # 超大单净流入
Column('large_net', Numeric(15, 2)),        # 大单净流入
Column('medium_net', Numeric(15, 2)),       # 中单净流入
Column('small_net', Numeric(15, 2)),        # 小单净流入
```

如果使用原生 SQL，在 `init_db()` 函数中添加 ALTER TABLE 语句：

```python
# 在 init_db() 函数中，daily_k 表创建后添加
try:
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE daily_k
            ADD COLUMN IF NOT EXISTS main_net_inflow NUMERIC(15, 2);
            ALTER TABLE daily_k
            ADD COLUMN IF NOT EXISTS super_large_net NUMERIC(15, 2);
            ALTER TABLE daily_k
            ADD COLUMN IF NOT EXISTS large_net NUMERIC(15, 2);
            ALTER TABLE daily_k
            ADD COLUMN IF NOT EXISTS medium_net NUMERIC(15, 2);
            ALTER TABLE daily_k
            ADD COLUMN IF NOT EXISTS small_net NUMERIC(15, 2);
        """))
        conn.commit()
        print("✅ daily_k 表字段扩展完成")
except Exception as e:
    print(f"⚠️ daily_k 表字段扩展失败: {e}")
```

**Step 3: 测试数据库迁移**

```bash
cd backend
python3 -c "
from core.db import get_db_engine
from sqlalchemy import text

engine = get_db_engine()
with engine.connect() as conn:
    result = conn.execute(text(\"\"\"
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'daily_k'
        AND column_name IN ('main_net_inflow', 'super_large_net', 'large_net', 'medium_net', 'small_net')
    \"\"\"))
    print('新增字段:', [row[0] for row in result])
"
```

Expected: 输出包含所有 5 个新字段名

**Step 4: 提交更改**

```bash
git add backend/core/db.py
git commit -m "feat(db): extend daily_k table with money flow fields

- Add main_net_inflow, super_large_net, large_net, medium_net, small_net columns
- Support capital flow data storage in daily_k table
"
```

---

### Task 2: 创建 money_flow_daily 表

**目标:** 创建专门的资金流历史数据表

**Files:**
- Modify: `backend/core/db.py` (在 `init_db()` 函数中)
- Test: 手动验证

**Step 1: 在 init_db() 函数中添加 money_flow_daily 表创建逻辑**

在 `backend/core/db.py` 的 `init_db()` 函数中，添加：

```python
# 创建 money_flow_daily 表
try:
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS money_flow_daily (
                id SERIAL PRIMARY KEY,
                code VARCHAR(10),
                date DATE,
                main_net_inflow NUMERIC(15, 2),
                super_large_net NUMERIC(15, 2),
                large_net NUMERIC(15, 2),
                medium_net NUMERIC(15, 2),
                small_net NUMERIC(15, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, date)
            );
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_money_flow_code_date
            ON money_flow_daily(code, date);
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_money_flow_date
            ON money_flow_daily(date);
        """))
        conn.commit()
        print("✅ money_flow_daily 表创建完成")
except Exception as e:
    print(f"⚠️ money_flow_daily 表创建失败: {e}")
```

**Step 2: 验证表创建**

```bash
cd backend
python3 -c "
from core.db import get_db_engine
from sqlalchemy import text

engine = get_db_engine()
with engine.connect() as conn:
    result = conn.execute(text(\"\"\"
        SELECT table_name, indexname
        FROM pg_indexes
        WHERE table_name = 'money_flow_daily'
    \"\"\"))
    print('money_flow_daily 索引:', [row[1] for row in result])
"
```

Expected: 输出至少包含 `idx_money_flow_code_date` 和 `idx_money_flow_date`

**Step 3: 提交更改**

```bash
git add backend/core/db.py
git commit -m "feat(db): create money_flow_daily table

- Add table for storing detailed capital flow history
- Create indexes on (code, date) and date for query performance
- Support upsert operation via UNIQUE constraint
"
```

---

## 阶段二：后端核心模块开发

### Task 3: 创建 money_flow.py 模块 - 数据获取函数

**目标:** 实现资金流数据获取的核心函数

**Files:**
- Create: `backend/core/money_flow.py`
- Test: 手动测试

**Step 1: 创建模块文件并导入依赖**

```bash
touch backend/core/money_flow.py
```

编辑 `backend/core/money_flow.py`，添加：

```python
"""
资金流向分析模块

提供个股资金流向数据获取、计算和存储功能
"""

import akshare as ak
import pandas as pd
import time
import random
from datetime import datetime, timedelta
from functools import lru_cache
from .data import get_cached_data, set_cached_data


def get_individual_fund_flow(code: str, days: int = 5) -> pd.DataFrame:
    """
    获取个股最近 N 天的资金流向数据

    Args:
        code: 股票代码（如 '000001'）
        days: 查询天数

    Returns:
        DataFrame with columns:
        - date: 日期
        - main_net_inflow: 主力净流入（万元）
        - super_large_net: 超大单净流入
        - large_net: 大单净流入
        - medium_net: 中单净流入
        - small_net: 小单净流入

        如果获取失败返回空 DataFrame
    """
    cache_key = f'money_flow_{code}_{days}'
    cached = get_cached_data(cache_key, 600)  # 10分钟缓存
    if cached is not None:
        return cached

    max_retries = 2
    for attempt in range(max_retries):
        try:
            # 添加随机延迟避免请求过于集中
            time.sleep(random.uniform(0.3, 0.8))

            df = ak.stock_individual_fund_flow(
                stock=code,
                symbol="个股资金流"
            )

            if df.empty:
                return pd.DataFrame()

            # 数据清洗和重命名
            df = df.head(days)

            # 标准化列名
            column_mapping = {
                '日期': 'date',
                '主力净流入-净额': 'main_net_inflow',
                '超大单净流入-净额': 'super_large_net',
                '大单净流入-净额': 'large_net',
                '中单净流入-净额': 'medium_net',
                '小单净流入-净额': 'small_net'
            }

            # 只保留需要的列
            available_columns = {k: v for k, v in column_mapping.items() if k in df.columns}
            df = df[list(available_columns.keys())].rename(columns=available_columns)

            # 转换日期格式
            df['date'] = pd.to_datetime(df['date'])

            # 确保数值类型
            numeric_columns = ['main_net_inflow', 'super_large_net', 'large_net', 'medium_net', 'small_net']
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

            # 缓存结果
            set_cached_data(cache_key, df)

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            print(f"❌ 获取 {code} 资金流数据失败: {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def calculate_money_flow_score(df_flow: pd.DataFrame) -> float:
    """
    计算资金流评分

    评分规则：3日主力净流入每1亿得20分，封顶20分

    Args:
        df_flow: 资金流数据 DataFrame

    Returns:
        float: 评分 (0-20)
    """
    if df_flow.empty or 'main_net_inflow' not in df_flow.columns:
        return 0.0

    # 最近 3 日主力净流入（万元）
    recent_main_flow = df_flow['main_net_inflow'].head(3).sum()

    # 评分：每 1 亿流入得 20 分，封顶 20 分
    score = min(abs(recent_main_flow) / 10000 * 20, 20)

    return round(score, 2)
```

**Step 2: 测试数据获取函数**

```bash
cd backend
python3 -c "
from core.money_flow import get_individual_fund_flow, calculate_money_flow_score

# 测试获取平安银行（000001）的资金流数据
df = get_individual_fund_flow('000001', days=5)
print('数据形状:', df.shape)
print('列名:', df.columns.tolist())
print('\\n前3行数据:')
print(df.head(3))

# 测试评分计算
score = calculate_money_flow_score(df)
print('\\n资金流评分:', score)
"
```

Expected: 输出包含日期和资金流数据的 DataFrame，评分在 0-20 之间

**Step 3: 提交初始模块**

```bash
git add backend/core/money_flow.py
git commit -m "feat(money-flow): add data fetching functions

- Implement get_individual_fund_flow() with caching
- Add calculate_money_flow_score() for strategy scoring
- Support retry logic and error handling
- Use akshare stock_individual_fund_flow API
"
```

---

### Task 4: 实现数据库存储函数

**目标:** 添加资金流数据存储到数据库的函数

**Files:**
- Modify: `backend/core/money_flow.py`
- Test: 手动测试

**Step 1: 添加数据库导入和保存函数**

在 `backend/core/money_flow.py` 顶部添加导入：

```python
from .db import get_db_engine
```

在文件末尾添加：

```python
def save_money_flow_to_db(df_flow: pd.DataFrame, code: str, engine):
    """
    将资金流数据保存到数据库

    使用 UPSERT 机制：如果记录已存在则更新，否则插入

    Args:
        df_flow: 资金流数据 DataFrame
        code: 股票代码
        engine: 数据库引擎
    """
    from sqlalchemy import text

    if df_flow.empty:
        return

    try:
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
                    'date': row['date'].date() if hasattr(row['date'], 'date') else pd.to_datetime(row['date']).date(),
                    'main': float(row.get('main_net_inflow', 0)),
                    'super': float(row.get('super_large_net', 0)),
                    'large': float(row.get('large_net', 0)),
                    'medium': float(row.get('medium_net', 0)),
                    'small': float(row.get('small_net', 0))
                })
            conn.commit()
            print(f"✅ {code} 资金流数据已保存到数据库 ({len(df_flow)} 条记录)")
    except Exception as e:
        print(f"❌ 保存 {code} 资金流数据失败: {e}")
        raise


def sync_stock_money_flow(code: str, engine=None) -> bool:
    """
    同步单只股票的资金流数据到数据库

    Args:
        code: 股票代码
        engine: 数据库引擎（可选，默认使用全局引擎）

    Returns:
        bool: 是否成功
    """
    from sqlalchemy import text

    if engine is None:
        engine = get_db_engine()
    if not engine:
        print("❌ 无法获取数据库引擎")
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
            days_to_sync = (datetime.now().date() - last_date).days
        else:
            fetch_start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            days_to_sync = 90

        today_str = datetime.now().strftime("%Y%m%d")
        if last_date and last_date.strftime("%Y%m%d") >= today_str:
            # 已经是最新数据
            return True

        # 调用 akshare 获取资金流数据
        df_flow = get_individual_fund_flow(code, days=days_to_sync)

        if df_flow.empty:
            print(f"⚠️ {code} 无资金流数据")
            return True  # 无数据不算失败

        # 保存到数据库
        save_money_flow_to_db(df_flow, code, engine)
        return True

    except Exception as e:
        print(f"❌ 同步 {code} 资金流数据失败: {e}")
        return False
```

**Step 2: 测试数据库存储**

```bash
cd backend
python3 -c "
from core.money_flow import get_individual_fund_flow, sync_stock_money_flow
from core.db import get_db_engine

# 获取测试数据
df = get_individual_fund_flow('000001', days=3)
print('获取到的数据:', df.shape)

# 保存到数据库
engine = get_db_engine()
sync_stock_money_flow('000001', engine)

# 验证数据已保存
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text(\"\"\"
        SELECT code, date, main_net_inflow
        FROM money_flow_daily
        WHERE code='000001'
        ORDER BY date DESC
        LIMIT 3
    \"\"\"))
    print('\\n数据库中的记录:')
    for row in result:
        print(f'  {row[0]} {row[1]} 主力净流入: {row[2]}')
"
```

Expected: 输出显示数据已成功保存到数据库

**Step 3: 提交更改**

```bash
git add backend/core/money_flow.py
git commit -m "feat(money-flow): add database storage functions

- Implement save_money_flow_to_db() with UPSERT support
- Add sync_stock_money_flow() for incremental sync
- Handle date range calculation automatically
- Add error handling and logging
"
```

---

### Task 5: 实现按需同步函数

**目标:** 在扫描时自动检查并同步过期的资金流数据

**Files:**
- Modify: `backend/core/money_flow.py`
- Test: 手动测试

**Step 1: 添加按需同步函数**

在 `backend/core/money_flow.py` 文件末尾添加：

```python
def ensure_money_flow_available(code: str, days: int = 5) -> bool:
    """
    确保指定股票的资金流数据可用

    如果数据缺失或过期（> 24 小时），触发快速同步（最近 N 天）

    Args:
        code: 股票代码
        days: 同步天数

    Returns:
        bool: 是否数据可用
    """
    from sqlalchemy import text

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
            # 无历史数据，需要同步
            need_sync = True
            print(f"🔄 {code} 无资金流历史数据")
        else:
            days_diff = (datetime.now().date() - last_date).days
            if days_diff > 1:  # 超过 1 天未更新
                need_sync = True
                print(f"🔄 {code} 资金流数据过期 ({days_diff} 天前)")

        if need_sync:
            sync_stock_money_flow(code, engine)

        return True

    except Exception as e:
        print(f"❌ 检查 {code} 资金流数据状态失败: {e}")
        return False
```

**Step 2: 测试按需同步**

```bash
cd backend
python3 -c "
from core.money_flow import ensure_money_flow_available

# 测试确保数据可用
success = ensure_money_flow_available('000002', days=5)
print('数据可用:', success)
"
```

Expected: 输出显示数据已确保可用（可能触发同步）

**Step 3: 提交更改**

```bash
git add backend/core/money_flow.py
git commit -m "feat(money-flow): add on-demand sync function

- Implement ensure_money_flow_available() for hybrid sync
- Check data freshness and trigger sync if needed (>24h)
- Support automatic refresh during scanning
"
```

---

### Task 6: 集成资金流过滤到策略函数

**目标:** 修改 `check_strategy()` 函数，添加资金流过滤条件

**Files:**
- Modify: `backend/core/strategy.py`
- Test: 手动测试

**Step 1: 在文件顶部添加导入**

在 `backend/core/strategy.py` 顶部添加：

```python
from .money_flow import get_individual_fund_flow, calculate_money_flow_score
```

**Step 2: 修改 check_strategy 函数签名**

找到 `def check_strategy()` 函数定义，修改为：

```python
def check_strategy(df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, use_macd_filter=True, use_bb_sqz=False, sqz_lookback=10, use_rs_filter=True, use_money_flow_filter=False, money_flow_days=3):
    """执行无门问禅：A股均线粘合战法 (Pine Script v5.0 Alignment)

    新增参数:
    - use_money_flow_filter: 是否启用资金流过滤（默认 False 保持向后兼容）
    - money_flow_days: 资金流统计天数（默认 3 日）
    """
```

**Step 3: 在策略逻辑中添加资金流检查**

在 `backend/core/strategy.py` 的 `check_strategy()` 函数中，找到 MACD 检查部分（约第 35 行），在其后添加：

```python
    # --- 7. 资金流向过滤 (新增) ---
    is_money_flow_ok = True
    recent_main_flow = 0

    if use_money_flow_filter:
        code = df.iloc[-1].get('code', '')

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
            if code:
                df_flow = get_individual_fund_flow(code, days=money_flow_days)
                if not df_flow.empty:
                    recent_main_flow = df_flow['main_net_inflow'].sum()
                    is_money_flow_ok = (recent_main_flow > 0)
                else:
                    # 无数据时不通过
                    is_money_flow_ok = False
            else:
                is_money_flow_ok = False
```

**Step 4: 在 debug_info 中添加资金流信息**

找到 `debug_info` 字典定义（约第 52 行），添加：

```python
    debug_info = {
        "squeeze": round(sqz_ratios.iloc[-1], 4),
        "vol_ratio": round(vol_ratio, 2),
        "rsi": round(curr['RSI'], 1),
        "is_breakout": is_breakout,
        "is_volume": is_volume,
        "is_rsi_ok": is_rsi_ok,
        "is_macd_ok": is_macd_ok,
        "is_bb_ok": is_bb_ok,
        "was_sqz_recent": was_squeeze_recent,
        "is_rs_ok": is_rs_ok,
        "is_money_flow_ok": is_money_flow_ok,  # 新增
        "main_flow_3d": round(recent_main_flow, 2) if use_money_flow_filter else 0  # 新增
    }
```

**Step 5: 修改综合判断条件**

找到 `if was_squeeze_recent and is_breakout ...` 这一行（约第 73 行），修改为：

```python
    if was_squeeze_recent and is_breakout and is_ema20_ok and is_volume and is_rsi_ok and is_macd_ok and is_bb_ok and is_rs_ok and is_money_flow_ok:
```

**Step 6: 修改评分计算（启用资金流时）**

找到评分计算部分（约第 82 行），修改为：

```python
        # SOP 评分权重调整：启用资金流时使用新权重
        flow_score = 0
        if use_money_flow_filter:
            # 新权重：量能(20%) + 粘合(40%) + RSI(20%) + 资金流(20%)
            if 'main_net_inflow' in df.columns:
                recent_flow_for_score = df['main_net_inflow'].iloc[-money_flow_days:].sum()
            else:
                df_flow = get_individual_fund_flow(df.iloc[-1].get('code', ''), days=money_flow_days)
                recent_flow_for_score = df_flow['main_net_inflow'].sum() if not df_flow.empty else 0

            flow_score = calculate_money_flow_score(
                pd.DataFrame({'main_net_inflow': [recent_flow_for_score]})
            )
            score = (vol_ratio * 20) + ((threshold - sqz_ratios.iloc[-1]) * 100 * 40) + (curr['RSI'] * 0.20) + flow_score
        else:
            # 原始权重：量能(25%) + 粘合(50%) + RSI(25%)
            score = (vol_ratio * 25) + ((threshold - sqz_ratios.iloc[-1]) * 100 * 50) + (curr['RSI'] * 0.25)

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
```

**Step 7: 在失败原因中添加资金流判断**

找到失败原因列表（约第 96 行），添加：

```python
    # 详细失败原因 (SOP 术语)
    reasons = []
    if not is_breakout: reasons.append("未突破均线簇")
    if not is_volume: reasons.append("量能未爆发")
    if not is_rsi_ok: reasons.append("强度不足(RSI)")
    if not is_macd_ok: reasons.append("MACD未金叉")
    if not is_bb_ok: reasons.append("布林带未收缩")
    if not is_rs_ok: reasons.append("弱于大盘(RS)")
    if use_money_flow_filter and not is_money_flow_ok: reasons.append("主力资金流出")  # 新增

    debug_info["reason"] = ",".join(reasons) if reasons else "多因子未共振"
    return False, debug_info
```

**Step 8: 测试策略集成**

```bash
cd backend
python3 -c "
from core.data import get_stock_daily_data
from core.strategy import check_strategy

# 获取测试数据
df = get_stock_daily_data('000001')
print('数据长度:', len(df))

# 测试不启用资金流过滤
match1, info1 = check_strategy(df)
print('\\n不启用资金流过滤:', match1, info1.get('reason', 'Pass'))

# 测试启用资金流过滤
match2, info2 = check_strategy(df, use_money_flow_filter=True)
print('启用资金流过滤:', match2, info2.get('reason', 'Pass'))
if match2:
    print('评分:', info2.get('Score'))
    print('主力净流入:', info2.get('主力净流入'))
"
```

Expected: 策略函数能够正常运行，根据资金流情况返回不同结果

**Step 9: 提交更改**

```bash
git add backend/core/strategy.py
git commit -m "feat(strategy): integrate money flow filter

- Add use_money_flow_filter and money_flow_days parameters
- Check main capital inflow for recent N days
- Adjust scoring weights when money flow filter enabled
- Add money flow status to debug_info
- Include money flow in failure reasons
"
```

---

### Task 7: 扩展 sync_data.py 实现混合同步

**目标:** 在数据同步脚本中添加资金流同步功能

**Files:**
- Modify: `backend/sync_data.py`
- Test: 手动测试

**Step 1: 添加导入**

在 `backend/sync_data.py` 顶部添加：

```python
from core.money_flow import sync_stock_money_flow, ensure_money_flow_available
```

**Step 2: 添加综合同步函数**

在 `backend/sync_data.py` 文件末尾添加：

```python
def sync_stock_with_money_flow(code, name, engine=None):
    """同步单只股票的 K 线 + 资金流数据"""
    # 1. 同步 K 线数据（现有逻辑）
    success = sync_stock(code, name, engine)
    if not success:
        return False

    # 2. 同步资金流数据（新增）
    return sync_stock_money_flow(code, engine)


def sync_all_money_flow(workers=5, force_full=False):
    """批量同步所有股票的资金流数据

    Args:
        workers: 并发线程数
        force_full: 是否强制全量同步
    """
    from core.db import get_stock_basic_map

    stock_map = get_stock_basic_map()
    if not stock_map:
        print("❌ 无法获取股票列表")
        return

    stock_list = [{'code': k, 'name': v} for k, v in stock_map.items()]
    print(f"🔄 开始同步 {len(stock_list)} 只股票的资金流数据...")

    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_code = {
            executor.submit(sync_stock_with_money_flow, s['code'], s['name']): s['code']
            for s in stock_list
        }

        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                result = future.result(timeout=30)
                if result:
                    completed += 1
                    if completed % 10 == 0:
                        print(f"✅ 进度: {completed}/{len(stock_list)}")
                else:
                    failed += 1
                    print(f"⚠️ {code} 资金流同步失败")
            except Exception as e:
                failed += 1
                print(f"❌ {code} 资金流同步异常: {e}")

    print(f"\n📊 同步完成: 成功 {completed}, 失败 {failed}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="同步资金流数据")
    parser.add_argument("--money-flow", action="store_true", help="同步资金流数据")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数")
    args = parser.parse_args()

    if args.money_flow:
        sync_all_money_flow(workers=args.workers)
    else:
        # 原有的同步逻辑
        print("使用 --money-flow 参数同步资金流数据")
```

**Step 3: 测试混合同步**

```bash
cd backend
# 测试单只股票同步
python3 -c "
from sync_data import sync_stock_with_money_flow
result = sync_stock_with_money_flow('000001', '平安银行')
print('同步结果:', result)
"

# 测试批量同步（可选，仅测试前 10 只股票）
python3 sync_data.py --money-flow --workers 2  # 注意：会同步所有股票
```

Expected: 资金流数据能够成功同步

**Step 4: 提交更改**

```bash
git add backend/sync_data.py
git commit -m "feat(sync): add money flow sync support

- Add sync_stock_with_money_flow() for combined sync
- Implement sync_all_money_flow() for batch operations
- Support concurrent sync with ThreadPoolExecutor
- Add CLI argument --money-flow for triggering sync
"
```

---

### Task 8: 修改 API 层添加参数支持

**目标:** 在扫描接口中添加资金流过滤参数

**Files:**
- Modify: `backend/api.py`
- Test: 手动测试 + Swagger UI

**Step 1: 找到 /api/scan 接口**

在 `backend/api.py` 中找到 `/api/scan` 路由函数

**Step 2: 添加资金流参数**

在函数签名中添加：

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

    参数:
    - code: 股票代码（可选）
    - sector: 行业板块（可选）
    - min_score: 最低评分
    - min_volume: 最小量比
    - use_money_flow: 是否启用资金流向过滤（新增）
    """
```

**Step 3: 在扫描逻辑中集成按需同步**

在扫描循环中，添加按需同步调用：

```python
        results = []

        for stock in stocks:
            stock_code = stock['code']

            # 如果启用资金流过滤，确保数据可用（新增）
            if use_money_flow:
                ensure_money_flow_available(stock_code, days=5)

            # 获取 K 线数据
            df = get_stock_daily_data(stock_code)
            if df.empty or len(df) < 120:
                continue

            # 执行策略检查（传递资金流参数）
            match, info = check_strategy(
                df,
                use_money_flow_filter=use_money_flow
            )

            if match:
                results.append(info)
```

**Step 4: 测试 API**

```bash
# 启动后端服务
cd backend
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload

# 在另一个终端测试
curl "http://127.0.0.1:8000/api/scan?min_score=50&use_money_flow=true&limit=5"
```

Expected: 返回包含资金流过滤结果的 JSON

**Step 5: 验证 Swagger UI**

打开浏览器访问 `http://127.0.0.1:8000/docs`，检查 `/api/scan` 接口是否显示新的 `use_money_flow` 参数

**Step 6: 提交更改**

```bash
git add backend/api.py
git commit -m "feat(api): add money flow filter parameter to /api/scan

- Add use_money_flow query parameter
- Trigger on-demand sync when money flow filter enabled
- Pass parameter to strategy check function
- Update API documentation
"
```

---

## 阶段三：前端集成

### Task 9: 扩展前端 API 客户端

**目标:** 在前端 API 调用中添加资金流参数

**Files:**
- Modify: `frontend/lib/api.ts`
- Test: 手动测试

**Step 1: 修改 ScanParams 接口**

在 `frontend/lib/api.ts` 中找到扫描相关的接口定义，添加：

```typescript
export interface ScanParams {
  code?: string;
  sector?: string;
  min_score?: number;
  min_volume?: number;
  use_money_flow?: boolean;  // 新增
}
```

**Step 2: 修改 scanMarket 函数**

确保 `scanMarket` 函数支持新参数：

```typescript
export const scanMarket = async (params: ScanParams) => {
  try {
    const response = await axios.get('/api/scan', { params });
    return response.data;
  } catch (error) {
    console.error('扫描市场失败:', error);
    throw error;
  }
};
```

**Step 3: 提交更改**

```bash
git add frontend/lib/api.ts
git commit -m "feat(api): add use_money_flow parameter to scanMarket

- Extend ScanParams interface with use_money_flow flag
- Support passing money flow filter to backend API
"
```

---

### Task 10: 修改筛选弹窗组件

**目标:** 在筛选弹窗中添加资金流过滤选项

**Files:**
- Modify: `frontend/components/FilterModal.tsx`
- Test: 手动 UI 测试

**Step 1: 找到 FilterModal 组件**

打开 `frontend/components/FilterModal.tsx`

**Step 2: 在 FilterState 接口中添加字段**

找到状态定义，添加：

```typescript
interface FilterState {
  minScore: number;
  minVolume: number;
  sector: string;
  useMoneyFlow: boolean;  // 新增
}
```

**Step 3: 在状态初始化中添加默认值**

```typescript
const [filters, setFilters] = useState<FilterState>({
  minScore: 60,
  minVolume: 1.5,
  sector: '',
  useMoneyFlow: false,  // 新增
});
```

**Step 4: 在 JSX 中添加复选框**

在筛选条件的 JSX 部分添加：

```tsx
<div className="flex items-center space-x-2 mb-4">
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

**Step 5: 测试 UI**

```bash
cd frontend
npm run dev
```

打开浏览器 `http://localhost:3000`，点击筛选按钮，验证是否显示新的复选框

**Step 6: 提交更改**

```bash
git add frontend/components/FilterModal.tsx
git commit -m "feat(ui): add money flow filter checkbox to FilterModal

- Add useMoneyFlow to FilterState
- Display checkbox for main capital inflow filter
- Pass filter state to parent component
"
```

---

### Task 11: 修改结果表格组件

**目标:** 在结果表格中显示资金流数据列

**Files:**
- Modify: `frontend/components/ResultsTable.tsx`
- Test: 手动 UI 测试

**Step 1: 找到表头定义**

在 `frontend/components/ResultsTable.tsx` 中找到表头部分，添加：

```tsx
<TableHead className="text-right">主力净流入(3日)</TableHead>
```

**Step 2: 在数据行中添加资金流列**

在数据行的 JSX 中添加：

```tsx
<TableCell className="text-right">
  {stock.main_flow_3d !== undefined ? (
    <span className={stock.main_flow_3d > 0 ? 'text-red-500 font-semibold' : 'text-green-500'}>
      {stock.main_flow_3d > 0 ? '+' : ''}{stock.main_flow_3d.toFixed(2)}万
    </span>
  ) : (
    <span className="text-gray-400">-</span>
  )}
</TableCell>
```

**注意:** 如果 `main_flow_3d` 字段名不一致，需要根据实际返回的字段名调整。也可能是 `主力净流入`。

**Step 3: 测试显示**

启动前端并启用资金流过滤，验证表格是否正确显示资金流数据

**Step 4: 提交更改**

```bash
git add frontend/components/ResultsTable.tsx
git commit -m "feat(ui): add money flow column to ResultsTable

- Add table header for 3-day main capital inflow
- Display inflow amount with color coding (red/green)
- Handle missing data with dash placeholder
"
```

---

### Task 12: 集成 Dashboard 主组件

**目标:** 确保主组件正确传递资金流参数

**Files:**
- Modify: `frontend/app/page.tsx` 或 `frontend/components/Dashboard.tsx`
- Test: 端到端测试

**Step 1: 找到扫描调用逻辑**

在主组件中找到调用 `scanMarket` 的地方

**Step 2: 确保传递资金流参数**

```typescript
const handleScan = async () => {
  try {
    const result = await scanMarket({
      min_score: filters.minScore,
      min_volume: filters.minVolume,
      sector: filters.sector || undefined,
      use_money_flow: filters.useMoneyFlow,  // 新增
    });
    // ... 处理结果
  } catch (error) {
    console.error('扫描失败:', error);
  }
};
```

**Step 3: 测试完整流程**

1. 启动后端服务
2. 启动前端服务
3. 打开浏览器访问应用
4. 勾选"主力资金流入"复选框
5. 点击扫描
6. 验证结果是否显示资金流数据

**Step 4: 提交更改**

```bash
git add frontend/app/page.tsx  # 或 frontend/components/Dashboard.tsx
git commit -m "feat(ui): integrate money flow filter in Dashboard

- Pass useMoneyFlow parameter to scanMarket
- Ensure filter state flows through scan pipeline
"
```

---

## 阶段四：测试与优化

### Task 13: 编写单元测试

**目标:** 为核心函数编写测试

**Files:**
- Create: `backend/tests/test_money_flow.py` (如果 tests 目录不存在则创建)
- Test: `pytest`

**Step 1: 创建测试文件**

```bash
mkdir -p backend/tests
touch backend/tests/test_money_flow.py
```

**Step 2: 编写测试用例**

编辑 `backend/tests/test_money_flow.py`：

```python
"""
资金流模块单元测试
"""

import pytest
import pandas as pd
from core.money_flow import (
    get_individual_fund_flow,
    calculate_money_flow_score,
    save_money_flow_to_db
)
from core.db import get_db_engine
from sqlalchemy import text


def test_calculate_money_flow_score_empty_dataframe():
    """测试空 DataFrame 的评分计算"""
    df = pd.DataFrame()
    score = calculate_money_flow_score(df)
    assert score == 0.0


def test_calculate_money_flow_score_positive_flow():
    """测试正向资金流的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [5000, 3000, 2000]  # 总计 1 亿
    })
    score = calculate_money_flow_score(df)
    assert score == 20.0  # 1 亿 = 20 分（封顶）


def test_calculate_money_flow_score_negative_flow():
    """测试负向资金流的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [-5000, -3000, -2000]  # 总计 -1 亿
    })
    score = calculate_money_flow_score(df)
    assert score == 20.0  # 绝对值 1 亿 = 20 分


def test_calculate_money_flow_score_partial():
    """测试部分流入的评分计算"""
    df = pd.DataFrame({
        'main_net_inflow': [3000, 2000, 1000]  # 总计 6000 万
    })
    score = calculate_money_flow_score(df)
    assert score == 12.0  # 6000 万 / 10000 * 20 = 12 分


@pytest.mark.integration
def test_save_money_flow_to_db():
    """测试数据库保存功能（集成测试）"""
    engine = get_db_engine()
    if not engine:
        pytest.skip("无法连接数据库")

    # 准备测试数据
    df = pd.DataFrame({
        'date': [pd.Timestamp('2025-01-15')],
        'main_net_inflow': [5000.0],
        'super_large_net': [3000.0],
        'large_net': [2000.0],
        'medium_net': [-1000.0],
        'small_net': [-500.0]
    })

    # 保存到数据库
    save_money_flow_to_db(df, '999999', engine)

    # 验证数据
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT main_net_inflow FROM money_flow_daily
            WHERE code='999999' AND date='2025-01-15'
        """))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 5000.0

    # 清理测试数据
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM money_flow_daily WHERE code='999999'"))
        conn.commit()


@pytest.mark.external
def test_get_individual_fund_flow_real():
    """测试真实数据获取（外部 API 测试）"""
    df = get_individual_fund_flow('000001', days=3)

    # 验证返回结构
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        assert 'date' in df.columns
        assert 'main_net_inflow' in df.columns
        assert len(df) <= 3
```

**Step 3: 安装 pytest（如果未安装）**

```bash
cd backend
python3 -m pip install pytest pytest-mock
```

**Step 4: 运行测试**

```bash
cd backend
# 运行所有测试
pytest tests/test_money_flow.py -v

# 只运行单元测试（跳过集成测试）
pytest tests/test_money_flow.py -v -m "not integration and not external"
```

Expected: 单元测试通过，集成测试可能需要数据库连接

**Step 5: 提交测试文件**

```bash
git add backend/tests/test_money_flow.py
git add backend/requirements.txt  # 如果添加了 pytest
git commit -m "test(money-flow): add unit tests for money flow module

- Test calculate_money_flow_score() with various inputs
- Add integration test for database operations
- Include external API test (marked as optional)
- Validate scoring logic and data persistence
"
```

---

### Task 14: 端到端测试

**目标:** 验证整个功能从前端到后端的完整流程

**Files:**
- 无新增文件
- Test: 手动端到端测试

**Step 1: 准备测试环境**

```bash
# 启动后端
cd backend
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload

# 启动前端（另一个终端）
cd frontend
npm run dev
```

**Step 2: 执行端到端测试流程**

1. **测试场景 1: 不启用资金流过滤**
   - 打开浏览器访问 `http://localhost:3000`
   - 确保"主力资金流入"复选框未勾选
   - 点击"扫描市场"按钮
   - 验证：扫描结果正常显示，表格中"主力净流入(3日)"列显示"-"

2. **测试场景 2: 启用资金流过滤**
   - 勾选"主力资金流入"复选框
   - 点击"扫描市场"按钮
   - 验证：扫描结果可能减少，表格中显示资金流数据

3. **测试场景 3: 查看资金流数据详情**
   - 在启用资金流过滤的情况下
   - 验证：资金流为正的股票显示红色，为负的显示绿色
   - 验证：数值格式正确（带"+"号、小数点后2位）

4. **测试场景 4: API 直接调用**
   ```bash
   curl "http://127.0.0.1:8000/api/scan?min_score=50&use_money_flow=false"
   curl "http://127.0.0.1:8000/api/scan?min_score=50&use_money_flow=true"
   ```
   验证：两次调用结果不同（启用资金流过滤后结果更少）

**Step 3: 性能测试**

```bash
# 测试 API 响应时间
time curl "http://127.0.0.1:8000/api/scan?use_money_flow=true"
```

验证：响应时间在可接受范围内（< 10 秒）

**Step 4: 错误处理测试**

1. 模拟网络错误：断开网络，启用资金流过滤，验证降级处理
2. 测试边界情况：查询不存在的股票代码
3. 验证：系统不会崩溃，显示适当的错误提示

**Step 5: 记录测试结果**

创建测试报告文档（可选）：

```bash
cat > backend/test_results.md << 'EOF'
# 资金流功能测试报告

**测试日期**: 2025-01-15
**测试人员**: [姓名]

## 测试用例

### 场景 1: 不启用资金流过滤
- [ ] 通过

### 场景 2: 启用资金流过滤
- [ ] 通过

### 场景 3: 资金流数据显示
- [ ] 通过

### 场景 4: API 调用
- [ ] 通过

### 性能测试
- 平均响应时间: __ 秒
- [ ] 通过

### 错误处理
- [ ] 通过

## 发现的问题

1. [问题描述]
   - 严重程度: [低/中/高]
   - 状态: [待修复/已修复]

EOF
```

**Step 6: 提交测试报告（如果有问题需要修复）**

```bash
# 如果发现了 bug 并修复了
git add .
git commit -m "fix(money-flow): resolve issues found in E2E testing

- Fix issue with [...]
- Improve error handling for [...]
"
```

---

### Task 15: 文档更新

**目标:** 更新项目文档，说明新功能的使用方法

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md` (如果存在)

**Step 1: 更新 CLAUDE.md**

在 `CLAUDE.md` 中找到"API Endpoints"部分，添加：

```markdown
## API Endpoints

- `GET /api/health` - Health check
- `GET /api/market/indices` - Market indices (Shanghai, Shenzhen, ChiNext)
- `GET /api/market/sectors` - Sector performance ranking
- `GET /api/market/snapshot` - Real-time market overview
- `GET /api/scan` - Execute market scan with filters
  - 参数:
    - `code`: 股票代码（可选）
    - `sector`: 行业板块（可选）
    - `min_score`: 最低评分（默认 60.0）
    - `min_volume`: 最小量比（默认 1.5）
    - `use_money_flow`: 是否启用资金流过滤（默认 false，新增）
  - 返回扫描结果，包含资金流数据（如果启用）
```

在"Core Trading Strategy"部分添加：

```markdown
## Core Trading Strategy (SOP)

### 策略因子（新增资金流过滤）

**Phase 1: Screening**
- EMA 5/10/20/60 四线粘合（squeeze < 8-12%）
- 成交量突破（量比 > 1.5-2.0）
- MACD 金叉（快线 > 慢线，红柱）
- RSI > 55
- **资金流过滤（可选）**: 主力净流入 > 0 或当日主力流入 > 1000 万（新增）
```

在"Development Commands"部分添加：

```bash
# 同步资金流数据
python3 sync_data.py --money-flow --workers 5
```

**Step 2: 创建用户指南（可选）**

```bash
cat > docs/money_flow_user_guide.md << 'EOF'
# 资金流向分析功能使用指南

## 功能概述

资金流向分析功能通过追踪主力大单资金的流入流出情况，为选股策略提供额外的参考维度。

## 使用方法

### 方法 1: 通过 Web 界面

1. 打开 Alpha Vision Pro 应用
2. 在筛选面板中勾选"主力资金流入（近 3 日）"复选框
3. 点击"扫描市场"按钮
4. 查看结果表格中的"主力净流入(3日)"列

### 方法 2: 通过 API

```bash
# 启用资金流过滤
curl "http://127.0.0.1:8000/api/scan?use_money_flow=true&min_score=60"

# 不启用资金流过滤
curl "http://127.0.0.1:8000/api/scan?use_money_flow=false&min_score=60"
```

### 方法 3: 命令行同步数据

```bash
cd backend
python3 sync_data.py --money-flow --workers 10
```

## 数据说明

- **主力净流入**: 超大单 + 大单的净流入金额（万元）
- **超大单**: 单笔成交金额 >= 100 万元
- **大单**: 50 万元 <= 单笔成交金额 < 100 万元
- **评分规则**: 3 日主力净流入每 1 亿得 20 分（封顶 20 分）

## 策略逻辑

启用资金流过滤后，策略会检查：
1. 最近 3 日主力净流入是否为正，或
2. 当日主力流入是否 > 1000 万元

满足条件之一即通过资金流过滤。

## 注意事项

- 资金流数据来源于 akshare，可能有延迟
- 建议每日收盘后运行全量同步
- 数据缓存时间为 10 分钟
EOF
```

**Step 3: 提交文档更新**

```bash
git add CLAUDE.md docs/money_flow_user_guide.md
git commit -m "docs(money-flow): update documentation for new feature

- Document use_money_flow API parameter
- Add money flow filter to strategy description
- Include sync commands in development guide
- Create user guide for money flow analysis
"
```

---

## 阶段五：部署与上线

### Task 16: 代码审查与优化

**目标:** 最后的代码审查和性能优化

**Files:**
- 所有修改的文件
- Test: 代码审查检查清单

**Step 1: 运行代码检查工具**

```bash
# 后端代码风格检查（如果有）
cd backend
python3 -m pylint core/money_flow.py --disable=C0103,C0114

# 前端代码检查
cd frontend
npm run lint
```

**Step 2: 检查是否有 TODO 或 FIXME**

```bash
grep -r "TODO\|FIXME" backend/core/money_flow.py backend/core/strategy.py backend/sync_data.py
```

如果有，评估是否需要立即处理

**Step 3: 性能分析**

```bash
# 测试资金流数据获取性能
cd backend
python3 -c "
import time
from core.money_flow import get_individual_fund_flow

start = time.time()
df = get_individual_fund_flow('000001', days=5)
elapsed = time.time() - start

print(f'获取耗时: {elapsed:.2f} 秒')
print(f'数据行数: {len(df)}')
"
```

验证：单个股票数据获取 < 2 秒

**Step 4: 内存使用检查**

```bash
# 测试批量操作时的内存使用
cd backend
python3 -c "
import tracemalloc
from core.money_flow import sync_stock_money_flow
from core.db import get_db_engine

tracemalloc.start()
engine = get_db_engine()

# 同步 10 只股票
for code in ['000001', '000002', '000003', '000004', '000005',
             '600000', '600036', '601318', '601398', '601939']:
    sync_stock_money_flow(code, engine)

current, peak = tracemalloc.get_traced_memory()
print(f'当前内存使用: {current / 1024 / 1024:.2f} MB')
print(f'峰值内存使用: {peak / 1024 / 1024:.2f} MB')

tracemalloc.stop()
"
```

验证：内存使用合理（< 500 MB）

**Step 5: 安全性检查**

```bash
# 检查 SQL 注入风险
grep -n "f\"SELECT\|f\"INSERT" backend/core/money_flow.py backend/sync_data.py
```

验证：所有 SQL 查询都使用参数化查询

**Step 6: 提交优化改进**

```bash
git add .
git commit -m "refactor(money-flow): code review improvements

- Optimize data fetching performance
- Add memory usage monitoring
- Ensure SQL queries use parameterization
- Clean up TODO comments
- Improve error messages
"
```

---

### Task 17: 创建发布说明

**目标:** 编写功能发布说明

**Files:**
- Create: `docs/release-notes/money-flow-v1.0.md` (如果目录不存在则创建)

**Step 1: 创建发布说明**

```bash
mkdir -p docs/release-notes
cat > docs/release-notes/money-flow-v1.0.md << 'EOF'
# 资金流向分析功能 v1.0 发布说明

**发布日期**: 2025-01-15
**版本**: v1.0
**状态**: ✅ 已上线

## 新功能

### 资金流向分析

- ✅ 新增主力大单资金流向数据获取
- ✅ 支持超大单、大单、中单、小单分类统计
- ✅ 集成到现有选股策略中
- ✅ 提供资金流评分机制（最高 20 分）
- ✅ 前端可视化展示资金流数据

### 数据同步

- ✅ 混合同步模式（定时全量 + 按需增量）
- ✅ 自动检测数据过期并刷新
- ✅ 支持并发同步提升效率

### API 更新

- ✅ `/api/scan` 接口新增 `use_money_flow` 参数
- ✅ 返回结果包含资金流数据
- ✅ Swagger 文档自动更新

## 技术改进

- 新建 `backend/core/money_flow.py` 模块
- 扩展 `daily_k` 表，新增 5 个资金流字段
- 创建 `money_flow_daily` 表存储历史数据
- 实现缓存机制（10 分钟 TTL）
- 添加单元测试和集成测试

## 使用示例

### Web 界面

1. 打开应用
2. 勾选"主力资金流入（近 3 日）"
3. 点击"扫描市场"

### API 调用

```bash
curl "http://127.0.0.1:8000/api/scan?use_money_flow=true"
```

### 数据同步

```bash
python3 sync_data.py --money-flow --workers 10
```

## 已知限制

1. 数据来源于 akshare，可能有 15-20 分钟延迟
2. 首次同步需要时间（建议在非交易时段进行）
3. 部分股票可能无资金流数据（如新上市股票）

## 后续计划

- [ ] 支持资金流历史回测
- [ ] 添加资金流异动提醒
- [ ] 接入 Level-2 数据提升精度
- [ ] 机器学习预测资金流趋势

## 反馈渠道

如有问题或建议，请通过以下方式反馈：
- GitHub Issues
- 项目文档: `docs/plans/2025-01-15-money-flow-analysis-design.md`
EOF
```

**Step 2: 提交发布说明**

```bash
git add docs/release-notes/money-flow-v1.0.md
git commit -m "docs(release): add money flow feature v1.0 release notes

- Document new features and improvements
- Include usage examples and known limitations
- Outline future roadmap
"
```

---

### Task 18: 合并到主分支

**目标:** 将功能分支合并到主分支

**Files:**
- Git 操作
- Test: 验证合并后的代码

**Step 1: 确保所有更改已提交**

```bash
git status
```

验证：没有未提交的更改

**Step 2: 切换到主分支并合并**

```bash
# 确保在功能分支上
git branch  # 应显示 try-more-funcction

# 推送功能分支到远程（如果有远程仓库）
git push origin try-more-funcction

# 切换到主分支
git checkout main  # 或 master

# 拉取最新更改
git pull origin main

# 合并功能分支
git merge try-more-funcction
```

**Step 3: 解决合并冲突（如果有）**

如果出现冲突：

```bash
# 查看冲突文件
git status

# 手动解决冲突
# 编辑冲突文件，保留需要的代码

# 标记冲突已解决
git add <resolved-files>
git commit -m "merge: resolve merge conflicts from money-flow feature"
```

**Step 4: 推送合并后的代码**

```bash
git push origin main
```

**Step 5: 验证合并后的代码**

```bash
# 启动服务验证
cd backend
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000

cd frontend
npm run dev
```

验证：所有功能正常工作

**Step 6: 创建版本标签（可选）**

```bash
git tag -a v5.1-money-flow -m "Release v5.1: Money Flow Analysis Feature"
git push origin v5.1-money-flow
```

---

## 任务完成检查清单

### 后端开发

- [ ] Task 1: 扩展 daily_k 表结构
- [ ] Task 2: 创建 money_flow_daily 表
- [ ] Task 3: 创建 money_flow.py 模块 - 数据获取函数
- [ ] Task 4: 实现数据库存储函数
- [ ] Task 5: 实现按需同步函数
- [ ] Task 6: 集成资金流过滤到策略函数
- [ ] Task 7: 扩展 sync_data.py 实现混合同步
- [ ] Task 8: 修改 API 层添加参数支持

### 前端开发

- [ ] Task 9: 扩展前端 API 客户端
- [ ] Task 10: 修改筛选弹窗组件
- [ ] Task 11: 修改结果表格组件
- [ ] Task 12: 集成 Dashboard 主组件

### 测试与文档

- [ ] Task 13: 编写单元测试
- [ ] Task 14: 端到端测试
- [ ] Task 15: 文档更新

### 部署与上线

- [ ] Task 16: 代码审查与优化
- [ ] Task 17: 创建发布说明
- [ ] Task 18: 合并到主分支

---

## 故障排除指南

### 常见问题

**Q1: akshare 数据获取失败**

```bash
# 检查网络连接
ping www.baidu.com

# 测试 akshare 接口
python3 -c "import akshare as ak; print(ak.stock_zh_a_spot_em())"
```

**Q2: 数据库连接错误**

```bash
# 检查 PostgreSQL 服务
brew services list  # macOS
systemctl status postgresql  # Linux

# 检查数据库配置
cat backend/db_config.json
```

**Q3: 前端构建失败**

```bash
# 清理 node_modules 并重新安装
cd frontend
rm -rf node_modules package-lock.json
npm install
```

**Q4: 资金流数据为空**

- 检查股票代码是否正确
- 确认 akshare 是否支持该股票的资金流数据
- 查看后端日志中的错误信息

---

## 总结

本实施计划详细描述了为 Alpha Vision Pro 添加资金流向分析功能的完整流程。计划包含 18 个任务，分为 5 个阶段：

1. **数据库迁移** (Task 1-2): 扩展现有表结构，创建新表
2. **后端核心模块开发** (Task 3-8): 实现数据获取、存储、同步和策略集成
3. **前端集成** (Task 9-12): 修改 UI 组件，添加筛选和显示功能
4. **测试与优化** (Task 13-15): 编写测试，端到端验证，更新文档
5. **部署与上线** (Task 16-18): 代码审查，发布说明，合并代码

每个任务都包含：
- 具体的文件路径
- 完整的代码示例
- 详细的测试步骤
- 明确的提交命令

遵循本计划，开发人员可以系统地完成功能开发，确保代码质量和功能完整性。

**预计总工时**: 8-12 小时（包含测试和文档）

**关键里程碑**:
- Task 8 完成: 后端核心功能可用
- Task 12 完成: 前端 UI 集成完成
- Task 14 完成: 功能端到端验证通过
- Task 18 完成: 功能正式上线
