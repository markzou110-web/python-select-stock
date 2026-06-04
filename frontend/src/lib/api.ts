import axios, { InternalAxiosRequestConfig } from 'axios';

// Extend Axios config to include our custom metadata
declare module 'axios' {
    interface InternalAxiosRequestConfig {
        metadata?: { startTime: number };
    }
}

const api = axios.create({
    baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000',
    timeout: 600000,  // 增加到10分钟
});

// 请求拦截器 - 添加开始时间
api.interceptors.request.use((config) => {
    config.metadata = { startTime: Date.now() };
    return config;
});

// 响应拦截器 - 记录耗时
api.interceptors.response.use(
    (response) => {
        const duration = Date.now() - (response.config.metadata?.startTime || 0);
        console.log(`API ${response.config.url?.split('?')[0]} completed in ${duration}ms`);
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
            const log = isPollingNetworkError ? console.warn : console.error;
            log(`API ${url} failed after ${duration}ms:`, error.message);
        }
        return Promise.reject(error);
    }
);

console.log("API Base URL:", api.defaults.baseURL);

export const marketApi = {
    checkHealth: () => api.get('/api/health'),
    getIndices: () => api.get('/api/market/indices'),
    getSectors: () => api.get('/api/market/sectors'),
    scanMarket: (params: any) => api.get('/api/scan', { params }),
};

export function connectScanWebSocket(onMessage: (msg: any) => void): WebSocket {
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
