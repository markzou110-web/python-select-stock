export interface ChartPriceLine {
    price: number;
    label: string;
    color: string;
    date?: string;
}

interface OperationBand {
    price?: unknown;
    label?: string;
    color?: string;
}

export interface RiskLevelSource {
    active_stop_price?: unknown;
    structure_stop_price?: unknown;
    initial_stop_price?: unknown;
    stop_price?: unknown;
    buy_price?: unknown;
    capital_protect_price?: unknown;
    moving_stop_price?: unknown;
    take_profit_price?: unknown;
    operation_bands?: OperationBand[];
}

function validPrice(value: unknown) {
    const price = Number(value);
    return Number.isFinite(price) && price > 0 ? price : null;
}

export function getEffectiveStopPrice(levels: RiskLevelSource | null | undefined) {
    const candidates = [
        levels?.active_stop_price,
        levels?.structure_stop_price,
        levels?.initial_stop_price,
        levels?.stop_price,
    ]
        .map(validPrice)
        .filter((price): price is number => price != null);

    return candidates.length > 0 ? Math.max(...candidates) : null;
}

export function buildRiskPriceLines(
    riskLevels: RiskLevelSource | null | undefined,
    paperLines: ChartPriceLine[] = [],
) {
    const lines: ChartPriceLine[] = [];
    const pushUniqueLine = (price: unknown, label: string, color: string, date?: string) => {
        const value = validPrice(price);
        if (value == null || lines.some((line) => Math.abs(line.price - value) < 0.005)) return;
        lines.push({ price: value, label, color, date });
    };

    const paperBuy = paperLines.find((line) => /买入|成本/.test(line.label));
    const paperStop = paperLines.find((line) => /风控|止损|失效/.test(line.label));
    const paperTarget = paperLines.find((line) => /止盈|目标/.test(line.label));
    const effectiveStop = getEffectiveStopPrice(riskLevels) ?? validPrice(paperStop?.price);

    pushUniqueLine(riskLevels?.buy_price ?? paperBuy?.price, '持仓成本', '#4f46e5', paperBuy?.date);
    pushUniqueLine(
        effectiveStop,
        validPrice(riskLevels?.structure_stop_price) != null ? '执行风控/结构失效' : '执行风控',
        '#e11d48',
    );
    pushUniqueLine(riskLevels?.capital_protect_price, '利润保护线', '#d97706');
    pushUniqueLine(riskLevels?.moving_stop_price, '移动风控线', '#f97316');
    pushUniqueLine(riskLevels?.take_profit_price ?? paperTarget?.price, '第一止盈目标', '#059669');

    (riskLevels?.operation_bands || []).forEach((band) => {
        const label = band?.label === '加仓撤退线'
            ? '加仓计划失效线'
            : band?.label === '加仓触发线'
                ? '加仓确认线'
                : band?.label || '操作线';
        pushUniqueLine(band?.price, label, band?.color || '#64748b');
    });

    paperLines.forEach((line) => {
        if (/买入|成本|风控|止损|失效|止盈|目标/.test(line.label)) return;
        pushUniqueLine(line.price, line.label, line.color, line.date);
    });

    return lines;
}

export function getQuoteSourceLabel(source?: string, updatedAt?: string) {
    if (source === 'paper_cached_price') return '持仓缓存价';
    if (source !== 'realtime_snapshot') return '日线收盘价';

    const time = updatedAt?.slice(11, 16);
    if (!time) return '行情快照';
    if (time < '09:15') return '盘前快照';
    if (time < '11:30') return '实时行情';
    if (time < '13:00') return '午间快照';
    if (time <= '15:00') return '实时行情';
    return '盘后快照';
}
