import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildRiskPriceLines,
    getEffectiveStopLabel,
    getEffectiveStopPrice,
    getQuoteSourceLabel,
} from '../src/lib/tradingLevels.ts';

test('uses the strictest applicable long-position stop', () => {
    assert.equal(getEffectiveStopPrice({
        stop_price: 16.03,
        initial_stop_price: 16.56,
        structure_stop_price: 16.60,
        active_stop_price: 16.03,
    }), 16.60);
});

test('labels the active stop by the level that actually sets it', () => {
    assert.equal(getEffectiveStopLabel({
        initial_stop_price: 18.00,
        structure_stop_price: 19.39,
        moving_stop_price: 0,
    }), '执行风控·结构失效');
    assert.equal(getEffectiveStopLabel({
        active_stop_price: 19.39,
        initial_stop_price: 18.00,
        structure_stop_price: 19.00,
        moving_stop_price: 19.39,
    }), '执行风控·移动风控');
    assert.equal(getEffectiveStopLabel({
        active_stop_price: 19.39,
        risk_stage: '保本移动',
    }), '执行风控·保本保护');
});

test('consolidates duplicate position and operation levels by price', () => {
    const lines = buildRiskPriceLines({
        buy_price: 17.62,
        stop_price: 16.03,
        initial_stop_price: 16.56,
        structure_stop_price: 16.60,
        active_stop_price: 16.03,
        take_profit_price: 18.88,
        operation_bands: [
            { price: 17.62, label: '加仓触发线', color: '#16a34a' },
            { price: 17.36, label: '加仓撤退线', color: '#d97706' },
            { price: 16.60, label: '减仓线', color: '#e11d48' },
        ],
    }, [
        { price: 17.62, label: '买入价', color: '#6366f1', date: '2026-09-04' },
        { price: 16.03, label: '实时持仓风控线', color: '#f43f5e' },
        { price: 18.88, label: '止盈价', color: '#10b981' },
    ]);

    assert.deepEqual(lines.map(({ price, label }) => ({ price, label })), [
        { price: 17.62, label: '持仓成本' },
        { price: 16.60, label: '执行风控·结构失效' },
        { price: 18.88, label: '第一止盈目标' },
        { price: 17.36, label: '加仓计划失效线' },
    ]);
});

test('labels pre-market snapshots as non-live prices', () => {
    assert.equal(getQuoteSourceLabel('realtime_snapshot', '2026-09-08 08:03:08'), '盘前快照');
    assert.equal(getQuoteSourceLabel('realtime_snapshot', '2026-09-08 10:03:08'), '实时行情');
});
