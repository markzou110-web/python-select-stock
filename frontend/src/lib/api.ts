import axios, { InternalAxiosRequestConfig } from 'axios';

// Extend Axios config to include our custom metadata
declare module 'axios' {
    interface InternalAxiosRequestConfig {
        metadata?: { startTime: number };
    }
}

const api = axios.create({
    baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000',
    // 全市场扫描等长任务需要较长时间，可用 NEXT_PUBLIC_API_TIMEOUT_MS 覆盖
    timeout: Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS) || 600000,
});

const isDev = process.env.NODE_ENV === 'development';

const SESSION_TOKEN_KEY = 'alphavision_api_token';

export function setSessionApiToken(token: string) {
    if (typeof window === 'undefined') return;
    if (token.trim()) sessionStorage.setItem(SESSION_TOKEN_KEY, token.trim());
    else sessionStorage.removeItem(SESSION_TOKEN_KEY);
}

export function hasSessionApiToken(): boolean {
    return typeof window !== 'undefined' && Boolean(sessionStorage.getItem(SESSION_TOKEN_KEY));
}

// 请求拦截器 - 添加开始时间
api.interceptors.request.use((config) => {
    config.metadata = { startTime: Date.now() };
    if (typeof window !== 'undefined') {
        const token = sessionStorage.getItem(SESSION_TOKEN_KEY);
        if (token) config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

// 响应拦截器 - 记录耗时
api.interceptors.response.use(
    (response) => {
        if (isDev) {
            const duration = Date.now() - (response.config.metadata?.startTime || 0);
            console.log(`API ${response.config.url?.split('?')[0]} completed in ${duration}ms`);
        }
        return response;
    },
    (error) => {
        if (error.config) {
            const duration = Date.now() - (error.config.metadata?.startTime || 0);
            const url = error.config.url?.split('?')[0] || '';
            const isPollingNetworkError = (
                error.message === 'Network Error' &&
                (url.startsWith('/api/scan/status/') || url === '/api/sync/status')
            );
            if (isDev || !isPollingNetworkError) {
                const log = isPollingNetworkError ? console.warn : console.error;
                log(`API ${url} failed after ${duration}ms:`, error.message);
            }
        }
        if (typeof window !== 'undefined' && error.response?.status === 401) {
            window.dispatchEvent(new CustomEvent('alphavision:auth-required'));
        }
        return Promise.reject(error);
    }
);

// /api/scan 查询参数（与 scanStore 的 ScanParams 对齐；strategy_types 为逗号分隔字符串，全部可选由后端给默认值）
export interface ScanMarketParams {
    strategy_type?: string;
    strategy_types?: string;
    match_mode?: 'any' | 'all';
    pine_min_signals?: number;
    min_data_days?: number;
    threshold?: number;
    vol_multiplier?: number;
    rsi_min?: number;
    use_macd_filter?: boolean;
    use_bb_sqz?: boolean;
    sqz_lookback?: number;
    use_weekly?: boolean;
    weekly_ma_period?: number;
    market_range?: string;
    turnover_min?: number;
    mkt_cap_min?: number;
    use_rs_filter?: boolean;
    local_only?: boolean;
    data_date?: string;
    stop_loss_pct?: number;
}

// /api/ws/scan-progress 推送的最小消息结构（后端字段）
export interface ScanProgressMessage {
    type?: 'scan_start' | 'scan_progress' | 'scan_end' | string;
    message?: string;
    current?: number;
    total?: number;
}

export const marketApi = {
    checkHealth: () => api.get('/api/health'),
    getIndices: () => api.get('/api/market/indices'),
    getSectors: () => api.get('/api/market/sectors'),
    scanMarket: (params: ScanMarketParams) => api.get('/api/scan', { params }),
};

export function connectScanWebSocket(onMessage: (msg: ScanProgressMessage) => void): WebSocket {
    const wsUrl = (process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000')
                    .replace(/^http/, 'ws') + '/api/ws/scan-progress';
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            onMessage(data);
        } catch (e) {
            console.error("WS Parse Error:", e);
        }
    };
    return ws;
}

export default api;
