# Project Development Plans & Walkthroughs

This document is the unified location for all implementation plans, feature checklists, and feature walkthroughs. It is kept at the project root for easy access.

---

## ✅ Feature: Paper Trading K-Line Details, Bark Notifications (Phase 1)

### Summary
All 5 tasks completed. Enriched stock detail API with paper trading indicators, added Bark push notifications for buy/sell/wind-control, rendered simulated position dashboard and colored price lines on the mini K-line chart in AIDeepDive sidebar.

---

## Feature: Full-Page Stock Detail View (Phase 2)

### 1. Implementation Plan

#### Goal Description
When clicking a stock in **Paper Trading (拟合实盘)**, open a **full-page detail view** with:
- Full-width K-line chart (200 days) with EMA overlays, buy/sell signal markers, and paper trade price lines
- Trading metrics strip (buy/stop/TP/P&L/days)
- AI operation suggestion card (rule-based: 加仓/等待/减仓/平仓)
- Three-column info grid: concept themes, financial profile, risk assessment
- All data dynamically updated daily

#### Proposed Changes

- **Backend**:
  - `backend/routers/stock.py`: New `/api/stock/full-analysis` endpoint returning K-line, signals, concepts, financials, risk assessment, and AI suggestions in a single call.
  - Helper functions: `_fetch_stock_concepts()` (24h cached), `_fetch_financials()`, `_compute_risk_assessment()`, `_generate_ai_suggestion()` (rule engine).
- **Frontend**:
  - `frontend/src/components/StockDetailPage.tsx` [NEW]: Full-page detail component with chart, metrics strip, AI card, and 3-column grid.
  - `frontend/src/components/PaperTradingView.tsx` [MODIFIED]: Changed stock click from sidebar to full-page navigation with `detailStock` state.

---

### 2. Implementation Checklist

- `[x]` Task 1: Create `/api/stock/full-analysis` backend endpoint
  - `[x]` 1a: Concept fetching with 24h cache
  - `[x]` 1b: Financial data query with fallback
  - `[x]` 1c: Risk assessment (volatility, liquidity, sector, regime)
  - `[x]` 1d: AI suggestion rule engine (trend, momentum, MACD, risk distance, regime)
  - `[x]` 1e: Assemble full endpoint
- `[x]` Task 2: Create `StockDetailPage.tsx` full-page component
- `[x]` Task 3: Modify `PaperTradingView.tsx` for full-page navigation
- `[x]` Task 4: Dashboard wiring (handled within PaperTradingView)
- `[x]` Task 5: Verify end-to-end flow

---

### 3. Verification & Walkthrough

#### Files Changed
| File | Type | Description |
|------|------|-------------|
| `backend/routers/stock.py` | MODIFY | Added 500-line `/api/stock/full-analysis` endpoint with 4 helper functions |
| `frontend/src/components/StockDetailPage.tsx` | NEW | Full-page detail component (~430 lines) |
| `frontend/src/components/PaperTradingView.tsx` | MODIFY | Changed stock click to open full-page detail |
| `frontend/src/components/KLineChart.tsx` | MODIFY | Fixed markers setting to use lightweight-charts v5 createSeriesMarkers helper |

#### AI Suggestion Rule Engine
The rule engine analyzes 6 dimensions and produces a composite score:
1. **Trend** (EMA5/20/60 alignment) → ±2 score
2. **Momentum** (RSI zones) → ±1 score
3. **MACD** (golden/death cross, histogram direction) → ±2 score
4. **Risk Distance** (stop-loss buffer, TP proximity) → ±3 score
5. **Market Regime** (OFFENSIVE/DEFENSIVE/CRITICAL) → ±2 score
6. **P&L Status** (current position return)

Decision thresholds:
- `CLOSE`: stop buffer <2% OR regime CRITICAL OR score ≤ -3
- `REDUCE`: score ≤ -1 OR RSI > 80 OR TP reached
- `ADD`: score ≥ 3 + buffer >10% OR MACD golden cross + trend + RSI < 60
- `HOLD`: default

---

## ✅ Feature: 模拟盘 vs 实盘 区分 (Phase 3)

### 1. Goal
在「拟合实盘」模块中新增交易模式维度，区分 **模拟盘 (SIMULATED)** 和 **实盘 (REAL)** 交易记录，便于用户在同一界面中分别管理两种模式的交易和统计。

### 2. Implementation Checklist

- `[x]` Task 1: Backend model & schema — `trade_mode` 字段 (SIMULATED | REAL)
- `[x]` Task 2: Backend router — 写入/返回/按模式统计/Bark推送区分
- `[x]` Task 3: Frontend PaperTradingView — 模式切换器 + 视觉徽章 + 按模式过滤
- `[x]` Task 4: Frontend add-trade flows — AIDeepDive & ResultsTable 模式选择器
- `[x]` Task 5: Build & verify

### 3. Files Changed
| File | Type | Description |
|------|------|-------------|
| `backend/core/models.py` | MODIFY | `PaperTrading.trade_mode` 字段 (String, default SIMULATED) |
| `backend/schemas/paper_trade.py` | MODIFY | `trade_mode: Literal["SIMULATED", "REAL"]` 验证 |
| `backend/core/db.py` | MODIFY | `init_db()` 自动迁移：ADD COLUMN IF NOT EXISTS |
| `backend/routers/paper_trade.py` | MODIFY | 4 个端点全面支持 trade_mode + Bark 推送区分 |
| `frontend/src/components/PaperTradingView.tsx` | MODIFY | 模式切换器 + 统计按模式展示 + 视觉标识 |
| `frontend/src/components/AIDeepDive.tsx` | MODIFY | 加入弹窗增加交易模式选择器 |
| `frontend/src/components/ResultsTable.tsx` | MODIFY | 加入弹窗增加交易模式选择器 |

### 4. Key Design Decisions
- **视觉区分**: 实盘 → 🔴红色竖线 + 红色徽章; 模拟 → 🔵蓝色虚线 + 蓝色徽章
- **推送区分**: 「【实盘买入】」/「【实盘平仓】」/「【实盘风控平仓】」vs 模拟仓
- **统计分离**: `stats_by_mode` 返回两种模式的独立胜率/盈亏/持仓数据
- **向后兼容**: 现有记录自动标记为 SIMULATED，无数据丢失
- **模拟转实盘**: `POST /api/paper/convert/{id}` 一键转换 + Bark 推送 + 确认对话框

### Phase 3.3: 全面支持拼音首字母/中文/代码模糊搜索
- **拼音简拼支持**: 引入 `pypinyin` 库，在后端建立极速的内存缓存（Lazy-loaded in-memory cache）。现在搜索框完美支持拼音首字母缩写搜索（例如：输入 `payh` 即可精准搜索出 `平安银行`；输入 `zgpa` 即可检索出 `中国平安`）。
- **中文/代码检索**: 原生支持中文名称（如 `平安`）与数字代码（如 `000001`）的秒级检索，极大地改善了符合中国股民习惯的交互体验。


