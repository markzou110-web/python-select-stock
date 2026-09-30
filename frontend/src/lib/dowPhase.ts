/**
 * 道氏趋势阶段（pa_trend_phase）的可视化映射。
 * 后端 /api/kline/{code} 与 /api/stock/full-analysis 返回 trend_phases：
 * [{ time, phase, action }]，仅在阶段切换的K线上有记录。
 * 本模块把阶段切换点转换为 lightweight-charts 的 series markers。
 */
export interface DowPhasePoint {
    time: string;
    phase: string;
    action?: string;
}

export type DowPhaseCategory = 'bull' | 'warning' | 'bear' | 'neutral';

/** 道氏三阶段归类：多头推进 / 过热派发 / 趋势破坏 / 中性震荡 */
export const DOW_PHASE_CATEGORY: Record<string, DowPhaseCategory> = {
    '第一波拉升': 'bull',
    '首次回调': 'bull',
    '二次入场': 'bull',
    '突破回踩': 'bull',
    '初始突破': 'bull',
    '加速段': 'warning',
    '衰竭段': 'warning',
    '趋势破坏': 'bear',
    '主要趋势反转': 'bear',
    '空头趋势': 'bear',
    '震荡观察': 'neutral',
};

const CATEGORY_COLOR: Record<DowPhaseCategory, string> = {
    bull: '#0d9488',
    warning: '#f59e0b',
    bear: '#dc2626',
    neutral: '#94a3b8',
};

export function dowPhaseCategory(phase: string): DowPhaseCategory {
    return DOW_PHASE_CATEGORY[phase] ?? 'neutral';
}

export function dowPhaseColor(phase: string): string {
    return CATEGORY_COLOR[dowPhaseCategory(phase)];
}

/** 阶段切换点 → lightweight-charts series marker（多头在下方，其余在上方） */
export function buildDowPhaseMarkers(phases?: DowPhasePoint[] | null) {
    return (phases || [])
        .filter((point) => point?.time && point?.phase && point.phase !== '震荡观察')
        .map((point) => {
            const category = dowPhaseCategory(point.phase);
            return {
                time: point.time,
                position: category === 'bull' ? 'belowBar' : 'aboveBar',
                color: CATEGORY_COLOR[category],
                shape: 'square',
                size: 0.6,
                text: point.phase,
                label: point.action ? `${point.phase}：${point.action}` : point.phase,
                source: 'dow_trend_phase',
            };
        });
}

/** 图例（按类别聚合，避免 11 个阶段全列） */
export const DOW_PHASE_LEGEND: Array<{ label: string; color: string }> = [
    { label: '多头推进(拉升/回调/入场/突破)', color: CATEGORY_COLOR.bull },
    { label: '过热派发(加速/衰竭)→仓位×0.5', color: CATEGORY_COLOR.warning },
    { label: '趋势破坏(反转/空头)', color: CATEGORY_COLOR.bear },
];

/** K线图行情提示条 */
export interface ChartHint {
    level: string; // 'info' | 'warning' | 'danger'
    text: string;
}

export const CHART_HINT_LEVEL_STYLE: Record<string, string> = {
    info: 'border-slate-200 bg-slate-100 text-slate-600',
    warning: 'border-amber-200 bg-amber-50 text-amber-700',
    danger: 'border-rose-200 bg-rose-50 text-rose-700',
};

export function hintLevelStyle(level: string): string {
    return CHART_HINT_LEVEL_STYLE[level] ?? CHART_HINT_LEVEL_STYLE.info;
}

/** 执行策略投影：未来可能走势（情景路径）+ 关键价位 */
export interface ProjectionLevel {
    label: string;
    price: number;
}

export interface ProjectionScenario {
    name: string;
    offsets: number[];  // 相对最后一根K线的"未来第N个交易日"
    values: number[];   // 对应价位
}

export interface TradeProjection {
    key_levels?: ProjectionLevel[];
    scenarios?: ProjectionScenario[];
    note?: string;
    phase?: string;
}

