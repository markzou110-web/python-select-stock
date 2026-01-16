import axios from 'axios';

const api = axios.create({
    baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000',
    timeout: 300000,
});

console.log("API Base URL:", api.defaults.baseURL);

export const marketApi = {
    checkHealth: () => api.get('/api/health'),
    getIndices: () => api.get('/api/market/indices'),
    getSectors: () => api.get('/api/market/sectors'),
    scanMarket: (params: any) => api.post('/api/scan', params),
};

export default api;
