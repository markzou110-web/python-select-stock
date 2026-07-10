import React, { useEffect, useState } from 'react';
import api from '@/lib/api';
import { 
    AreaChart, Area, XAxis, YAxis, CartesianGrid, 
    Tooltip, ResponsiveContainer
} from 'recharts';

interface SentimentData {
    date: string;
    limit_up_count: number;
    limit_down_count: number;
    max_streak: number;
    sentiment_score: number;
    market_sentiment_label?: string;
    market_sentiment_reason?: string;
    market_breadth?: {
        advance_ratio?: number;
        strong_ratio?: number;
        weak_ratio?: number;
        limit_up_ratio?: number;
        limit_down_ratio?: number;
    };
    portfolio_position_cap_pct?: number;
    market_allowed_actions?: string[];
    market_forbidden_actions?: string[];
    error?: string;
}

interface SentimentHistoryItem {
    date: string;
    up: number;
    down: number;
}

const MarketSentiment: React.FC = () => {
    const [data, setData] = useState<SentimentData | null>(null);
    const [history, setHistory] = useState<SentimentHistoryItem[]>([]);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchAll = async () => {
            try {
                setLoading(true);
                const [sentimentResult, historyResult] = await Promise.allSettled([
                    api.get('/api/market/sentiment'),
                    api.get('/api/market/sentiment/history')
                ]);

                if (sentimentResult.status === 'rejected') {
                    throw sentimentResult.reason;
                }

                const sentimentRes = sentimentResult.value;
                if (sentimentRes.data.error) {
                    setError(sentimentRes.data.error);
                } else {
                    setData(sentimentRes.data);
                }

                if (historyResult.status === 'fulfilled' && Array.isArray(historyResult.value.data)) {
                    const historyRes = historyResult.value;
                    setHistory(historyRes.data);
                }
            } catch (err: unknown) {
                setError(err instanceof Error ? err.message : "Failed to fetch sentiment data");
            } finally {
                setLoading(false);
            }
        };
        fetchAll();
    }, []);

    if (loading) {
        return (
            <div className="workspace-panel p-4 flex items-center justify-center min-h-[96px]">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-700"></div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="workspace-panel p-4 text-sm text-slate-500 min-h-[96px] flex items-center">
                暂无情绪数据: {error}
            </div>
        );
    }

    // Determine color based on score
    let scoreColor = "text-yellow-500";
    let bgPulse = "bg-yellow-50";
    let statusText = "震荡分化";
    
    if (data.sentiment_score >= 80) {
        scoreColor = "text-rose-600";
        bgPulse = "bg-rose-50";
        statusText = "极度亢奋";
    } else if (data.sentiment_score >= 60) {
        scoreColor = "text-amber-600";
        bgPulse = "bg-amber-50";
        statusText = "多头主导";
    } else if (data.sentiment_score < 20) {
        scoreColor = "text-blue-700";
        bgPulse = "bg-blue-50";
        statusText = "冰点退潮";
    } else if (data.sentiment_score < 40) {
        scoreColor = "text-blue-600";
        bgPulse = "bg-blue-50";
        statusText = "空头压制";
    }

    return (
        <div className="workspace-panel p-3">
            <div className="flex items-center justify-between mb-2">
                <h3 className="font-black text-slate-900 flex items-center gap-2">
                    市场情绪温度计
                </h3>
                <span className="metric-label">{data.date}</span>
            </div>

            <div className="flex flex-col lg:flex-row gap-3">
                {/* Left Section: Current Snapshot */}
                <div className="flex flex-col md:flex-row items-center gap-3 lg:w-[42%]">
                    <div className="flex flex-col items-center">
                        <div className={`relative w-16 h-16 rounded-lg flex items-center justify-center border border-slate-200 ${bgPulse}`}>
                            <div className={`text-2xl font-black font-mono ${scoreColor}`}>
                                {data.sentiment_score}
                            </div>
                        </div>
                        <div className={`mt-2 text-xs font-black ${scoreColor}`}>{data.market_sentiment_label || statusText}</div>
                        {typeof data.market_breadth?.advance_ratio === 'number' && (
                            <div className="mt-0.5 text-[10px] font-bold text-slate-400">
                                仅 {data.market_breadth.advance_ratio.toFixed(0)}% 个股上涨
                            </div>
                        )}
                        <div className="mt-1 text-[10px] font-bold text-slate-500">
                            总仓上限 {data.portfolio_position_cap_pct ?? '--'}%
                        </div>
                    </div>

                    <div className="flex-1 w-full grid grid-cols-3 gap-2">
                        <div className="bg-slate-50 rounded-lg p-2 text-center border border-slate-100">
                            <div className="metric-label mb-1">涨停</div>
                            <div className="text-lg font-black font-mono text-rose-600">{data.limit_up_count}</div>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-2 text-center border border-slate-100">
                            <div className="metric-label mb-1">跌停</div>
                            <div className="text-lg font-black font-mono text-teal-600">{data.limit_down_count}</div>
                        </div>
                        <div className="bg-slate-50 rounded-lg p-2 text-center border border-slate-100">
                            <div className="metric-label mb-1">连板</div>
                            <div className="text-lg font-black font-mono text-amber-600">{data.max_streak}</div>
                        </div>
                    </div>
                </div>

                {/* Right Section: History Trend */}
                <div className="flex-1 h-24 lg:h-auto min-h-[96px]">
                    <div className="metric-label mb-2">最近 10 日情绪趋势 (涨跌停家数)</div>
                    {history.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={history}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
                                <XAxis 
                                    dataKey="date" 
                                    hide 
                                />
                                <YAxis hide domain={[0, 'auto']} />
                                <Tooltip 
                                    labelClassName="text-xs font-bold"
                                    contentStyle={{ borderRadius: '8px', border: '1px solid #e2e8f0', boxShadow: '0 8px 20px rgba(15,23,42,0.08)' }}
                                />
                                <Area 
                                    type="monotone" 
                                    dataKey="up" 
                                    stroke="#e11d48"
                                    fill="#fecaca" 
                                    name="涨停"
                                    strokeWidth={2}
                                />
                                <Area 
                                    type="monotone" 
                                    dataKey="down" 
                                    stroke="#0d9488"
                                    fill="#ccfbf1"
                                    name="跌停"
                                    strokeWidth={2}
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="h-full flex items-center justify-center text-slate-300 text-xs italic">
                            正在建立历史通道...
                        </div>
                    )}
                </div>
            </div>

            {data.market_sentiment_reason && (
                <div className="mt-2 text-[11px] font-semibold text-slate-500">
                    💡 {data.market_sentiment_reason}
                </div>
            )}

            <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px] font-semibold">
                <div className="border-l-2 border-emerald-500 pl-2 text-emerald-700">
                    允许：{data.market_allowed_actions?.join('、') || '等待市场信号'}
                </div>
                <div className="border-l-2 border-rose-500 pl-2 text-rose-700">
                    禁止：{data.market_forbidden_actions?.join('、') || '无'}
                </div>
            </div>
            
            <div className="mt-2 w-full h-1.5 bg-slate-100 rounded-full overflow-hidden flex">
                <div 
                    className="h-full bg-rose-500 transition-all duration-1000 ease-out"
                    style={{ width: `${data.sentiment_score}%` }}
                ></div>
                <div 
                    className="h-full bg-teal-500 transition-all duration-1000 ease-out"
                    style={{ width: `${100 - data.sentiment_score}%` }}
                ></div>
            </div>
        </div>
    );
};

export default MarketSentiment;
