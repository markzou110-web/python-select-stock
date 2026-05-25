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
