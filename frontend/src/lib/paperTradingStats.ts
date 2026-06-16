const EMPTY_MODE_STATS = {
    total: 0,
    wins: 0,
    losses: 0,
    win_rate: 0,
    avg_pl_pct: 0,
    total_pl_pct: 0,
    avg_hold_days: 0
};

const EMPTY_STATS = {
    total_trades: 0,
    wins: 0,
    losses: 0,
    flat: 0,
    win_rate: 0,
    avg_pl_pct: 0,
    total_pl_pct: 0,
    avg_hold_days: 0,
    max_drawdown: 0,
    profit_factor: 0,
    sector_distribution: [],
    rolling_performance: []
};

type StatsCollections = {
    sector_distribution?: unknown[];
    rolling_performance?: unknown[];
};

export const normalizePaperModeStats = <T extends object>(value?: Partial<T>): T => ({
    ...EMPTY_MODE_STATS,
    ...value
}) as T;

export const normalizePaperStats = <T extends StatsCollections & object>(value?: Partial<T>): T => ({
    ...EMPTY_STATS,
    ...value,
    sector_distribution: value?.sector_distribution || [],
    rolling_performance: value?.rolling_performance || []
}) as unknown as T;
