"use client";

import React, { useState, useEffect } from 'react';
import { X, TrendingUp, TrendingDown, Search, Filter } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface ThemeStock {
    code: string;
    name: string;
    price: number;
    change_pct: number;
    volume: number;
    relevance: number;
}

interface ThemeStocksProps {
    isOpen: boolean;
    onClose: () => void;
    themeName: string;
    themeId: number;
}

export default function ThemeStocks({ isOpen, onClose, themeName, themeId }: ThemeStocksProps) {
    const [stocks, setStocks] = useState<ThemeStock[]>([]);
    const [loading, setLoading] = useState(true);
    const [sortConfig, setSortConfig] = useState<'change_pct' | 'volume' | 'relevance'>('relevance');

    useEffect(() => {
        if (isOpen && themeId) {
            fetchThemeStocks();
        }
    }, [isOpen, themeId]);

    const fetchThemeStocks = async () => {
        try {
            setLoading(true);
            // TODO: 调用后端 API 获取题材成分股
            // const res = await api.get(`/api/news/themes/${themeId}/stocks`);
            // setStocks(res.data);

            // 模拟数据
            setStocks([
                { code: '300750', name: '宁德时代', price: 185.50, change_pct: 3.2, volume: 1500000000, relevance: 0.95 },
                { code: '688981', name: '中芯国际', price: 52.30, change_pct: -1.2, volume: 890000000, relevance: 0.88 },
                { code: '603259', name: '药明康德', price: 78.90, change_pct: 1.5, volume: 650000000, relevance: 0.82 },
                { code: '002594', name: '比亚迪', price: 258.40, change_pct: 2.8, volume: 2100000000, relevance: 0.78 },
                { code: '300015', name: '爱尔眼科', price: 15.80, change_pct: -0.5, volume: 450000000, relevance: 0.75 },
            ]);
        } catch (e) {
            console.error("Failed to fetch theme stocks", e);
        } finally {
            setLoading(false);
        }
    };

    const sortedStocks = [...stocks].sort((a, b) => {
        return b[sortConfig] - a[sortConfig];
    });

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[80vh] overflow-hidden flex flex-col m-4">
                {/* Header */}
                <div className="flex items-center justify-between p-6 border-b border-slate-200">
                    <div>
                        <h2 className="text-2xl font-bold text-slate-800">
                            {themeName} - 成分股
                        </h2>
                        <p className="text-sm text-slate-500 mt-1">
                            共 {stocks.length} 只股票
                        </p>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-2 hover:bg-slate-100 rounded-xl transition-colors"
                    >
                        <X size={24} className="text-slate-500" />
                    </button>
                </div>

                {/* Filters */}
                <div className="flex items-center gap-3 px-6 py-4 border-b border-slate-100 bg-slate-50">
                    <Filter size={16} className="text-slate-400" />
                    <span className="text-sm font-bold text-slate-600">排序:</span>
                    <button
                        onClick={() => setSortConfig('relevance')}
                        className={cn(
                            "px-3 py-1.5 rounded-lg text-xs font-bold transition-colors",
                            sortConfig === 'relevance'
                                ? "bg-indigo-100 text-indigo-700"
                                : "bg-white text-slate-600 hover:bg-slate-100"
                        )}
                    >
                        相关度
                    </button>
                    <button
                        onClick={() => setSortConfig('change_pct')}
                        className={cn(
                            "px-3 py-1.5 rounded-lg text-xs font-bold transition-colors",
                            sortConfig === 'change_pct'
                                ? "bg-indigo-100 text-indigo-700"
                                : "bg-white text-slate-600 hover:bg-slate-100"
                        )}
                    >
                        涨跌幅
                    </button>
                    <button
                        onClick={() => setSortConfig('volume')}
                        className={cn(
                            "px-3 py-1.5 rounded-lg text-xs font-bold transition-colors",
                            sortConfig === 'volume'
                                ? "bg-indigo-100 text-indigo-700"
                                : "bg-white text-slate-600 hover:bg-slate-100"
                        )}
                    >
                        成交额
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto">
                    {loading ? (
                        <div className="flex items-center justify-center py-20">
                            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600"></div>
                        </div>
                    ) : (
                        <table className="w-full">
                            <thead className="bg-slate-50 sticky top-0">
                                <tr className="text-left text-xs text-slate-500 font-bold uppercase tracking-wider">
                                    <th className="px-6 py-3">股票</th>
                                    <th className="px-6 py-3">现价</th>
                                    <th className="px-6 py-3">涨跌幅</th>
                                    <th className="px-6 py-3">成交额</th>
                                    <th className="px-6 py-3">相关度</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {sortedStocks.map((stock) => (
                                    <tr
                                        key={stock.code}
                                        className="hover:bg-slate-50 transition-colors cursor-pointer"
                                        onClick={() => window.open(`https://quote.eastmoney.com/${stock.code.startsWith('6') || stock.code.startsWith('688') ? 'SH' : 'SZ'}${stock.code}.html`, '_blank')}
                                    >
                                        <td className="px-6 py-4">
                                            <div>
                                                <div className="font-bold text-slate-800">{stock.name}</div>
                                                <div className="text-xs text-slate-500 font-mono">{stock.code}</div>
                                            </div>
                                        </td>
                                        <td className="px-6 py-4">
                                            <span className="font-bold text-slate-700">
                                                ¥{stock.price.toFixed(2)}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4">
                                            <div className={cn(
                                                "inline-flex items-center gap-1 px-2 py-1 rounded-lg text-sm font-bold",
                                                stock.change_pct >= 0
                                                    ? "bg-rose-50 text-rose-600"
                                                    : "bg-emerald-50 text-emerald-600"
                                            )}>
                                                {stock.change_pct >= 0 ? (
                                                    <TrendingUp size={14} />
                                                ) : (
                                                    <TrendingDown size={14} />
                                                )}
                                                {stock.change_pct >= 0 ? '+' : ''}{stock.change_pct.toFixed(2)}%
                                            </div>
                                        </td>
                                        <td className="px-6 py-4">
                                            <span className="text-sm text-slate-600">
                                                {(stock.volume / 100000000).toFixed(2)} 亿
                                            </span>
                                        </td>
                                        <td className="px-6 py-4">
                                            <div className="flex items-center gap-2">
                                                <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden max-w-[80px]">
                                                    <div
                                                        className="h-full bg-indigo-500 rounded-full"
                                                        style={{ width: `${stock.relevance * 100}%` }}
                                                    />
                                                </div>
                                                <span className="text-xs font-bold text-slate-600">
                                                    {Math.round(stock.relevance * 100)}%
                                                </span>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Footer */}
                <div className="p-4 border-t border-slate-200 bg-slate-50">
                    <p className="text-xs text-slate-400 text-center">
                        点击股票查看详情 • 数据仅供参考，不构成投资建议
                    </p>
                </div>
            </div>
        </div>
    );
}
