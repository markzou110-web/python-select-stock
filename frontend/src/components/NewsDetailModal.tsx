"use client";

import React, { useState, useEffect } from 'react';
import { X, ExternalLink, Clock, TrendingUp, AlertTriangle, Loader2 } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface NewsItem {
    title: string;
    source: string;
    url: string;
    publish_time: string;
}

interface SentimentAnalysis {
    score: number;
    label: string;
    reason: string;
}

interface NewsDetailModalProps {
    isOpen: boolean;
    onClose: () => void;
    stockCode: string;
    stockName: string;
}

export default function NewsDetailModal({
    isOpen,
    onClose,
    stockCode,
    stockName
}: NewsDetailModalProps) {
    const [news, setNews] = useState<NewsItem[]>([]);
    const [sentiment, setSentiment] = useState<SentimentAnalysis | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    useEffect(() => {
        if (isOpen && stockCode) {
            fetchNews();
        }
    }, [isOpen, stockCode]);

    const fetchNews = async () => {
        try {
            setLoading(true);
            const res = await api.get(`/api/news/stock/${stockCode}`);
            setNews(res.data.data || []);
        } catch (e) {
            console.error("Failed to fetch news", e);
        } finally {
            setLoading(false);
        }
    };

    const handleRefresh = async () => {
        try {
            setRefreshing(true);
            await api.post(`/api/news/refresh/${stockCode}`);
            await fetchNews();
        } catch (e) {
            console.error("Failed to refresh news", e);
        } finally {
            setRefreshing(false);
        }
    };

    const getSentimentIcon = (label: string) => {
        switch (label) {
            case 'positive':
                return <TrendingUp className="text-emerald-500" size={20} />;
            case 'negative':
                return <AlertTriangle className="text-red-500" size={20} />;
            default:
                return <Clock className="text-slate-400" size={20} />;
        }
    };

    const getSentimentColor = (label: string) => {
        switch (label) {
            case 'positive':
                return 'bg-emerald-50 text-emerald-700 border-emerald-200';
            case 'negative':
                return 'bg-red-50 text-red-700 border-red-200';
            default:
                return 'bg-slate-50 text-slate-700 border-slate-200';
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[80vh] overflow-hidden flex flex-col m-4">
                {/* Header */}
                <div className="flex items-center justify-between p-6 border-b border-slate-200">
                    <div>
                        <h2 className="text-2xl font-bold text-slate-800">
                            {stockName} ({stockCode})
                        </h2>
                        <p className="text-sm text-slate-500 mt-1">相关新闻与舆情</p>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-2 hover:bg-slate-100 rounded-xl transition-colors"
                    >
                        <X size={24} className="text-slate-500" />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-6">
                    {loading ? (
                        <div className="flex items-center justify-center py-20">
                            <Loader2 size={32} className="animate-spin text-indigo-500" />
                        </div>
                    ) : news.length === 0 ? (
                        <div className="text-center py-20">
                            <Clock size={48} className="mx-auto text-slate-300 mb-4" />
                            <p className="text-slate-500 font-medium">暂无相关新闻</p>
                            <button
                                onClick={handleRefresh}
                                disabled={refreshing}
                                className="mt-4 px-4 py-2 bg-indigo-50 text-indigo-600 rounded-xl font-medium hover:bg-indigo-100 transition-colors disabled:opacity-50"
                            >
                                {refreshing ? '刷新中...' : '立即抓取'}
                            </button>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {/* Actions */}
                            <div className="flex items-center justify-between mb-6">
                                <span className="text-sm text-slate-500">
                                    找到 {news.length} 条相关新闻
                                </span>
                                <button
                                    onClick={handleRefresh}
                                    disabled={refreshing}
                                    className={cn(
                                        "flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition-colors",
                                        "bg-indigo-50 text-indigo-600 hover:bg-indigo-100",
                                        "disabled:opacity-50"
                                    )}
                                >
                                    {refreshing ? (
                                        <>
                                            <Loader2 size={16} className="animate-spin" />
                                            刷新中...
                                        </>
                                    ) : (
                                        '刷新新闻'
                                    )}
                                </button>
                            </div>

                            {/* News List */}
                            {news.map((item, index) => (
                                <div
                                    key={index}
                                    className="p-5 bg-slate-50 rounded-xl border border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 transition-all"
                                >
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex-1">
                                            <h4 className="font-semibold text-slate-800 mb-2 leading-snug">
                                                {item.title}
                                            </h4>
                                            <div className="flex items-center gap-3 text-xs text-slate-500">
                                                <span className="font-medium">{item.source}</span>
                                                <span>•</span>
                                                <span>{new Date(item.publish_time).toLocaleString('zh-CN')}</span>
                                            </div>
                                        </div>
                                        <a
                                            href={item.url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="flex-shrink-0 p-2 hover:bg-white rounded-lg transition-colors"
                                            title="查看原文"
                                        >
                                            <ExternalLink size={18} className="text-indigo-500" />
                                        </a>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-slate-200 bg-slate-50">
                    <p className="text-xs text-slate-400 text-center">
                        数据来源: 东方财富 • 更新时间: {new Date().toLocaleString('zh-CN')}
                    </p>
                </div>
            </div>
        </div>
    );
}