/** 投影箭头标记（未来日期上的 lightweight-charts marker） */
export interface ProjectionMarker {
    time: string;
    position: 'belowBar' | 'aboveBar';
    color: string;
    shape: 'arrowUp' | 'arrowDown';
    size: number;
    text: string;
    label: string;
    source: string;
}

const SCENARIO_MARKER_STYLE = {
    买点: { color: '#0d9488', shape: 'arrowUp' as const, position: 'belowBar' as const },
};

/** 生成最后一根K线之后的 N 个未来交易日（跳过周末；节假日近似忽略，仅作示意） */
export function futureBusinessDates(lastTime: string, count: number): string[] {
    const dates: string[] = [];
    const cursor = new Date(`${lastTime}T00:00:00`);
    if (Number.isNaN(cursor.getTime())) return dates;
    const pad = (n: number) => String(n).padStart(2, '0');
    while (dates.length < count) {
        cursor.setDate(cursor.getDate() + 1);
        const day = cursor.getDay();
        if (day === 0 || day === 6) continue;
        // 用本地时间拼日期串（toISOString 会转 UTC，东八区零点会回退一天）
        dates.push(`${cursor.getFullYear()}-${pad(cursor.getMonth() + 1)}-${pad(cursor.getDate())}`);
    }
    return dates;
}

/** 未来空白K线（仅 time）：让 lightweight-charts 在最后一根之后留出可标注的未来区域 */
export function futureWhitespaceCandles(lastTime: string, count: number): Array<{ time: string }> {
    return futureBusinessDates(lastTime, count).map((time) => ({ time }));
}

/**
 * 执行策略投影 → 未来日期上的箭头标记（替代虚线路径，更干净）：
 * - 回踩买点：青色↑（在K线下方）
 */
export function buildProjectionScenarioMarkers(
    projection: TradeProjection | null | undefined,
    lastTime: string,
): ProjectionMarker[] {
    if (!projection?.scenarios?.length || !lastTime) return [];
    const maxOffset = Math.max(
        ...projection.scenarios
            .flatMap((s) => (s.offsets || []).map(Number))
            .filter((n) => Number.isFinite(n) && n > 0),
        0,
    );
    if (maxOffset <= 0) return [];
    const dates = futureBusinessDates(lastTime, maxOffset);
    const dateAt = (offset: number) => dates[offset - 1];

    const markers: ProjectionMarker[] = [];
    const placed = new Set<string>();
    const push = (time: string | undefined, kind: keyof typeof SCENARIO_MARKER_STYLE, text: string, label: string) => {
        const style = SCENARIO_MARKER_STYLE[kind];
        if (!time || placed.has(kind)) return;
        placed.add(kind);
        markers.push({ time, ...style, size: 1, text, label, source: 'trade_projection' });
    };

    for (const scenario of projection.scenarios || []) {
        const offsets = (scenario?.offsets || []).map(Number);
        const values = (scenario?.values || []).map(Number);
        if (scenario.name === '回踩再上攻') {
            if (offsets[0] > 0 && values[0] > 0) {
                push(dateAt(offsets[0]), '买点', '回踩买点', `回踩买入区 ${values[0].toFixed(2)} 附近介入`);
            }
        }
    }
    return markers;
}

/** 关键价位 → 右轴价格线（跳过与已有价位重合的，避免重复画线） */
export function projectionPriceLines(
    projection: TradeProjection | null | undefined,
    existingPrices: Array<number | null | undefined>,
): Array<{ label: string; price: number }> {
    const existing = (existingPrices || [])
        .map((p) => Number(p))
        .filter((p) => Number.isFinite(p) && p > 0);
    return (projection?.key_levels || [])
        .map((level) => ({ label: String(level?.label || ''), price: Number(level?.price) }))
        .filter((level) => Number.isFinite(level.price) && level.price > 0)
        .filter((level) => !existing.some((price) => Math.abs(price - level.price) / level.price < 0.003));
}
