import React, { useEffect, useState } from 'react';
import api from '@/lib/api';
import { 
    AreaChart, Area, XAxis, YAxis, CartesianGrid, 
    Tooltip, ResponsiveContainer, ReferenceLine 
} from 'recharts';

interface SentimentData {
    date: string;
    limit_up_count: number;
    limit_down_count: number;
    max_streak: number;
    sentiment_score: number;
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
                const [sentimentRes, historyRes] = await Promise.all([
                    api.get('/api/market/sentiment'),
                    api.get('/api/market/sentiment/history')
                ]);

                if (sentimentRes.data.error) {
                    setError(sentimentRes.data.error);
                } else {
                    setData(sentimentRes.data);
                }

                if (Array.isArray(historyRes.data)) {
                    setHistory(historyRes.data);
                }
            } catch (err: any) {
                setError(err.message || "Failed to fetch sentiment data");
            } finally {
                setLoading(false);
            }
        };
        fetchAll();
    }, []);

    if (loading) {
        return (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 flex items-center justify-center min-h-[120px]">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-indigo-600"></div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 text-sm text-gray-500 min-h-[120px] flex items-center">
                ⚠️ 暂无情绪数据: {error}
            </div>
        );
    }

    // Determine color based on score
    let scoreColor = "text-yellow-500";
    let bgPulse = "bg-yellow-50";
    let statusText = "震荡分化";
    
    if (data.sentiment_score >= 80) {
        scoreColor = "text-red-500";
        bgPulse = "bg-red-50";
        statusText = "极度亢奋 🔥";
    } else if (data.sentiment_score >= 60) {
        scoreColor = "text-orange-500";
        bgPulse = "bg-orange-50";
        statusText = "多头主导 📈";
    } else if (data.sentiment_score < 20) {
        scoreColor = "text-blue-600";
        bgPulse = "bg-blue-50";
        statusText = "冰点退潮 🧊";
    } else if (data.sentiment_score < 40) {
        scoreColor = "text-blue-400";
        bgPulse = "bg-blue-50";
        statusText = "空头压制 📉";
    }

    return (
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
            <div className="flex items-center justify-between mb-4">
                <h3 className="font-bold text-gray-800 flex items-center gap-2">
                    <span className="text-xl">🌡️</span> 市场情绪温度计
                </h3>
                <span className="text-xs text-gray-400">{data.date}</span>
            </div>

            <div className="flex flex-col lg:flex-row gap-6">
                {/* Left Section: Current Snapshot */}
                <div className="flex flex-col md:flex-row items-center gap-6 lg:w-1/2">
                    <div className="flex flex-col items-center">
                        <div className={`relative w-24 h-24 rounded-full flex items-center justify-center border-4 border-gray-100 ${bgPulse}`}>
                            <div className={`text-3xl font-bold ${scoreColor}`}>
                                {data.sentiment_score}
                            </div>
                        </div>
                        <div className={`mt-2 font-medium ${scoreColor}`}>{statusText}</div>
                    </div>

                    <div className="flex-1 w-full grid grid-cols-3 gap-3">
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                            <div className="text-xs text-gray-500 mb-1">涨停</div>
                            <div className="text-xl font-bold text-red-500">{data.limit_up_count}</div>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                            <div className="text-xs text-gray-500 mb-1">跌停</div>
                            <div className="text-xl font-bold text-green-500">{data.limit_down_count}</div>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-3 text-center">
                            <div className="text-xs text-gray-500 mb-1">连板</div>
                            <div className="text-xl font-bold text-orange-500">{data.max_streak}</div>
                        </div>
                    </div>
                </div>

                {/* Right Section: History Trend */}
                <div className="flex-1 h-32 lg:h-auto min-h-[120px]">
                    <div className="text-[10px] text-gray-400 mb-2 uppercase font-bold tracking-wider">最近 10 日情绪趋势 (涨跌停家数)</div>
                    {history.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={history}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
                                <XAxis 
                                    dataKey="date" 
                                    hide 
                                />
                                <YAxis hide domain={[0, 'auto']} />
                                <Tooltip 
                                    labelClassName="text-xs font-bold"
                                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}
                                />
                                <Area 
                                    type="monotone" 
                                    dataKey="up" 
                                    stroke="#ef4444" 
                                    fill="#fecaca" 
                                    name="涨停"
                                    strokeWidth={2}
                                />
                                <Area 
                                    type="monotone" 
                                    dataKey="down" 
                                    stroke="#22c55e" 
                                    fill="#bbf7d0" 
                                    name="跌停"
                                    strokeWidth={2}
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="h-full flex items-center justify-center text-gray-300 text-xs italic">
                            正在建立历史通道...
                        </div>
                    )}
                </div>
            </div>
            
            <div className="mt-4 w-full h-1.5 bg-gray-100 rounded-full overflow-hidden flex">
                <div 
                    className="h-full bg-red-400 transition-all duration-1000 ease-out"
                    style={{ width: `${data.sentiment_score}%` }}
                ></div>
                <div 
                    className="h-full bg-green-400 transition-all duration-1000 ease-out"
                    style={{ width: `${100 - data.sentiment_score}%` }}
                ></div>
            </div>
        </div>
    );
};

export default MarketSentiment;
