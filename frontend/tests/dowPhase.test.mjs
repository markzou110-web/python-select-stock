import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildDowPhaseMarkers,
    dowPhaseCategory,
    DOW_PHASE_LEGEND,
} from '../src/lib/dowPhase.ts';

test('maps each dow phase to the right category', () => {
    assert.equal(dowPhaseCategory('二次入场'), 'bull');
    assert.equal(dowPhaseCategory('突破回踩'), 'bull');
    assert.equal(dowPhaseCategory('衰竭段'), 'warning');
    assert.equal(dowPhaseCategory('加速段'), 'warning');
    assert.equal(dowPhaseCategory('趋势破坏'), 'bear');
    assert.equal(dowPhaseCategory('空头趋势'), 'bear');
    assert.equal(dowPhaseCategory('震荡观察'), 'neutral');
    assert.equal(dowPhaseCategory('未知阶段'), 'neutral');
});

test('builds series markers only for valid phase changes', () => {
    const markers = buildDowPhaseMarkers([
        { time: '2026-09-01', phase: '第一波拉升', action: '顺势观察回踩入场' },
        { time: '2026-09-10', phase: '加速段', action: '持有为主，等待首次像样回调' },
        { time: '2026-09-15', phase: '震荡观察', action: '等待区间边界信号' },
        { time: '', phase: '衰竭段' },
        null,
    ]);

    assert.equal(markers.length, 2);
    assert.deepEqual(
        markers.map((marker) => [marker.time, marker.position, marker.shape]),
        [
            ['2026-09-01', 'belowBar', 'square'],
            ['2026-09-10', 'aboveBar', 'square'],
        ],
    );
    assert.equal(markers[0].color, '#0d9488');
    assert.equal(markers[0].source, 'dow_trend_phase');
    assert.equal(markers[0].label, '第一波拉升：顺势观察回踩入场');
    assert.equal(markers[1].color, '#f59e0b');
});

test('does not show ambiguous range-observation markers or legend entries', () => {
    assert.deepEqual(buildDowPhaseMarkers([
        { time: '2026-09-15', phase: '震荡观察', action: '等待区间边界信号' },
    ]), []);
    assert.equal(DOW_PHASE_LEGEND.some((item) => item.label === '震荡观察'), false);
});

import { buildProjectionScenarioMarkers, futureBusinessDates, futureWhitespaceCandles, projectionPriceLines } from '../src/lib/dowPhase.ts';

test('futureBusinessDates skips weekends', () => {
    // 2026-09-18 是周五 → 之后应是 09-21(一)、09-22(二)、09-23(三)
    assert.deepEqual(futureBusinessDates('2026-09-18', 3), ['2026-09-21', '2026-09-22', '2026-09-23']);
});

test('builds pullback markers without invalidation or target arrows', () => {
    const markers = buildProjectionScenarioMarkers({
        scenarios: [
            { name: '回踩再上攻', offsets: [4, 12], values: [19.6, 24.0] },
            { name: '直接上攻', offsets: [7], values: [24.0] },
            { name: '破位失效', offsets: [5], values: [18.4] },
            { name: '坏数据', offsets: [-1], values: [0] },
        ],
    }, '2026-09-18');

    assert.deepEqual(markers.map((m) => [m.time, m.shape, m.position, m.text]), [
        ['2026-09-24', 'arrowUp', 'belowBar', '回踩买点'],
    ]);
    assert.equal(markers[0].color, '#0d9488');
    assert.ok(markers[0].label.includes('19.60'));
    assert.equal(markers.some((m) => m.text === '失效离场'), false);
});

test('does not create target markers for either projection scenario', () => {
    const markers = buildProjectionScenarioMarkers({
        scenarios: [
            { name: '直接上攻', offsets: [7], values: [24.0] },
            { name: '回踩再上攻', offsets: [4, 12], values: [19.6, 24.0] },
        ],
    }, '2026-09-18');
    assert.equal(markers.some((m) => m.text === '目标位'), false);
});

test('futureWhitespaceCandles reserves future area without OHLC', () => {
    const bars = futureWhitespaceCandles('2026-09-18', 3);
    assert.deepEqual(bars, [
        { time: '2026-09-21' }, { time: '2026-09-22' }, { time: '2026-09-23' },
    ]);
});

test('projectionPriceLines skips levels overlapping existing lines', () => {
    const lines = projectionPriceLines({
        key_levels: [
            { label: '回踩买入区', price: 19.6 },
            { label: '失效止损', price: 18.4 },
            { label: '第一目标', price: 24.0 },
        ],
    }, [18.41, null]);  // 18.41 与 18.4 重合(<0.3%)
    assert.deepEqual(lines.map((l) => l.label), ['回踩买入区', '第一目标']);
});
