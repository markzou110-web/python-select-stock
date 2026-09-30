"use client";

import React, { useState, useEffect } from 'react';
import {
    Clock,
    Bell,
    Save,
    Settings as SettingsIcon,
    ShieldCheck,
    Cpu,
    Database,
    CheckCircle2,
    Loader2,
    Activity,
    AlertTriangle
} from 'lucide-react';
import { cn } from '@/lib/utils';
import api, { hasSessionApiToken, setSessionApiToken } from '@/lib/api';

interface SystemHealthCheck {
    name: string;
    status: 'ok' | 'warn' | 'error';
    message: string;
}

interface SystemHealth {
    status: 'ok' | 'warn' | 'error';
    time: string;
    score: number;
    checks: SystemHealthCheck[];
    summary: {
        stock_count?: number;
        daily_k_count?: number;
        scan_history_count?: number;
        open_positions?: number;
        watchlist_count?: number;
        strategy_template_count?: number;
        latest_daily_date?: string | null;
        latest_scan_date?: string | null;
    };
    recommendations: string[];
}

interface OperationalMetrics {
    status: 'ok' | 'warning' | 'critical';
    metrics: Record<string, string | number | null>;
    alerts: Array<{ severity: string; code: string; message: string }>;
}

interface PointInTimeCoverage {
    status: 'ok' | 'collecting' | 'error';
    summary: {
        snapshots?: { dates?: number; rows?: number };
        scan_audits?: { dates?: number };
        events?: { verified?: number };
    };
    gaps: string[];
    target?: { trading_dates?: number; verified_events?: number };
}

interface StrategyReleaseState {
    strategy_key: string;
    state: string;
    version?: string;
    updated_at?: string;
}

interface AIStatus {
    enabled?: boolean;
    configured?: boolean;
    model?: string | null;
    provider?: string;
    max_candidates?: number;
}

interface BarkStrategyOption {
    value: string;
    label: string;
    description: string;
}

interface AppSettings {
    sentinel_schedule_times: string;
    market_sync_schedule_times: string;
    bark_key: string;
    bark_key_set?: boolean;
    bark_key_masked?: string;
    bark_scan_strategy: string;
    bark_scan_strategy_options?: BarkStrategyOption[];
}

const DEFAULT_BARK_STRATEGY_OPTIONS: BarkStrategyOption[] = [
    { value: 'tv_zp', label: 'TV-ZP趋势信号', description: 'Range Filter 主导，Volume/QQE 确认' },
    { value: 'tv_dual', label: 'TV 宽松观察池', description: '均线B共振或 TV-ZP 趋势信号' },
    { value: 'tv_dual_strict', label: 'TV+ 强确认精选', description: '均线B共振且 TV-ZP 趋势信号' },
];

