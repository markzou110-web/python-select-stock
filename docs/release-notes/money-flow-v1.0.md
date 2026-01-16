# 资金流向分析功能 v1.0 发布说明

**发布日期**: 2025-01-16
**版本**: v1.0
**状态**: ✅ 已完成

---

## 🎉 新功能概览

本次更新为核心策略增强，新增**主力资金流向分析**功能，将大单资金流向数据融入现有的均线粘合策略，大幅提升选股精准度。

### 核心亮点

- ✅ **智能资金追踪** - 实时监控主力大单（超大单+大单）资金流向
- ✅ **策略无缝集成** - 资金流过滤作为可选因子，评分权重动态调整
- ✅ **混合同步机制** - 定时全量 + 扫描时按需增量同步，确保数据新鲜度
- ✅ **可视化增强** - 前端新增资金流列，红绿配色直观展示资金动向
- ✅ **向后兼容** - 默认关闭，不影响现有策略使用

---

## 📊 功能详情

### 1. 资金流数据获取

**数据源**: akshare (免费接口)
- **接口**: `stock_individual_fund_flow`
- **数据范围**: 
  - 超大单净流入（单笔 ≥ 100 万元）
  - 大单净流入（50-100 万元）
  - 中单净流入
  - 小单净流入
- **更新频率**: 实时（15-20 分钟延迟）
- **缓存策略**: 10 分钟 TTL

### 2. 数据库设计

#### 扩展表: `daily_k`
新增字段存储每日资金流快照：
```sql
- main_net_inflow NUMERIC(15, 2)      -- 主力净流入（万元）
- super_large_net NUMERIC(15, 2)      -- 超大单净流入
- large_net NUMERIC(15, 2)            -- 大单净流入
- medium_net NUMERIC(15, 2)           -- 中单净流入
- small_net NUMERIC(15, 2)            -- 小单净流入
```

#### 新表: `money_flow_daily`
专门存储资金流历史：
```sql
CREATE TABLE money_flow_daily (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10),
    date DATE,
    main_net_inflow NUMERIC(15, 2),
    ...
    UNIQUE(code, date)
);
```
索引：`(code, date)`、`date` 优化查询性能

### 3. 策略集成

#### 参数扩展
```python
check_strategy(
    df,
    use_money_flow_filter=False,  # 新增：是否启用资金流过滤
    money_flow_days=3              # 新增：统计天数
)
```

#### 过滤逻辑
**启用资金流过滤时**，额外检查：
1. 最近 N 日主力净流入 > 0（累计为正）
2. **或** 当日主力大幅流入 > 1000 万元

满足条件之一即通过资金流过滤。

#### 评分调整
- **不启用资金流**: 量能(25%) + 粘合(50%) + RSI(25%)
- **启用资金流**: 量能(20%) + 粘合(40%) + RSI(20%) + **资金流(20%)**

资金流评分公式：
```python
score = min(abs(最近3日主力净流入) / 10000 * 20, 20)
# 每 1 亿流入得 20 分，封顶 20 分
```

---

## 🔧 使用指南

### Web 界面使用

1. 启动后端服务
   ```bash
   cd backend
   PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
   ```

2. 启动前端服务
   ```bash
   cd frontend
   npm run dev
   ```

3. 打开浏览器访问 `http://localhost:3000`

4. 点击"🔬 高级策略筛选"按钮

5. 勾选"💰 主力资金流入过滤"复选框

6. 点击"保存并执行"

7. 查看扫描结果中的"资金流"列

### 命令行数据同步

```bash
cd backend
python3 sync_data.py --money-flow --workers 10
```

---

## ⚠️ 已知限制

1. **数据延迟**: akshare 数据有 15-20 分钟延迟
2. **首次同步**: 首次运行需同步 90 天历史数据，约需 10-30 分钟
3. **部分股票缺失**: 新上市股票或停牌股票可能无资金流数据
4. **网络依赖**: 需要连接 akshare API（国内访问）
5. **并发限制**: 建议同步线程数 ≤ 20

---

## 📚 相关文档

- **设计文档**: `docs/plans/2025-01-15-money-flow-analysis-design.md`
- **实施计划**: `docs/plans/2025-01-15-money-flow-implementation.md`
- **项目文档**: `CLAUDE.md`

---

**祝您投资顺利！📈**

*Alpha Vision Pro - 让 A 股量化交易更智能*
