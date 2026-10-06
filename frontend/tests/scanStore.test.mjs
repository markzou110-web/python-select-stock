import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/stores/scanStore.ts', import.meta.url), 'utf8');
const compiledStore = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function scanHarness(getStatus, getDates = async () => ({ data: [] })) {
    let state;
    let poll;
    let requests = 0;
    let closed = 0;
    let cleared = 0;
    const alerts = [];
    const timeouts = [];
    const pollingReady = deferred();
    const api = {
        get: (url) => {
            if (url === '/api/scan/dates') return getDates();
            assert.equal(url, '/api/scan/status/test-task');
            requests += 1;
            return getStatus();
        },
    };
    const dependencies = {
        zustand: {
            create: (initialize) => {
                state = initialize(
                    (patch) => Object.assign(state, typeof patch === 'function' ? patch(state) : patch),
                    () => state,
                );
                return { getState: () => state };
            },
        },
        '@/lib/api': {
            default: api,
            marketApi: { scanMarket: async () => ({ data: { task_id: 'test-task' } }) },
            connectScanWebSocket: () => ({ close: () => { closed += 1; } }),
        },
        '@/lib/csv': {},
    };
    runInNewContext(compiledStore, {
        exports: {},
        require: (name) => {
            assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
            return dependencies[name];
        },
        setInterval: (callback) => { poll = callback; pollingReady.resolve(); return 1; },
        clearInterval: () => { cleared += 1; },
        setTimeout: (callback) => { timeouts.push(callback); return 1; },
        alert: (message) => alerts.push(message),
        console: { warn() {}, error() {} },
    });
    return {
        get state() { return state; },
        get requests() { return requests; },
        get closed() { return closed; },
        get cleared() { return cleared; },
        alerts,
        ready: pollingReady.promise,
        poll: () => poll(),
        completeAnimation: () => timeouts.splice(0).forEach(callback => callback()),
    };
}

async function submitScan(harness) {
    const completion = harness.state.startScan();
    await harness.ready;
    return { completion };
}

test('revoked scans finish, release the scan lock and close progress tracking', async () => {
    const harness = scanHarness(async () => ({ data: { status: 'REVOKED', message: '已取消' } }));
    const { completion } = await submitScan(harness);
    await harness.poll();
    await completion;

    assert.equal(harness.state.isScanning, false);
    assert.equal(harness.state.scanProgress, null);
    assert.equal(harness.closed, 1);
    assert.equal(harness.cleared, 1);
    assert.match(harness.alerts[0], /扫描已取消/);
    await harness.poll();
    assert.equal(harness.requests, 1);
});

test('slow status requests never overlap and completed scans stop polling', async () => {
    const status = deferred();
    const harness = scanHarness(() => status.promise);
    const { completion } = await submitScan(harness);
    const firstPoll = harness.poll();
    await harness.poll();
    await harness.poll();
    assert.equal(harness.requests, 1);

    status.resolve({ data: { status: 'SUCCESS', results: [] } });
    await firstPoll;
    await harness.poll();
    assert.equal(harness.requests, 1);
    harness.completeAnimation();
    await completion;
    assert.equal(harness.state.isScanning, false);
    assert.equal(harness.state.lastScanSummary.count, 0);
});

test('ten polling failures finish once without further requests or repeated alerts', async () => {
    const harness = scanHarness(async () => { throw new Error('offline'); });
    const { completion } = await submitScan(harness);
    for (let attempt = 0; attempt < 10; attempt += 1) await harness.poll();
    await completion;
    await harness.poll();
    await harness.poll();

    assert.equal(harness.requests, 10);
    assert.equal(harness.alerts.length, 1);
    assert.equal(harness.cleared, 1);
    assert.equal(harness.closed, 1);
    assert.equal(harness.state.isScanning, false);
});

test('refreshing history dates during a scan preserves status tracking and completion', async () => {
    const harness = scanHarness(async () => ({ data: { status: 'SUCCESS', results: [] } }));
    const { completion } = await submitScan(harness);
    await harness.state.fetchHistory();
    await harness.poll();

    assert.equal(harness.requests, 1);
    harness.completeAnimation();
    await completion;
    assert.equal(harness.state.isScanning, false);
    assert.equal(harness.state.lastScanSummary.taskId, 'test-task');
});

test('an older history refresh cannot overwrite the latest date list', async () => {
    const older = deferred();
    const newer = deferred();
    let dateRequests = 0;
    const harness = scanHarness(async () => ({ data: { status: 'REVOKED' } }), () => {
        dateRequests += 1;
        return dateRequests === 1 ? older.promise : newer.promise;
    });
    harness.state.lastScanSummary = {};
    const firstRefresh = harness.state.fetchHistory();
    const secondRefresh = harness.state.fetchHistory();
    newer.resolve({ data: ['2026-10-01'] });
    await secondRefresh;
    older.resolve({ data: ['2026-09-30'] });
    await firstRefresh;
    assert.deepEqual(harness.state.historyDates, ['2026-10-01']);
});
