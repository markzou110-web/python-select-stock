import { describe, expect, it } from 'vitest';

import { normalizePaperModeStats, normalizePaperStats } from './paperTradingStats';

describe('paper trading response normalization', () => {
    it('fills an empty stats response instead of rendering undefined', () => {
        const stats = normalizePaperStats<{
            max_drawdown?: number;
            profit_factor?: number;
            wins?: number;
            sector_distribution?: unknown[];
            rolling_performance?: unknown[];
        }>({});

        expect(stats.max_drawdown).toBe(0);
        expect(stats.profit_factor).toBe(0);
        expect(stats.wins).toBe(0);
        expect(stats.sector_distribution).toEqual([]);
    });

    it('fills missing per-mode fields while preserving received values', () => {
        const stats = normalizePaperModeStats<{
            total?: number;
            wins?: number;
            losses?: number;
            avg_hold_days?: number;
        }>({ total: 3, wins: 2 });

        expect(stats.total).toBe(3);
        expect(stats.wins).toBe(2);
        expect(stats.losses).toBe(0);
        expect(stats.avg_hold_days).toBe(0);
    });
});
