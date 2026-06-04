import React, { useEffect, useState } from 'react';
import api from '@/lib/api';
import SplitKLineCharts from './SplitKLineCharts';

interface StockChartProps {
    code: string;
    name: string;
    strategyType?: string;
}

const StockChart: React.FC<StockChartProps> = ({ code, name, strategyType }) => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [chartData, setChartData] = useState<any | null>(null);
    const [priceAction, setPriceAction] = useState<any>(null);

    useEffect(() => {
        const fetchDataAndRender = async () => {
            try {
                setLoading(true);
                setError(null);
                const response = await api.get(`/api/kline/${code}?strategy_type=${strategyType || 'squeeze'}`);
                const data = response.data;
                setChartData(data);
                setPriceAction(data.price_action || null);
            } catch (err: any) {
                setError(err.message || 'Error loading chart data');
            } finally {
                setLoading(false);
            }
        };

        fetchDataAndRender();
    }, [code, strategyType]);

    return (
        <div className="relative w-full bg-white rounded-lg overflow-hidden border border-slate-200">
            <div className="px-4 py-2 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                <div className="font-bold text-slate-800">
                    {name} <span className="text-slate-500 text-sm ml-2">{code}</span>
                </div>
                <div className="text-xs text-slate-500 flex items-center gap-3">
                    <span className="flex items-center gap-1">
                        <div className="w-2 h-2 rounded-full bg-[#ff9800]"></div> Range Filter
                    </span>
                    {strategyType === 'squeeze' && (
                        <span className="flex items-center gap-1 font-medium text-blue-600">
                            🔵 均线粘合突破
                        </span>
                    )}
                    {strategyType === 'consensus' && (
                        <span className="flex items-center gap-1 font-medium text-purple-600">
                            🟣 Azul共识突破
                        </span>
                    )}
                    {strategyType === 'pine' && (
                        <span className="flex items-center gap-1 font-medium text-indigo-600">
                            🚀 多指标共振
                        </span>
                    )}
                    <span className="flex items-center gap-1 text-red-500 font-medium">
                        --- 移动风控线
                    </span>
                </div>
            </div>
            {priceAction?.price_action_summary && (
                <div className="px-4 py-2 bg-blue-50/60 border-b border-blue-100 flex flex-wrap items-center gap-3 text-[11px]">
                    <span className="font-black text-blue-500 uppercase tracking-widest">价格行为</span>
                    <span className="font-black text-blue-800">{priceAction.price_action_summary}</span>
                    {priceAction.price_action_regime && (
                        <span className="font-bold text-blue-700">{priceAction.price_action_regime} · {priceAction.price_action_entry_quality}</span>
                    )}
                    <span className="font-bold text-slate-500">入场 {priceAction.pa_entry_price || '--'}</span>
                    <span className="font-bold text-rose-600">失效 {priceAction.pa_stop_price || '--'}</span>
                    <span className="font-bold text-emerald-700">目标 {priceAction.pa_target_price || '--'}</span>
                    {priceAction.pa_pullback_structure && (
                        <span className="font-bold text-blue-700">{priceAction.pa_pullback_structure}</span>
                    )}
                    {priceAction.pa_breakout_quality && (
                        <span className="font-bold text-slate-500">突破 {priceAction.pa_breakout_quality}</span>
                    )}
                    {priceAction.pa_failure_risk != null && (
                        <span className="font-bold text-amber-700">失败风险 {priceAction.pa_failure_risk}%</span>
                    )}
                    {priceAction.pa_h2_quality && priceAction.pa_h2_quality !== '不适用' && (
                        <span className="font-bold text-blue-700">H2 {priceAction.pa_h2_quality}</span>
                    )}
                    {priceAction.pa_failed_breakout_type && (
                        <span className="font-bold text-rose-600">{priceAction.pa_failed_breakout_type}</span>
                    )}
                    {priceAction.pa_micro_channel && priceAction.pa_micro_channel !== '无' && (
                        <span className="font-bold text-blue-700">{priceAction.pa_micro_channel}</span>
                    )}
                    {priceAction.pa_trend_damage && priceAction.pa_trend_damage !== '无' && (
                        <span className="font-bold text-rose-600">{priceAction.pa_trend_damage}</span>
                    )}
                    {priceAction.pa_always_in_strength != null && (
                        <span className="font-bold text-slate-500">AI强度 {priceAction.pa_always_in_strength}</span>
                    )}
                    {priceAction.pa_weekly_context && (
                        <span className="font-bold text-slate-500">{priceAction.pa_weekly_context}</span>
                    )}
                    {priceAction.pa_volume_pattern && priceAction.pa_volume_pattern !== '量能中性' && (
                        <span className="font-bold text-emerald-700">{priceAction.pa_volume_pattern}</span>
                    )}
                    {priceAction.pa_gap_type && priceAction.pa_gap_type !== '无缺口' && (
                        <span className="font-bold text-amber-700">{priceAction.pa_gap_type}</span>
                    )}
                    <span className="font-bold text-slate-400">评分 {priceAction.price_action_score ?? '--'}</span>
                </div>
            )}
            {chartData && (
                <SplitKLineCharts
                    candles={chartData.candlestick || []}
                    rfFilter={chartData.rf_filter || []}
                    trailingStops={chartData.trailing_stops || []}
                    markers={chartData.markers || []}
                    priceAction={chartData.price_action || null}
                    priceActionLines={chartData.price_action_lines || []}
                    height={400}
                />
            )}
            {loading && (
                <div className="absolute inset-0 flex items-center justify-center bg-white/70">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                </div>
            )}
            {error && (
                <div className="px-4 py-3 bg-red-50 text-red-500 text-sm font-bold">
                    Failed to load chart: {error}
                </div>
            )}
        </div>
    );
};

export default StockChart;
