import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts';
import api from '@/lib/api';

interface StockChartProps {
    code: string;
    name: string;
    strategyType?: string;
}

const StockChart: React.FC<StockChartProps> = ({ code, name, strategyType }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let chart: IChartApi | null = null;
        let candlestickSeries: ISeriesApi<"Candlestick"> | null = null;
        let rfSeries: ISeriesApi<"Line"> | null = null;
        let trailingSeries: ISeriesApi<"Line"> | null = null;

        const fetchDataAndRender = async () => {
            try {
                setLoading(true);
                const response = await api.get(`/api/kline/${code}?strategy_type=${strategyType || 'squeeze'}`);
                const data = response.data;

                if (!chartContainerRef.current) return;

                // Initialize chart
                chart = createChart(chartContainerRef.current, {
                    layout: {
                        background: { type: ColorType.Solid, color: '#ffffff' },
                        textColor: '#333',
                    },
                    grid: {
                        vertLines: { color: '#f0f3fa' },
                        horzLines: { color: '#f0f3fa' },
                    },
                    width: chartContainerRef.current.clientWidth,
                    height: 400,
                    timeScale: {
                        timeVisible: true,
                        borderColor: '#D1D4DC',
                    },
                });

                // Add Candlestick Series
                candlestickSeries = chart.addSeries(CandlestickSeries, {
                    upColor: '#ef5350',
                    downColor: '#26a69a',
                    borderVisible: false,
                    wickUpColor: '#ef5350',
                    wickDownColor: '#26a69a',
                });
                
                // Need to filter out rows with missing open/high/low/close values which occasionally happen
                const validCandles = data.candlestick.filter((c: any) => 
                    c.open !== null && c.high !== null && c.low !== null && c.close !== null
                );
                candlestickSeries!.setData(validCandles);

                // Add RF Filter Line
                if (data.rf_filter && data.rf_filter.length > 0) {
                    rfSeries = chart.addSeries(LineSeries, {
                        color: '#ff9800',
                        lineWidth: 2,
                        crosshairMarkerVisible: false,
                        lastValueVisible: false,
                        priceLineVisible: false,
                    });
                    
                    const validRf = data.rf_filter.filter((r: any) => r.value !== null && !isNaN(r.value));
                    rfSeries!.setData(validRf);
                }

                // Add Trailing Stop Line (红色虚线：跟踪止损止盈轨迹)
                if (data.trailing_stops && data.trailing_stops.length > 0) {
                    trailingSeries = chart.addSeries(LineSeries, {
                        color: '#f44336',
                        lineWidth: 2,
                        lineStyle: 2, // 2 = Dotted line style in lightweight-charts
                        crosshairMarkerVisible: false,
                        lastValueVisible: false,
                        priceLineVisible: false,
                    });

                    const validTrailing = data.trailing_stops
                        .map((t: any) => ({
                            time: t.time,
                            value: parseFloat(t.value)
                        }))
                        .filter((t: any) => !isNaN(t.value));

                    trailingSeries!.setData(validTrailing);
                }

                if (data.markers && data.markers.length > 0) {
                    const markersPlugin = createSeriesMarkers(candlestickSeries!);
                    markersPlugin.setMarkers(data.markers);
                }

                chart.timeScale().fitContent();

            } catch (err: any) {
                setError(err.message || 'Error loading chart data');
            } finally {
                setLoading(false);
            }
        };

        fetchDataAndRender();

        const handleResize = () => {
            if (chartContainerRef.current && chart) {
                chart.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            if (chart) {
                chart.remove();
            }
        };
    }, [code, strategyType]);

    if (loading) {
        return (
            <div className="w-full h-[400px] flex items-center justify-center bg-slate-50 rounded-lg">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="w-full h-[400px] flex items-center justify-center bg-red-50 text-red-500 rounded-lg">
                Failed to load chart: {error}
            </div>
        );
    }

    return (
        <div className="w-full bg-white rounded-lg overflow-hidden border border-slate-200">
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
            <div ref={chartContainerRef} className="w-full h-[400px]" />
        </div>
    );
};

export default StockChart;