export default function SettingsView() {
    const [settings, setSettings] = useState<AppSettings>({
        sentinel_schedule_times: "09:30,10:00,10:30,11:00,13:00,13:30,14:00,14:30",
        market_sync_schedule_times: "08:30,12:10,18:00",
        bark_key: "",
        bark_scan_strategy: "tv_zp",
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
    const [operationalMetrics, setOperationalMetrics] = useState<OperationalMetrics | null>(null);
    const [pointInTimeCoverage, setPointInTimeCoverage] = useState<PointInTimeCoverage | null>(null);
    const [strategyStates, setStrategyStates] = useState<StrategyReleaseState[]>([]);
    const [apiToken, setApiToken] = useState('');
    const [apiTokenActive, setApiTokenActive] = useState(false);
    const [retryingNotifications, setRetryingNotifications] = useState(false);
    const [notificationRetryMessage, setNotificationRetryMessage] = useState('');
    const [aiStatus, setAIStatus] = useState<AIStatus | null>(null);

    useEffect(() => {
        setApiTokenActive(hasSessionApiToken());
        fetchSettings();
    }, []);

    const fetchSettings = async () => {
        const results = await Promise.allSettled([
                api.get('/api/settings'),
                api.get('/api/system/health'),
                api.get('/api/system/operational-metrics'),
                api.get('/api/system/point-in-time-coverage'),
                api.get('/api/system/strategy-release/states'),
                api.get('/api/ai/status')
        ]);
        if (results[0].status === 'fulfilled') setSettings(results[0].value.data);
        if (results[1].status === 'fulfilled') setSystemHealth(results[1].value.data);
        if (results[2].status === 'fulfilled') setOperationalMetrics(results[2].value.data);
        if (results[3].status === 'fulfilled') setPointInTimeCoverage(results[3].value.data);
        if (results[4].status === 'fulfilled') setStrategyStates(results[4].value.data);
        if (results[5].status === 'fulfilled') setAIStatus(results[5].value.data);
        results.forEach(result => {
            if (result.status === 'rejected') console.error("Fetch Settings Diagnostic Error:", result.reason);
        });
        setLoading(false);
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.post('/api/settings', settings);
            setSaved(true);
            setTimeout(() => setSaved(false), 3000);
        } catch (err) {
            console.error("Save Settings Error:", err);
        } finally {
            setSaving(false);
        }
    };

    const handleClearBarkKey = async () => {
        setSaving(true);
        try {
            await api.post('/api/settings', { bark_key: '__CLEAR__' });
            setSettings(prev => ({ ...prev, bark_key: '', bark_key_set: false, bark_key_masked: '' }));
        } catch (err) {
            console.error("Clear Bark Key Error:", err);
        } finally {
            setSaving(false);
        }
    };

    const handleRetryDeadNotifications = async () => {
        setRetryingNotifications(true);
        setNotificationRetryMessage('');
        try {
            const response = await api.post('/api/system/notification-outbox/retry-dead');
            const count = Number(response.data?.requeued || 0);
            setNotificationRetryMessage(count > 0 ? `已重新排队 ${count} 条通知` : '没有需要重新排队的通知');
            await fetchSettings();
        } catch (err) {
            console.error('Notification retry error:', err);
            setNotificationRetryMessage('重新排队失败，请检查 API 令牌或后端状态');
        } finally {
            setRetryingNotifications(false);
        }
    };

    if (loading) {
        return (
            <div className="flex-1 flex items-center justify-center p-20 text-slate-400">
                <Loader2 className="animate-spin mr-2" /> 正在加载系统配置...
            </div>
        );
    }

    const barkStrategyOptions = settings.bark_scan_strategy_options?.length
        ? settings.bark_scan_strategy_options
        : DEFAULT_BARK_STRATEGY_OPTIONS;
    const selectedBarkStrategy = barkStrategyOptions.find(
        option => option.value === settings.bark_scan_strategy
    );

    return (
        <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-indigo-600 text-white rounded-2xl flex items-center justify-center shadow-lg shadow-indigo-100">
                        <SettingsIcon size={24} />
                    </div>
                    <div>
                        <h2 className="text-2xl font-black text-slate-900">系统配置</h2>
                        <p className="text-slate-400 font-bold text-xs uppercase tracking-widest mt-1">System Terminal Configuration</p>
                    </div>
                </div>

                <button
                    onClick={handleSave}
                    disabled={saving}
                    className={cn(
                        "flex items-center gap-2 px-6 py-3 rounded-2xl font-bold text-sm transition-all shadow-xl active:scale-95",
                        saved
                            ? "bg-emerald-500 text-white shadow-emerald-100"
                            : "bg-indigo-600 text-white hover:bg-indigo-700 shadow-indigo-100"
                    )}
                >
                    {saving ? <Loader2 size={18} className="animate-spin" /> : (saved ? <CheckCircle2 size={18} /> : <Save size={18} />)}
                    {saved ? "设置已保存" : "保存全局设置"}
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                {/* Automation Section */}
                <div className="space-y-6">
                    <div className="flex items-center gap-2 px-2 text-slate-400">
                        <Cpu size={18} />
                        <h3 className="font-black text-[10px] uppercase tracking-widest">自动化工作流</h3>
                    </div>

                    <div className="glass-card p-6 space-y-6">
                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <ShieldCheck size={16} className="text-emerald-500" />
                                写操作 API Token（仅当前标签页会话）
                            </label>
                            <div className="flex gap-2">
                                <input
                                    type="password"
                                    value={apiToken}
                                    onChange={(e) => setApiToken(e.target.value)}
                                    placeholder={apiTokenActive ? "会话令牌已设置" : "输入 API_TOKEN"}
                                    className="flex-1 bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none"
                                />
                                <button
                                    type="button"
                                    onClick={() => { setSessionApiToken(apiToken); setApiTokenActive(Boolean(apiToken.trim())); setApiToken(''); }}
                                    className="px-4 rounded-xl bg-emerald-600 text-white text-xs font-black"
                                >保存会话</button>
                            </div>
                            <p className="text-[10px] text-slate-400">令牌只保存在 sessionStorage，关闭标签页后失效，不写入数据库或构建产物。状态：{apiTokenActive ? '已启用' : '未设置'}</p>
                        </div>
                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <Clock size={16} className="text-indigo-500" />
                                哨兵自动巡检时间点
                            </label>
                            <input
                                type="text"
                                value={settings.sentinel_schedule_times || ""}
                                onChange={(e) => setSettings({ ...settings, sentinel_schedule_times: e.target.value })}
                                placeholder="例如: 09:30,10:00,10:30,11:00,13:00,13:30,14:00,14:30"
                                className="w-full bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-indigo-500/10 focus:bg-white transition-all"
                            />
                            <p className="text-[10px] text-slate-400 font-medium leading-relaxed">
                                💡 默认从开盘起每30分钟扫描一次（午间休市暂停）。支持用英文逗号自定义时间点；系统会获取最新行情、分析全市场标的，并根据状态去重推送。
                            </p>
                        </div>

                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <Database size={16} className="text-emerald-500" />
                                行情自动同步时间点
                            </label>
                            <input
                                type="text"
                                value={settings.market_sync_schedule_times || ""}
                                onChange={(e) => setSettings({ ...settings, market_sync_schedule_times: e.target.value })}
                                placeholder="例如: 08:30, 12:10, 18:00"
                                className="w-full bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-emerald-500/10 focus:bg-white transition-all"
                            />
                            <p className="text-[10px] text-slate-400 font-medium leading-relaxed">
                                默认盘前、中午、晚上各同步一次行情数据，用于板块雷达、选股和次日跟踪。请保持后端服务运行。
                            </p>
                        </div>

                        <div className="p-4 bg-amber-50 rounded-2xl border border-amber-100/50 flex gap-3">
                            <span className="text-xl">⚠️</span>
                            <div className="text-[10px] text-amber-700 font-medium leading-tight">
                                提示：请确保程序在此时间段处于运行状态。建议设置在下午 14:00 - 15:00 之间，以获得最准的确诊信号。
                            </div>
                        </div>
                        <div className="rounded-2xl border border-violet-100 bg-violet-50/70 p-4">
                            <div className="flex items-center gap-2 text-sm font-black text-violet-800">
                                <Cpu size={16} /> AI 二次选股复核
                            </div>
                            <p className="mt-2 text-[10px] font-bold text-violet-700">
                                状态：{aiStatus?.configured ? '已配置' : '未配置'}{aiStatus?.model ? ` · ${aiStatus.model}` : ''}
                            </p>
                            <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
                                在扫描结果点击“AI二次分析”。密钥只从后端环境变量读取，不会写入浏览器或数据库；单次最多复核 {aiStatus?.max_candidates || 10} 只。
                            </p>
                        </div>
                    </div>
                </div>

                {/* Notifications Section */}
                <div className="space-y-6">
                    <div className="flex items-center gap-2 px-2 text-slate-400">
                        <Bell size={18} />
                        <h3 className="font-black text-[10px] uppercase tracking-widest">消息通知服务</h3>
                    </div>

                    <div className="glass-card p-6 space-y-6">
                        <div className="space-y-3">
                            <label htmlFor="bark-scan-strategy" className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <Bell size={16} className="text-violet-500" />
                                Bark 选股策略
                            </label>
                            <select
                                id="bark-scan-strategy"
                                value={settings.bark_scan_strategy || "tv_zp"}
                                onChange={(e) => setSettings({ ...settings, bark_scan_strategy: e.target.value })}
                                className="w-full bg-slate-50 border border-slate-100 text-slate-700 font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-violet-500/10 focus:bg-white transition-all"
                            >
                                {barkStrategyOptions.map(option => (
                                    <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                            </select>
                            <p className="text-[10px] text-slate-400 font-medium leading-relaxed">
                                {selectedBarkStrategy?.description || '选择 Bark 定时扫描使用的选股策略。'}保存后下一次扫描立即生效，仍需通过统一风控与可交易门槛才会推送。
                            </p>
                        </div>

                        <div className="space-y-3">
                            <label className="flex items-center gap-2 text-sm font-bold text-slate-700">
                                <ShieldCheck size={16} className="text-indigo-500" />
                                Bark 推送 Key (当前库)
                            </label>
                            <input
                                type="text"
                                value={settings.bark_key || ""}
                                onChange={(e) => setSettings({ ...settings, bark_key: e.target.value })}
                                placeholder={settings.bark_key_set ? "已配置（留空表示不修改）" : "输入您的 Bark Key"}
                                className="w-full bg-slate-50 border border-slate-100 text-slate-600 font-mono font-bold rounded-xl px-4 py-3 outline-none focus:ring-4 focus:ring-indigo-500/10 focus:bg-white transition-all"
                            />
                            <div className="flex items-center justify-between gap-3">
                                <p className="text-[10px] text-slate-400 font-medium">
                                    🔒 Key 不再回传明文{settings.bark_key_masked ? `，当前：${settings.bark_key_masked}` : ""}。留空保存 = 不修改；输入新 Key 覆盖。
                                </p>
                                {settings.bark_key_set && (
                                    <button
                                        type="button"
                                        onClick={handleClearBarkKey}
                                        className="shrink-0 text-[10px] font-bold text-rose-500 hover:text-rose-600 underline underline-offset-2"
                                    >
                                        清除已存 Key
                                    </button>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {systemHealth && (
                <div className="glass-card p-6 space-y-5">
                    <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                        <div className="flex items-center gap-3">
                            <div className={cn(
                                "w-11 h-11 rounded-2xl flex items-center justify-center",
                                systemHealth.status === 'ok' ? "bg-emerald-100 text-emerald-600" :
                                    systemHealth.status === 'warn' ? "bg-amber-100 text-amber-600" :
                                        "bg-rose-100 text-rose-600"
                            )}>
                                <Activity size={22} />
                            </div>
                            <div>
                                <h3 className="font-black text-slate-900">系统专业度自检</h3>
                                <p className="text-[10px] text-slate-400 font-bold uppercase tracking-widest">
                                    Operational Readiness Snapshot
                                </p>
                            </div>
                        </div>
                        <div className="flex items-center gap-3">
                            <div className="text-right">
                                <p className="text-[10px] text-slate-400 font-bold uppercase tracking-widest">成熟度评分</p>
                                <p className="text-3xl font-black text-slate-900">{systemHealth.score}</p>
                            </div>
                            <div className="h-12 w-2 rounded-full bg-slate-100 overflow-hidden">
                                <div
                                    className={cn(
                                        "w-full rounded-full",
                                        systemHealth.score >= 80 ? "bg-emerald-500" :
                                            systemHealth.score >= 60 ? "bg-amber-500" : "bg-rose-500"
                                    )}
                                    style={{ height: `${Math.max(6, systemHealth.score)}%`, marginTop: `${100 - Math.max(6, systemHealth.score)}%` }}
                                />
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <HealthMetric label="股票池" value={systemHealth.summary.stock_count ?? 0} />
                        <HealthMetric label="日线记录" value={systemHealth.summary.daily_k_count ?? 0} />
                        <HealthMetric label="最近扫描" value={systemHealth.summary.latest_scan_date || '--'} />
                        <HealthMetric label="策略模板" value={systemHealth.summary.strategy_template_count ?? 0} />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        {systemHealth.checks.map(check => (
                            <div key={check.name} className="flex items-start gap-3 rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
                                {check.status === 'ok'
                                    ? <CheckCircle2 size={16} className="mt-0.5 text-emerald-500 shrink-0" />
                                    : <AlertTriangle size={16} className={cn("mt-0.5 shrink-0", check.status === 'warn' ? "text-amber-500" : "text-rose-500")} />}
                                <div>
                                    <p className="text-xs font-black text-slate-700">{check.message}</p>
                                    <p className="text-[9px] font-bold uppercase tracking-wider text-slate-400">{check.name}</p>
                                </div>
                            </div>
                        ))}
                    </div>

                    {systemHealth.recommendations.length > 0 && (
                        <div className="rounded-2xl border border-indigo-100 bg-indigo-50 px-4 py-3">
                            <p className="text-xs font-black text-indigo-700 mb-2">下一步建议</p>
                            <div className="space-y-1">
                                {systemHealth.recommendations.slice(0, 4).map(item => (
                                    <p key={item} className="text-[11px] font-medium text-indigo-600">{item}</p>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
                <DiagnosticCard
                    title="运行与备份"
                    status={operationalMetrics?.status || 'unavailable'}
                    metrics={[
                        ['最新行情', operationalMetrics?.metrics.latest_daily_date || '--'],
                        ['最新扫描', operationalMetrics?.metrics.latest_scan_at || '--'],
                        ['24h带错误任务', operationalMetrics?.metrics.degraded_tasks_24h ?? '--'],
                        ['尾盘正式扫描', Number(operationalMetrics?.metrics.late_formal_scan_completed || 0) ? '已完成' : '未确认'],
                        ['通知待重试', operationalMetrics?.metrics.notification_pending ?? '--'],
                        ['通知永久失败', operationalMetrics?.metrics.notification_dead ?? '--'],
                        ['24h恢复送达', operationalMetrics?.metrics.notification_recovered_24h ?? '--'],
                        [
                            '最长恢复延迟',
                            operationalMetrics?.metrics.notification_max_recovery_seconds_24h != null
                                ? `${Math.round(Number(operationalMetrics.metrics.notification_max_recovery_seconds_24h) / 60)}分钟`
                                : '--',
                        ],
                    ]}
                    messages={[
                        ...(operationalMetrics?.alerts.map(item => item.message) || ['运行指标暂不可用']),
                        ...(notificationRetryMessage ? [notificationRetryMessage] : []),
                    ]}
                    action={Number(operationalMetrics?.metrics.notification_dead || 0) > 0 ? (
                        <button
                            type="button"
                            onClick={handleRetryDeadNotifications}
                            disabled={retryingNotifications}
                            className="w-full rounded-xl bg-rose-600 px-3 py-2 text-[11px] font-black text-white transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                            {retryingNotifications ? '正在重新排队…' : '重新尝试失败通知'}
                        </button>
                    ) : undefined}
                />
                <DiagnosticCard
                    title="点时证据覆盖"
                    status={pointInTimeCoverage?.status || 'unavailable'}
                    metrics={[
                        ['快照交易日', pointInTimeCoverage?.summary.snapshots?.dates ?? '--'],
                        ['扫描审计日', pointInTimeCoverage?.summary.scan_audits?.dates ?? '--'],
                        ['验证事件', pointInTimeCoverage?.summary.events?.verified ?? '--'],
                        ['目标交易日', pointInTimeCoverage?.target?.trading_dates ?? '--'],
                    ]}
                    messages={pointInTimeCoverage?.gaps || ['覆盖数据暂不可用']}
                />
                <DiagnosticCard
                    title="策略发布状态"
                    status={strategyStates.length ? 'ok' : 'collecting'}
                    metrics={(strategyStates.length ? strategyStates.slice(0, 4) : [{ strategy_key: '尚无已登记策略', state: 'DRAFT' }]).map(item => [item.strategy_key, item.state])}
                    messages={[strategyStates.length ? '策略晋级受成熟样本与正期望证据门禁控制' : '尚未有策略通过人工状态迁移']}
                />
            </div>

            {/* Performance Hints */}
            <div className="glass-card p-8 bg-slate-900 text-slate-300 relative overflow-hidden">
                <div className="relative z-10 flex gap-6">
                    <div className="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center shrink-0">
                        <Database size={32} className="text-indigo-400" />
                    </div>
                    <div className="space-y-2">
                        <h4 className="text-white font-bold">后台算力状态</h4>
                        <p className="text-xs font-medium text-slate-400 leading-relaxed">
                            自动化扫描将使用 <span className="text-indigo-400">本地极速模式</span> 进行。
                            单次全市场筛选耗时约为 <span className="text-white">5-10 秒</span>。
                            所有筛选结果将自动存入 `scan_history` 表供回顾。
                        </p>
                    </div>
                </div>
                <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/10 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />
            </div>
        </div>
    );
}

function HealthMetric({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="rounded-2xl border border-slate-100 bg-white px-4 py-3">
            <p className="text-[10px] text-slate-400 font-bold">{label}</p>
            <p className="text-sm font-black text-slate-800 truncate">{value}</p>
        </div>
    );
}

function DiagnosticCard({ title, status, metrics, messages, action }: {
    title: string;
    status: string;
    metrics: Array<[string, string | number]>;
    messages: string[];
    action?: React.ReactNode;
}) {
    const healthy = status === 'ok';
    const critical = status === 'critical' || status === 'error';
    return (
        <div className="glass-card p-5 space-y-4">
            <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-black text-slate-800">{title}</h3>
                <span className={cn(
                    "rounded-full px-2.5 py-1 text-[9px] font-black uppercase tracking-wider",
                    healthy ? "bg-emerald-100 text-emerald-700" : critical ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700"
                )}>{status}</span>
            </div>
            <div className="space-y-2">
                {metrics.map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between gap-3 text-[11px]">
                        <span className="text-slate-400 font-bold truncate">{label}</span>
                        <span className="text-slate-700 font-black text-right truncate max-w-[60%]">{value}</span>
                    </div>
                ))}
            </div>
            <div className={cn("rounded-xl px-3 py-2 text-[10px] font-medium leading-relaxed", critical ? "bg-rose-50 text-rose-600" : "bg-slate-50 text-slate-500")}>
                {messages.slice(0, 3).map(message => <p key={message}>{message}</p>)}
            </div>
            {action}
        </div>
    );
}
