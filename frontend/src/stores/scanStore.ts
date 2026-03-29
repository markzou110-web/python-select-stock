import { create } from 'zustand';

export interface ScanResult {
    代码: string;
    名称: string;
    行业: string;
    现价: number;
    '涨幅%': number;
    Score: number;
    RSI: number;
    DIF: number;
    BB: number;
    粘合度: number;
    历史胜率: number;
    信号次数: number;
    北向: string;
    共振: string;
    影线比: number;
    strategy_type?: string;
    warnings?: string[];
    结构?: string;
    体质?: string;
}

interface ScanStore {
    results: ScanResult[];
    isScanning: boolean;
    selectedStock: ScanResult | null;
    params: Record<string, any>;
    setResults: (results: ScanResult[]) => void;
    setIsScanning: (v: boolean) => void;
    setSelectedStock: (stock: ScanResult | null) => void;
    setParams: (params: Record<string, any>) => void;
}

export const useScanStore = create<ScanStore>((set) => ({
    results: [],
    isScanning: false,
    selectedStock: null,
    params: {},
    setResults: (results) => set({ results }),
    setIsScanning: (isScanning) => set({ isScanning }),
    setSelectedStock: (selectedStock) => set({ selectedStock }),
    setParams: (params) => set({ params }),
}));
