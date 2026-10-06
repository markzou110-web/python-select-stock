import type { ScanResult } from '@/stores/scanStore';

export type PriceActionDetails = Partial<ScanResult> & {
    pa_mtf_intraday?: { label?: string };
    price_action_signal?: string;
    pa_invalidation?: string;
    pa_volume_ratio?: number;
    pa_volume_ratio_percentile?: number;
};
