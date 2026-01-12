# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Alpha Vision (v5+ "Alpha Vision Pro") is an A-share (Chinese stock market) quantitative trading terminal built with a modern full-stack architecture. The system implements a multi-factor resonance scanning strategy based on "均线粘合 + 趋势突破" (MA Convergence + Trend Breakthrough).

**Architecture**: Frontend-Backend Separation
- **Backend**: FastAPI + PostgreSQL (Python 3.9.6+)
- **Frontend**: Next.js 16 + TypeScript + Tailwind CSS

## Development Commands

### Backend (FastAPI)

```bash
cd backend

# Install dependencies
python3 -m pip install -r requirements.txt

# Start development server (explicitly use IPv4 to avoid Mac IPv6 issues)
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload

# Run database test
python3 test_db.py

# Sync market data (incremental sync from last date)
python3 sync_data.py

# Force full sync (from 2020-01-01)
python3 sync_data.py --force
```

### Frontend (Next.js)

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Start production server
npm start

# Lint code
npm run lint
```

## Architecture & Code Organization

### Backend Structure (`backend/`)

- **`api.py`**: FastAPI application entry point with REST endpoints
- **`core/db.py`**: Database connection, schema initialization, CRUD operations
- **`core/data.py`**: Data fetching from akshare API, caching layer, market snapshot
- **`core/indicators.py`**: Technical indicator calculations (EMA, RSI, MACD, Bollinger Bands)
- **`core/strategy.py`**: Trading strategy implementation (`check_strategy()`, win rate calculation)
- **`sync_data.py`**: Incremental data synchronization script with concurrent workers

### Frontend Structure (`frontend/src/`)

- **`app/page.tsx`**: Main dashboard (market overview, scan results, paper trading)
- **`app/layout.tsx`**: Root layout with providers
- **`components/`**: React components (Dashboard, FilterModal, ResultsTable, PaperTradingView)
- **`lib/api.ts`**: Axios client for backend API calls
- **`lib/utils.ts`**: Utility functions (cn() for className merging)

### Database Schema (PostgreSQL)

Key tables:
- `stock_basic`: Stock metadata (code, name, industry)
- `daily_k`: Daily K-line data with technical indicators
- `scan_history`: Scan results with scoring
- `paper_trading`: Virtual trading positions
- `system_settings`: Key-value configuration storage

Configuration stored in `backend/db_config.json` (auto-created on first run).

## Core Trading Strategy (SOP)

The strategy is documented in `Stock_Strategy.md`. Key principles:

**Phase 1: Screening**
- EMA 5/10/20/60 four-line convergence (squeeze < 8-12%)
- Volume breakout (volume ratio > 1.5-2.0)
- MACD golden cross (fast line > slow line, red bar)
- RSI > 55

**Phase 2: Visual Validation**
- Sector resonance (prefer 2-3 stocks from same industry)
- Check left-side resistance
- Prefer full-body bullish candles

**Phase 3: Entry**
- Standard breakout: 14:30+, +3% to +7% gain, full-body yang line
- 5-day pullback: Strong stock retracing to EMA5 with low volume

**Phase 4: Risk Management**
- Hold 2 positions (50%/50%)
- Stop loss: -8%
- Take profit: Close below EMA20 or RSI < 45

## API Endpoints

- `GET /api/health` - Health check
- `GET /api/market/indices` - Market indices (Shanghai, Shenzhen, ChiNext)
- `GET /api/market/sectors` - Sector performance ranking
- `GET /api/market/snapshot` - Real-time market overview
- `GET /api/scan` - Execute market scan with filters (code, sector, min_score, min_volume)
- `GET /api/scan/history` - Get historical scan results by date
- `POST /api/sync` - Trigger data synchronization
- `GET /api/sync/status` - Get sync progress
- `GET/POST /api/settings` - System settings CRUD
- `POST /api/paper-trade` - Create paper trading position
- `GET /api/paper-trade` - Get all positions

Auto-generated API docs available at `http://127.0.0.1:8000/docs` (Swagger UI).

## Data Sync Strategy

The system uses **incremental synchronization**:
- Each stock tracks its last synced date in `daily_k` table
- `sync_data.py` only fetches new data from `last_date + 1` to today
- Skips if already up-to-date
- Uses `ThreadPoolExecutor` with configurable workers (default: 10)
- Only syncs stocks with market cap >= 5B CNY (configurable)

Market data sourced from **akshare** library (free A-share data API).

## Frontend Data Flow

1. User opens page → `Dashboard` component loads
2. `useEffect` triggers `fetchMarketSnapshot()` from `lib/api.ts`
3. Backend `/api/scan` returns filtered stocks sorted by score
4. `ResultsTable` displays with sortable columns
5. User clicks stock → Opens paper trade modal or external chart

State management: React hooks (`useState`, `useEffect`) with API polling for auto-refresh.

## Common Patterns

### Adding a New Technical Indicator

1. Add calculation in `core/indicators.py` to `calculate_indicators()` function
2. Store as new column in returned DataFrame
3. Update `check_strategy()` in `core/strategy.py` to use the indicator
4. Add frontend column in `ResultsTable` component if needed for display

### Adding a New API Endpoint

1. Define Pydantic model for request body (if POST)
2. Add route function in `api.py`
3. Use `sanitize_recursive()` for any JSON responses to handle NaN/Inf
4. Update `lib/api.ts` in frontend to call the new endpoint

### Database Migration

The system uses SQLAlchemy with auto-init on startup. Manual schema changes:
1. Modify `init_db()` function in `core/db.py`
2. Add table creation or ALTER TABLE statements
3. Restart backend to apply changes

## Important Notes

- **Timezone**: Uses China market timezone (trading hours 9:30-15:00)
- **Weekend handling**: Sync auto-adjusts to Friday for weekends
- **Error handling**: All API responses use `sanitize_recursive()` to convert NaN/Inf to 0 for JSON compliance
- **CORS**: Backend allows origins from localhost:3000, 3001, and 8000
- **Legacy**: `app.py` is the old Streamlit version (v1-v4), no longer maintained

## Testing

Run `python3 backend/test_db.py` to verify database connectivity and basic CRUD operations.

No automated test suite currently exists. Manual testing via frontend or Swagger UI docs.
