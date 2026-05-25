"use client";

import React, { useState } from 'react';
import { X, Check, Zap, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useMarketStore } from '@/stores/marketStore';

interface FilterModalProps {
    isOpen: boolean;
    onClose: () => void;
    params: any;
    setParams: (params: any) => void;
    onScan: () => void;
    availableDates?: Array<{ date: string; stock_count: number }>;
}

export default function FilterModal({ isOpen, onClose, params, setParams, onScan, availableDates = [] }: FilterModalProps) {
    const showSqueezeParams = params.strategy_type === "squeeze" || params.strategy_type === "both";
    const showPineParams = params.strategy_type === "pine" || params.strategy_type === "both";
    const showConsensusParams = params.strategy_type === "consensus";
    const [recommending, setRecommending] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [highlightedFields, setHighlightedFields] = useState<string[]>([]);
    const fetchMarketRegime = useMarketStore(s => s.fetchMarketRegime);

    const showToast = (message: string, type: 'success' | 'error' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 4500);
    };

    const handleSmartRecommend = async () => {
        setRecommending(true);
        const data = await fetchMarketRegime(params.strategy_type);
        if (data && data.recommended_params) {
            const recommendedParams = data.recommended_params;
            
            // Find which keys are actually changed
            const changes: string[] = [];
            const changedKeys: string[] = [];
            const newParams = { ...params, ...recommendedParams };
            
            // Map parameter keys to friendly Chinese names
            const paramNames: Record<string, string> = {
                threshold: '粘合度阈值',
                vol_multiplier: '量比倍数',
                rsi_min: 'RSI最小强度',
                use_bb_sqz: '极致波动率(BB)',
                sqz_lookback: '粘合回溯天数',
                stop_loss_pct: '回测止损线',
                pine_min_signals: '最小共振信号数'
            };
            
            Object.keys(recommendedParams).forEach(key => {
                if (key !== 'description' && params[key] !== recommendedParams[key]) {
                    const name = paramNames[key] || key;
                    const oldVal = params[key];
                    const newVal = recommendedParams[key];
                    changedKeys.push(key);
                    if (oldVal !== undefined) {
                        changes.push(`${name}(${oldVal} ➡️ ${newVal})`);
                    } else {
                        changes.push(`${name}(${newVal})`);
                    }
                }
            });

            setParams(newParams);
            
            if (changedKeys.length > 0) {
                setHighlightedFields(changedKeys);
                setTimeout(() => setHighlightedFields([]), 3000);
            }
            
            const regime = data.regime;
            const desc = recommendedParams.description || regime.description;
            
            let msg = `检测到当前大盘为【${regime.label}】。${desc}`;
            if (changes.length > 0) {
                msg += `，已自适应调整参数：${changes.join('、')}`;
            } else {
                msg += `。（当前参数已处于该市场环境下的推荐配置，无需调整）`;
            }
            
            showToast(msg, 'success');
        } else {
            showToast("智能推荐参数获取失败，请重试", 'error');
        }
        setRecommending(false);
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4 text-slate-800">
            <div className="bg-white rounded-3xl w-full max-w-4xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
                <div className="flex items-center justify-between px-8 py-6 border-b border-slate-100 bg-slate-50/50">
                    <div>
                        <h3 className="text-xl font-bold text-slate-800">🔬 高级策略筛选</h3>
                        <p className="text-xs text-slate-400 font-medium mt-0.5 uppercase tracking-wider">TradingView Pro Logic Configuration</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <button 
                            onClick={handleSmartRecommend} 
                            disabled={recommending}
                            className="flex items-center gap-2 px-4 py-2 bg-indigo-50 text-indigo-600 rounded-xl text-sm font-bold hover:bg-indigo-100 transition-colors disabled:opacity-50"
                        >
                            {recommending ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
                            智能推荐
                        </button>
                        <button onClick={onClose} className="p-2 hover:bg-slate-200 rounded-xl transition-colors">
                            <X size={20} className="text-slate-500" />
                        </button>
                    </div>
                </div>

                {/* 策略选择器 */}
                <div className="px-8 py-4 bg-gradient-to-r from-indigo-50 to-purple-50 border-b border-indigo-100">
                    <FilterItem label="🎯 选择选股策略">
                        <div className="grid grid-cols-4 gap-2">
                            <StrategyOption
                                title="均线粘合策略"
                                description="传统均线粘合突破战法"
                                active={params.strategy_type === "squeeze"}
                                onClick={() => setParams({ ...params, strategy_type: "squeeze" })}
                                icon="📊"
                            />
                            <StrategyOption
                                title="Pine Script 多指标"
                                description="Range Filter/QQE 等共振"
                                active={params.strategy_type === "pine"}
                                onClick={() => setParams({ ...params, strategy_type: "pine" })}
                                icon="🚀"
                            />
                            <StrategyOption
                                title="双重强力共振"
                                description="均线粘合 + Pine 信号"
                                active={params.strategy_type === "both"}
                                onClick={() => setParams({ ...params, strategy_type: "both" })}
                                icon="🔥"
                            />
                            <StrategyOption
                                title="Azul 共识策略"
                                description="放量突破+高低点结构"
                                active={params.strategy_type === "consensus"}
                                onClick={() => setParams({ ...params, strategy_type: "consensus" })}
                                icon="💎"
                            />
                        </div>
                    </FilterItem>
                </div>

                <div className="p-8 grid grid-cols-2 gap-8">
                    {/* Column 1 */}
                    <div className="space-y-6">
                        {/* 均线粘合策略专用参数 */}
                        {showSqueezeParams && (
                            <>
                                <FilterItem label="粘合度阈值 (0.01~0.30)" highlighted={highlightedFields.includes('threshold')}>
                                    <input
                                        type="range" min="0.01" max="0.30" step="0.01" value={params.threshold || 0}
                                        onChange={e => setParams({ ...params, threshold: parseFloat(e.target.value) })}
                                        className="w-full accent-indigo-600"
                                    />
                                    <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                        <span>极限粘合 (0.01)</span>
                                        <span className="text-indigo-600 font-extrabold text-xs">{(Number(params.threshold) || 0).toFixed(2)}</span>
                                        <span>宽容粘合 (0.30)</span>
                                    </div>
                                </FilterItem>

                                <FilterItem label="量比倍数 (1.0~5.0)" highlighted={highlightedFields.includes('vol_multiplier')}>
                                    <input
                                        type="number" step="0.1" value={isNaN(params.vol_multiplier) ? '' : params.vol_multiplier}
                                        onChange={e => setParams({ ...params, vol_multiplier: e.target.value === '' ? NaN : parseFloat(e.target.value) })}
                                        className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                    />
                                </FilterItem>

                                <div className="space-y-3 pt-2">
                                    <ToggleItem
                                        label="启用周线趋势过滤"
                                        active={params.use_weekly}
                                        onClick={() => setParams({ ...params, use_weekly: !params.use_weekly })}
                                    />
                                    <ToggleItem
                                        label="🔥 MACD 必须处于金叉 (红柱)"
                                        active={params.use_macd_filter}
                                        onClick={() => setParams({ ...params, use_macd_filter: !params.use_macd_filter })}
                                    />
                                </div>

                                <FilterItem label="📅 最小数据天数">
                                    <div className="flex items-center gap-4">
                                        <input
                                            type="range" min="60" max="250" step="10" value={params.min_data_days || 120}
                                            onChange={e => setParams({ ...params, min_data_days: parseInt(e.target.value) })}
                                            className="flex-1 accent-indigo-600"
                                        />
                                        <div className="w-16 h-12 rounded-xl bg-indigo-100 flex items-center justify-center">
                                            <span className="text-sm font-bold text-indigo-600">{params.min_data_days || 120}天</span>
                                        </div>
                                    </div>
                                    <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                        <span>60天</span>
                                        <span>250天</span>
                                    </div>
                                    <p className="text-[10px] text-indigo-500 mt-2">
                                        股票至少需要有多少天的历史数据才会被扫描
                                    </p>
                                </FilterItem>
                            </>
                        )}

                        {/* Pine Script 策略专用参数 */}
                        {showPineParams && (
                            <>
                                <FilterItem label="🎯 最小共振信号数" highlighted={highlightedFields.includes('pine_min_signals')}>
                                    <div className="flex items-center gap-4">
                                        <input
                                            type="range" min="1" max="5" step="1" value={params.pine_min_signals || 3}
                                            onChange={e => setParams({ ...params, pine_min_signals: parseInt(e.target.value) })}
                                            className="flex-1 accent-purple-600"
                                        />
                                        <div className="w-16 h-12 rounded-xl bg-purple-100 flex items-center justify-center">
                                            <span className="text-xl font-bold text-purple-600">{params.pine_min_signals || 3}</span>
                                        </div>
                                    </div>
                                    <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                        <span>宽松 (1)</span>
                                        <span>严格 (5)</span>
                                    </div>
                                    <p className="text-[10px] text-purple-500 mt-2">
                                        至少需要多少个指标(Range Filter/SuperTrend/RQK/Half Trend/QQE)同时看涨才触发信号
                                    </p>
                                </FilterItem>

                                <FilterItem label="📅 最小数据天数">
                                    <div className="flex items-center gap-4">
                                        <input
                                            type="range" min="30" max="250" step="10" value={params.min_data_days || 60}
                                            onChange={e => setParams({ ...params, min_data_days: parseInt(e.target.value) })}
                                            className="flex-1 accent-purple-600"
                                        />
                                        <div className="w-16 h-12 rounded-xl bg-purple-100 flex items-center justify-center">
                                            <span className="text-sm font-bold text-purple-600">{params.min_data_days || 60}天</span>
                                        </div>
                                    </div>
                                    <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                        <span>30天</span>
                                        <span>250天</span>
                                    </div>
                                    <p className="text-[10px] text-purple-500 mt-2">
                                        股票至少需要有多少天的历史数据才会被扫描，天数越多被排除的股票越多
                                    </p>
                                </FilterItem>

                                <div className="p-4 rounded-xl bg-purple-50 border border-purple-100 space-y-2">
                                    <h4 className="text-xs font-bold text-purple-700">📊 Pine Script 策略说明</h4>
                                    <ul className="text-[10px] text-purple-600 space-y-1">
                                        <li>• <strong>Range Filter</strong>: 基于 ATR 的范围过滤器</li>
                                        <li>• <strong>SuperTrend</strong>: 超级趋势指标</li>
                                        <li>• <strong>RQK</strong>: 核回归趋势分析</li>
                                        <li>• <strong>Half Trend</strong>: 半趋势确认</li>
                                        <li>• <strong>QQE Mod</strong>: 量化指标带</li>
                                    </ul>
                                </div>
                            </>
                        )}

                        {showConsensusParams && (
                            <>
                                <div className="p-4 rounded-xl bg-blue-50 border border-blue-100 space-y-2">
                                    <h4 className="text-xs font-bold text-blue-700">💎 Azul 共识策略说明</h4>
                                    <ul className="text-[10px] text-blue-600 space-y-1">
                                        <li>• <strong>场景博弈</strong>: 确认行业/题材处于景气期</li>
                                        <li>• <strong>图表验证</strong>: 均线多头 + HH突破</li>
                                        <li>• <strong>沉积体质</strong>: 过去5天放量上涨多于下跌</li>
                                        <li>• <strong>大阳突破</strong>: 实体 &gt; 2.5% + 成交量 &gt; 1.8倍</li>
                                    </ul>
                                </div>
                                
                                <FilterItem label="量比倍数 (建议 &gt; 1.8)" highlighted={highlightedFields.includes('vol_multiplier')}>
                                    <input
                                        type="number" step="0.5" value={isNaN(params.vol_multiplier) ? '' : params.vol_multiplier}
                                        onChange={e => setParams({ ...params, vol_multiplier: e.target.value === '' ? NaN : parseFloat(e.target.value) })}
                                        className="w-full px-4 py-2 bg-blue-50/50 border-none rounded-xl text-sm font-bold text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
                                    />
                                </FilterItem>

                                <ToggleItem
                                    label="启用周线大均线过滤"
                                    active={params.use_weekly}
                                    onClick={() => setParams({ ...params, use_weekly: !params.use_weekly })}
                                />

                                {params.use_weekly && (
                                    <FilterItem label="📈 周线均线周期">
                                        <select
                                            value={params.weekly_ma_period}
                                            onChange={e => setParams({ ...params, weekly_ma_period: parseInt(e.target.value) })}
                                            className="w-full px-4 py-2 bg-blue-50/50 border-none rounded-xl text-sm font-bold text-slate-900 outline-none focus:ring-2 focus:ring-blue-500"
                                        >
                                            <option value={10}>MA10w (10周 ≈ 2.5个月) — 灵敏</option>
                                            <option value={20}>MA20w (20周 ≈ 5个月) — 推荐</option>
                                            <option value={30}>MA30w (30周 ≈ 7个月) — 平衡</option>
                                            <option value={60}>MA60w (60周 ≈ 15个月) — 严格</option>
                                        </select>
                                        <p className="text-[10px] text-blue-500 mt-1">
                                            周期越长，要求长期趋势越强，但会过滤掉更多处于反转初期的股票
                                        </p>
                                    </FilterItem>
                                )}
                            </>
                        )}

                        <FilterItem label="最小换手率 (%)">
                            <input
                                type="number" step="0.5" value={isNaN(params.turnover_min) ? '' : params.turnover_min}
                                onChange={e => setParams({ ...params, turnover_min: e.target.value === '' ? NaN : parseFloat(e.target.value) })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            />
                        </FilterItem>

                        <FilterItem label="最小市值 (亿)">
                            <input
                                type="number" step="10" value={isNaN(params.mkt_cap_min) ? '' : params.mkt_cap_min}
                                onChange={e => setParams({ ...params, mkt_cap_min: e.target.value === '' ? NaN : parseFloat(e.target.value) })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            />
                        </FilterItem>
                    </div>

                    {/* Column 2 */}
                    <div className="space-y-6">
                        {/* 均线粘合策略专用参数 */}
                        {showSqueezeParams && (
                            <>
                                <FilterItem label="RSI 最小强度 (30~80)" highlighted={highlightedFields.includes('rsi_min')}>
                                    <input
                                        type="number" value={isNaN(params.rsi_min) ? '' : params.rsi_min}
                                        onChange={e => setParams({ ...params, rsi_min: e.target.value === '' ? NaN : parseInt(e.target.value) })}
                                        className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                    />
                                </FilterItem>

                                <FilterItem label="粘合回溯天数 (1~30)" highlighted={highlightedFields.includes('sqz_lookback')}>
                                    <input
                                        type="number" value={isNaN(params.sqz_lookback) ? '' : params.sqz_lookback}
                                        onChange={e => setParams({ ...params, sqz_lookback: e.target.value === '' ? NaN : parseInt(e.target.value) })}
                                        className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                                    />
                                </FilterItem>

                                <ToggleItem
                                    label="极致波动率收缩 (BB)"
                                    active={params.use_bb_sqz}
                                    onClick={() => setParams({ ...params, use_bb_sqz: !params.use_bb_sqz })}
                                    highlighted={highlightedFields.includes('use_bb_sqz')}
                                />

                                <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-slate-100 cursor-pointer transition-all hover:bg-slate-50">
                                    <span className="flex-1 font-semibold text-slate-600">相对强度过滤 (RS)</span>
                                    <input
                                        type="checkbox" checked={params.use_rs_filter}
                                        onChange={e => setParams({ ...params, use_rs_filter: e.target.checked })}
                                        className="w-5 h-5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                                    />
                                </label>
                            </>
                        )}

                        {/* Pine Script 策略专用参数 - 添加一些通用过滤选项 */}
                        {showPineParams && (
                            <>
                                <div className="p-4 rounded-xl bg-gradient-to-br from-purple-50 to-indigo-50 border border-purple-100">
                                    <h4 className="text-xs font-bold text-purple-700 mb-2">💡 使用建议</h4>
                                    <ul className="text-[10px] text-purple-600 space-y-1">
                                        <li>• 信号数设为 <strong>3</strong>：平衡信号数量和质量</li>
                                        <li>• 信号数设为 <strong>4-5</strong>：更严格，信号较少但准确度高</li>
                                        <li>• 信号数设为 <strong>1-2</strong>：较宽松，捕捉更多机会</li>
                                    </ul>
                                </div>

                                <div className="p-4 rounded-xl bg-slate-50 border border-slate-200">
                                    <h4 className="text-xs font-bold text-slate-700 mb-2">📈 信号说明</h4>
                                    <p className="text-[10px] text-slate-500 leading-relaxed">
                                        Pine Script 策略通过 5 个独立技术指标的共振来识别趋势启动点。
                                        当多个指标同时看涨时，产生买入信号。信号数越多，趋势确认度越高。
                                    </p>
                                </div>
                            </>
                        )}

                        <FilterItem label="市场范围限制">
                            <select
                                value={params.market_range}
                                onChange={e => setParams({ ...params, market_range: e.target.value })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            >
                                <option value="全市场(除科创)">全市场(除科创)</option>
                                <option value="包含科创板">全市场(包含科创)</option>
                                <option value="沪深300">沪深300</option>
                                <option value="上证50">上证50</option>
                                <option value="中证500">中证500</option>
                                <option value="中证1000">中证1000</option>
                            </select>
                        </FilterItem>

                        {/* 数据日期选择器 */}
                        <FilterItem label="📅 选股数据日期">
                            <select
                                value={params.data_date || ""}
                                onChange={e => setParams({ ...params, data_date: e.target.value })}
                                className="w-full px-4 py-2 bg-slate-100 border-none rounded-xl text-sm font-bold text-slate-900 outline-none ring-offset-2 focus:ring-2 focus:ring-indigo-500"
                            >
                                <option value="">🔄 自动选择最新日期</option>
                                {availableDates.slice(0, 15).map((d) => (
                                    <option key={d.date} value={d.date}>
                                        📅 {d.date} ({d.stock_count}只股票)
                                    </option>
                                ))}
                            </select>
                            <p className="text-[10px] text-slate-400 mt-1">
                                选择使用哪一天的数据进行选股，留空则自动使用最新可用日期
                            </p>
                        </FilterItem>

                        <FilterItem label="🛑 回测止损线 (%)" highlighted={highlightedFields.includes('stop_loss_pct')}>
                            <div className="flex items-center gap-4">
                                <input
                                    type="range" min="-15" max="-3" step="1" value={params.stop_loss_pct || -8}
                                    onChange={e => setParams({ ...params, stop_loss_pct: parseInt(e.target.value) })}
                                    className="flex-1 accent-rose-600"
                                />
                                <div className="w-16 h-12 rounded-xl bg-rose-100 flex items-center justify-center">
                                    <span className="text-sm font-bold text-rose-600">{params.stop_loss_pct || -8}%</span>
                                </div>
                            </div>
                            <div className="flex justify-between text-[10px] font-bold text-slate-400 mt-1">
                                <span>宽松 (-3%)</span>
                                <span>严格 (-15%)</span>
                            </div>
                            <p className="text-[10px] text-rose-500 mt-1">
                                回测中模拟止损退出的触发点，影响历史胜率 and 盈亏比计算
                            </p>
                        </FilterItem>

                        <div className="pt-4 border-t border-slate-100">
                            <label className="flex items-center gap-3 px-4 py-3 rounded-xl border-2 border-indigo-100 bg-indigo-50/20 cursor-pointer transition-all hover:bg-indigo-50/40">
                                <div className="flex-1">
                                    <span className="block font-bold text-indigo-700">🚀 本地极速扫描模式</span>
                                    <span className="text-[10px] text-indigo-400 font-medium">仅使用本地数据库，无需网络，秒级出结果</span>
                                </div>
                                <input
                                    type="checkbox"
                                    checked={params.local_only}
                                    onChange={e => setParams({ ...params, local_only: e.target.checked })}
                                    className="w-5 h-5 rounded border-indigo-300 text-indigo-600 focus:ring-indigo-500"
                                />
                            </label>
                        </div>
                    </div>
                </div>

                <div className="px-8 py-6 bg-slate-50 flex justify-end gap-3">
                    <button onClick={onClose} className="px-6 py-2.5 rounded-xl font-bold text-slate-500 hover:bg-slate-200 transition-all">取消</button>
                    <button
                        onClick={() => { onScan(); onClose(); }}
                        className={cn(
                            "px-8 py-2.5 text-white rounded-xl font-bold shadow-lg transition-all hover:scale-105 active:scale-95",
                            params.strategy_type === "pine" ? "bg-gradient-to-r from-purple-600 to-indigo-600 shadow-purple-100" : 
                            params.strategy_type === "both" ? "bg-gradient-to-r from-indigo-600 to-emerald-600 shadow-emerald-100" :
                            params.strategy_type === "consensus" ? "bg-gradient-to-r from-blue-600 to-cyan-600 shadow-blue-100" :
                            "premium-gradient"
                        )}
                    >
                        保存并执行
                    </button>
                </div>
            </div>
            
            {/* Toast notification */}
            {toast && (
                <div className={cn(
                    "fixed top-6 right-6 z-[60] max-w-md px-5 py-4 rounded-2xl shadow-2xl text-sm font-bold animate-in fade-in slide-in-from-top-4 duration-300 bg-indigo-600 text-white"
                )}>
                    <div className="flex items-start gap-3">
                        <div className="mt-0.5 p-1 bg-white/10 rounded-lg">
                            <Zap size={16} className="text-amber-300 fill-amber-300 animate-bounce" />
                        </div>
                        <div className="space-y-1">
                            <p className="font-extrabold tracking-wide">💡 智能推荐反馈</p>
                            <p className="text-xs text-white/95 font-medium leading-relaxed">{toast.message}</p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function FilterItem({ label, children, highlighted = false }: { label: string, children: React.ReactNode, highlighted?: boolean }) {
    return (
        <div className={cn(
            "space-y-2 p-2 rounded-2xl transition-all duration-500",
            highlighted ? "bg-amber-50 ring-2 ring-amber-400/50 shadow-md shadow-amber-100 scale-[1.02]" : "border border-transparent"
        )}>
            <label className="text-xs font-extrabold text-slate-500 uppercase tracking-wider flex items-center gap-1">
                {label}
                {highlighted && <span className="text-[10px] bg-amber-500 text-white px-1.5 py-0.5 rounded-full font-black animate-pulse">推荐更新</span>}
            </label>
            {children}
        </div>
    );
}

function ToggleItem({ label, active, onClick, highlighted = false }: { label: string, active: boolean, onClick: () => void, highlighted?: boolean }) {
    return (
        <div
            onClick={onClick}
            className={cn(
                "flex items-center justify-between p-3 rounded-xl border-2 cursor-pointer transition-all duration-500",
                highlighted ? "border-amber-400 bg-amber-50/50 scale-[1.02] ring-2 ring-amber-400/30" : 
                active ? "border-indigo-600 bg-indigo-50/50" : "border-slate-100 bg-slate-50/30 hover:border-slate-200"
            )}
        >
            <span className={cn("text-xs font-bold flex items-center gap-1", highlighted ? "text-amber-700" : active ? "text-indigo-600" : "text-slate-400")}>
                {label}
                {highlighted && <span className="text-[9px] bg-amber-500 text-white px-1 py-0.5 rounded-full font-black">更新</span>}
            </span>
            <div className={cn(
                "w-5 h-5 rounded-md flex items-center justify-center transition-all",
                highlighted ? "bg-amber-500 text-white" : active ? "bg-indigo-600 text-white" : "bg-slate-200 text-transparent"
            )}>
                <Check size={14} strokeWidth={3} />
            </div>
        </div>
    );
}

function StrategyOption({
    title,
    description,
    active,
    onClick,
    icon
}: {
    title: string;
    description: string;
    active: boolean;
    onClick: () => void;
    icon: string;
}) {
    return (
        <div
            onClick={onClick}
            className={cn(
                "relative p-4 rounded-xl border-2 cursor-pointer transition-all",
                active
                    ? "border-indigo-500 bg-indigo-50/50 ring-2 ring-indigo-200"
                    : "border-slate-200 bg-white hover:border-indigo-200 hover:bg-indigo-50/20"
            )}
        >
            <div className="flex items-start gap-3">
                <span className="text-2xl">{icon}</span>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                        <h4 className={cn("font-bold text-sm", active ? "text-indigo-700" : "text-slate-700")}>
                            {title}
                        </h4>
                        {active && (
                            <div className="w-5 h-5 rounded-full bg-indigo-500 flex items-center justify-center">
                                <Check size={12} className="text-white" strokeWidth={3} />
                            </div>
                        )}
                    </div>
                    <p className="text-[10px] text-slate-500 mt-1 leading-relaxed">{description}</p>
                </div>
            </div>
        </div>
    );
}
