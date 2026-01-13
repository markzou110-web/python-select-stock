"use client";

import React, { useState, useEffect } from 'react';
import { AlertTriangle, X, ExternalLink, Clock } from 'lucide-react';
import api from '@/lib/api';
import { cn } from '@/lib/utils';

interface RiskEvent {
    stock_code: string;
    risk_type: string;
    risk_level: string;
    title: string;
    description: string;
    news_url: string;
    event_date: string;
}

interface RiskAlertProps {
    className?: string;
}

export default function RiskAlert({ className }: RiskAlertProps) {
    const [risks, setRisks] = useState<RiskEvent[]>([]);
    const [loading, setLoading] = useState(true);
    const [dismissed, setDismissed] = useState<Set<string>>(new Set());
    const [expanded, setExpanded] = useState(false);

    useEffect(() => {
        fetchRisks();
    }, []);

    const fetchRisks = async () => {
        try {
            const res = await api.get('/api/news/risks?days=7');
            const riskData = res.data.data || [];
            // 只显示高风险和中等风险
            setRisks(riskData.filter((r: RiskEvent) => r.risk_level !== 'low'));
        } catch (e) {
            console.error("Failed to fetch risks", e);
        } finally {
            setLoading(false);
        }
    };

    const visibleRisks = risks.filter(r => !dismissed.has(r.stock_code + r.event_date));

    if (loading || visibleRisks.length === 0) {
        return null;
    }

    const getRiskLevelColor = (level: string) => {
        switch (level) {
            case 'high':
                return 'bg-red-500';
            case 'medium':
                return 'bg-amber-500';
            default:
                return 'bg-slate-500';
        }
    };

    const getRiskLevelLabel = (level: string) => {
        switch (level) {
            case 'high':
                return '高风险';
            case 'medium':
                return '中风险';
            default:
                return '低风险';
        }
    };

    const getRiskTypeLabel = (type: string) => {
        const labels: Record<string, string> = {
            'financial': '财务',
            'operational': '经营',
            'market': '市场',
            'major': '重大'
        };
        return labels[type] || type;
    };

    return (
        <div className={cn("mb-6", className)}>
            {/* Alert Bar */}
            <div className="bg-gradient-to-r from-red-50 to-amber-50 border border-red-200 rounded-2xl p-4 shadow-lg">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="relative">
                            <AlertTriangle size={24} className="text-red-500 animate-pulse" />
                            <span className="absolute -top-1 -right-1 flex h-3 w-3">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
                            </span>
                        </div>
                        <div>
                            <h3 className="font-bold text-red-800">
                                风险预警通知
                            </h3>
                            <p className="text-sm text-red-600">
                                检测到 {visibleRisks.length} 只股票存在风险事件
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setExpanded(!expanded)}
                            className="px-4 py-2 bg-white text-red-600 rounded-xl font-bold text-sm hover:bg-red-50 transition-colors border border-red-200"
                        >
                            {expanded ? '收起' : '查看详情'}
                        </button>
                    </div>
                </div>

                {/* Expanded Details */}
                {expanded && (
                    <div className="mt-4 space-y-3 animate-in fade-in slide-in-from-top-2 duration-300">
                        {visibleRisks.map((risk, index) => (
                            <div
                                key={index}
                                className="bg-white rounded-xl p-4 border border-red-100 hover:border-red-200 transition-all"
                            >
                                <div className="flex items-start justify-between gap-4">
                                    <div className="flex-1">
                                        <div className="flex items-center gap-2 mb-2">
                                            <span className="font-bold text-slate-800">
                                                {risk.stock_code}
                                            </span>
                                            <span className={cn(
                                                "px-2 py-0.5 rounded-full text-xs font-bold text-white",
                                                getRiskLevelColor(risk.risk_level)
                                            )}>
                                                {getRiskLevelLabel(risk.risk_level)}
                                            </span>
                                            <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-slate-100 text-slate-600">
                                                {getRiskTypeLabel(risk.risk_type)}
                                            </span>
                                        </div>
                                        <h4 className="font-semibold text-slate-700 mb-1">
                                            {risk.title}
                                        </h4>
                                        <p className="text-sm text-slate-500 line-clamp-2">
                                            {risk.description}
                                        </p>
                                        <div className="flex items-center gap-2 mt-2 text-xs text-slate-400">
                                            <Clock size={12} />
                                            <span>{new Date(risk.event_date).toLocaleDateString('zh-CN')}</span>
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-2">
                                        <a
                                            href={risk.news_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="p-2 hover:bg-slate-100 rounded-lg transition-colors"
                                            title="查看原文"
                                        >
                                            <ExternalLink size={16} className="text-slate-500" />
                                        </a>
                                        <button
                                            onClick={() => {
                                                const newDismissed = new Set(dismissed);
                                                newDismissed.add(risk.stock_code + risk.event_date);
                                                setDismissed(newDismissed);
                                            }}
                                            className="p-2 hover:bg-slate-100 rounded-lg transition-colors"
                                            title="忽略此条"
                                        >
                                            <X size={16} className="text-slate-400" />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
