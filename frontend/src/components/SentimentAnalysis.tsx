"use client";

import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Minus, BarChart3 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SentimentData {
    positive: number;
    negative: number;
    neutral: number;
    average_score: number;
    trend: 'up' | 'down' | 'stable';
}

interface SentimentAnalysisProps {
    stockCode: string;
    stockName: string;
}

export default function SentimentAnalysis({ stockCode, stockName }: SentimentAnalysisProps) {
    const [sentiment, setSentiment] = useState<SentimentData | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetchSentiment();
    }, [stockCode]);

    const fetchSentiment = async () => {
        try {
            // TODO: 调用后端 API 获取情绪分析数据
            // const res = await api.get(`/api/news/sentiment/${stockCode}`);
            // setSentiment(res.data);

            // 模拟数据
            setSentiment({
                positive: 15,
                negative: 3,
                neutral: 7,
                average_score: 2.3,
                trend: 'up'
            });
        } catch (e) {
            console.error("Failed to fetch sentiment", e);
        } finally {
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="animate-pulse bg-slate-100 rounded-xl p-4 h-32">
                <div className="h-4 bg-slate-200 rounded w-1/3 mb-3"></div>
                <div className="h-8 bg-slate-200 rounded w-2/3"></div>
            </div>
        );
    }

    if (!sentiment) return null;

    const total = sentiment.positive + sentiment.negative + sentiment.neutral;
    const positivePct = total > 0 ? Math.round((sentiment.positive / total) * 100) : 0;
    const negativePct = total > 0 ? Math.round((sentiment.negative / total) * 100) : 0;
    const neutralPct = total > 0 ? Math.round((sentiment.neutral / total) * 100) : 0;

    return (
        <div className="bg-gradient-to-br from-slate-50 to-slate-100 rounded-xl p-5 border border-slate-200">
            {/* Header */}
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <BarChart3 size={18} className="text-indigo-600" />
                    <h4 className="font-bold text-slate-800">情绪分析</h4>
                </div>
                <div className={cn(
                    "flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold",
                    sentiment.trend === 'up' && "bg-emerald-100 text-emerald-700",
                    sentiment.trend === 'down' && "bg-red-100 text-red-700",
                    sentiment.trend === 'stable' && "bg-slate-100 text-slate-700"
                )}>
                    {sentiment.trend === 'up' && <TrendingUp size={14} />}
                    {sentiment.trend === 'down' && <TrendingDown size={14} />}
                    {sentiment.trend === 'stable' && <Minus size={14} />}
                    <span>
                        {sentiment.trend === 'up' ? '看涨' : sentiment.trend === 'down' ? '看跌' : '中性'}
                    </span>
                </div>
            </div>

            {/* Score */}
            <div className="mb-4">
                <div className="flex items-end justify-between mb-2">
                    <span className="text-xs text-slate-500 font-medium">情绪评分</span>
                    <span className={cn(
                        "text-2xl font-black",
                        sentiment.average_score > 2 ? "text-emerald-600" :
                        sentiment.average_score < -2 ? "text-red-600" : "text-slate-600"
                    )}>
                        {sentiment.average_score > 0 ? '+' : ''}{sentiment.average_score.toFixed(1)}
                    </span>
                </div>
                {/* Progress Bar */}
                <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
                    <div
                        className={cn(
                            "h-full transition-all duration-500",
                            sentiment.average_score > 2 ? "bg-emerald-500" :
                            sentiment.average_score < -2 ? "bg-red-500" : "bg-slate-400"
                        )}
                        style={{
                            width: `${Math.min(100, Math.max(0, ((sentiment.average_score + 5) / 10) * 100))}%`
                        }}
                    />
                </div>
                <div className="flex justify-between mt-1 text-[10px] text-slate-400 font-medium">
                    <span>-5</span>
                    <span>0</span>
                    <span>+5</span>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-3">
                {/* Positive */}
                <div className="bg-white rounded-lg p-3 border border-emerald-100">
                    <div className="flex items-center gap-1 mb-1">
                        <TrendingUp size={12} className="text-emerald-500" />
                        <span className="text-xs text-slate-500">正面</span>
                    </div>
                    <div className="text-lg font-black text-emerald-600">{sentiment.positive}</div>
                    <div className="text-[10px] text-emerald-500 font-bold">{positivePct}%</div>
                </div>

                {/* Neutral */}
                <div className="bg-white rounded-lg p-3 border border-slate-100">
                    <div className="flex items-center gap-1 mb-1">
                        <Minus size={12} className="text-slate-400" />
                        <span className="text-xs text-slate-500">中性</span>
                    </div>
                    <div className="text-lg font-black text-slate-600">{sentiment.neutral}</div>
                    <div className="text-[10px] text-slate-400 font-bold">{neutralPct}%</div>
                </div>

                {/* Negative */}
                <div className="bg-white rounded-lg p-3 border border-red-100">
                    <div className="flex items-center gap-1 mb-1">
                        <TrendingDown size={12} className="text-red-500" />
                        <span className="text-xs text-slate-500">负面</span>
                    </div>
                    <div className="text-lg font-black text-red-600">{sentiment.negative}</div>
                    <div className="text-[10px] text-red-500 font-bold">{negativePct}%</div>
                </div>
            </div>
        </div>
    );
}
