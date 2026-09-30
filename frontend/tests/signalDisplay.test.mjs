import assert from 'node:assert/strict';
import test from 'node:test';

import { collapseChartMarkers, selectTvStrictSignals, selectWaveDisplaySignals } from '../src/lib/signalDisplay.ts';

test('collapses same-candle markers while preserving signal details and priority', () => {
    const result = collapseChartMarkers([
        { time: '2026-09-26', position: 'belowBar', source: 'bark', text: 'Bark推荐' },
        { time: '2026-09-26', position: 'belowBar', source: 'tv_strict', text: 'TV共振' },
        { time: '2026-09-26', position: 'aboveBar', source: 'exit', text: '止损' },
    ], ['tv_strict', 'bark']);

    assert.equal(result.length, 2);
    assert.equal(result[0].text, 'TV共振 +1');
    assert.equal(result[0].label, 'Bark推荐 · TV共振');
});

test('keeps only the first and latest buy signal in one wave', () => {
    const result = selectWaveDisplaySignals([
        { time: '2026-09-03' },
        { time: '2026-09-07' },
        { time: '2026-09-09' },
        { time: '2026-09-11' },
    ]);

    assert.deepEqual(result.map(({ signal, role }) => [signal.time, role]), [
        ['2026-09-03', 'entry'],
        ['2026-09-11', 'confirmation'],
    ]);
});

test('starts a new wave after an exit signal', () => {
    const result = selectWaveDisplaySignals(
        [
            { time: '2026-08-01' },
            { time: '2026-08-03' },
            { time: '2026-09-03' },
            { time: '2026-09-07' },
            { time: '2026-09-09' },
        ],
        [{ time: '2026-08-10' }],
    );

    assert.deepEqual(result.map(({ signal, role }) => [signal.time, role]), [
        ['2026-08-01', 'entry'],
        ['2026-08-03', 'confirmation'],
        ['2026-09-03', 'entry'],
        ['2026-09-09', 'confirmation'],
    ]);
});

test('shows a single signal as an entry instead of a confirmation', () => {
    const result = selectWaveDisplaySignals([{ time: '2026-09-03' }]);
    assert.deepEqual(result.map(({ signal, role }) => [signal.time, role]), [
        ['2026-09-03', 'entry'],
    ]);
});

test('distinguishes same-day TV dual hit from a three-trading-bar window hit', () => {
    const dates = ['2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09'];
    assert.deepEqual(selectTvStrictSignals(
        dates,
        [{ time: '2026-09-03' }, { time: '2026-09-08' }],
        [{ time: '2026-09-07' }, { time: '2026-09-08' }],
    ), [
        { time: '2026-09-07', sameDay: false },
        { time: '2026-09-08', sameDay: true },
    ]);
});

test('does not mark TV dual hit beyond three trading bars or before both signals exist', () => {
    const dates = ['2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08'];
    assert.deepEqual(selectTvStrictSignals(dates, [{ time: dates[0] }], [{ time: dates[3] }]), []);
    assert.deepEqual(selectTvStrictSignals(dates, [{ time: dates[2] }], [{ time: dates[0] }]), [
        { time: dates[2], sameDay: false },
    ]);
});

test('does not repeat a strict marker while an existing match remains active', () => {
    const dates = ['2026-09-03', '2026-09-04', '2026-09-07'];
    assert.deepEqual(selectTvStrictSignals(dates,
        [{ time: dates[0] }, { time: dates[2] }],
        [{ time: dates[1] }],
    ), [{ time: dates[1], sameDay: false }]);
});
