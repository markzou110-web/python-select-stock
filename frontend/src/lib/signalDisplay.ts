export interface DatedSignal {
    time?: unknown;
    [key: string]: unknown;
}

export interface DisplaySignal {
    signal: DatedSignal;
    role: 'entry' | 'confirmation';
}

export function selectTvStrictSignals(
    candleDates: string[],
    maBuys: DatedSignal[] = [],
    zpLongs: DatedSignal[] = [],
): { time: string; sameDay: boolean }[] {
    const maDates = new Set(maBuys.map((signal) => String(signal.time || '')));
    const zpDates = new Set(zpLongs.map((signal) => String(signal.time || '')));
    const events: { time: string; sameDay: boolean }[] = [];
    let previouslyMatched = false;
    candleDates.forEach((time, index) => {
        const recent = candleDates.slice(Math.max(0, index - 2), index + 1);
        const matched = recent.some((date) => maDates.has(date)) && recent.some((date) => zpDates.has(date));
        const sameDay = maDates.has(time) && zpDates.has(time);
        if (matched && (!previouslyMatched || sameDay)) events.push({ time, sameDay });
        previouslyMatched = matched;
    });
    return events;
}

export function selectWaveDisplaySignals(
    buySignals: DatedSignal[] = [],
    sellSignals: DatedSignal[] = [],
): DisplaySignal[] {
    const buys = Array.from(
        new Map(
            buySignals
                .filter((signal) => signal?.time)
                .map((signal) => [String(signal.time), signal]),
        ).values(),
    ).sort((a, b) => String(a.time).localeCompare(String(b.time)));
    const sellTimes = Array.from(
        new Set(sellSignals.filter((signal) => signal?.time).map((signal) => String(signal.time))),
    ).sort();

    const waves = new Map<number, DatedSignal[]>();
    let boundary = 0;
    buys.forEach((signal) => {
        while (boundary < sellTimes.length && sellTimes[boundary] < String(signal.time)) boundary += 1;
        const wave = waves.get(boundary) || [];
        wave.push(signal);
        waves.set(boundary, wave);
    });

    return Array.from(waves.values()).flatMap((wave) => {
        const first = wave[0];
        const latest = wave.at(-1);
        if (!first || !latest) return [];
        if (first === latest) return [{ signal: first, role: 'entry' as const }];
        return [
            { signal: first, role: 'entry' as const },
            { signal: latest, role: 'confirmation' as const },
        ];
    });
}
