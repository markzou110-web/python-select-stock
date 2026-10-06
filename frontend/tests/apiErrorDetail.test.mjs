import assert from 'node:assert/strict';
import test from 'node:test';
import { AxiosError } from 'axios';
import { getApiErrorDetail } from '../src/lib/api.ts';

test('preserves API detail strings and rejects non-string error payloads', () => {
    const error = new AxiosError('Request failed');
    error.response = { data: { detail: '扫描服务暂不可用' } };
    assert.equal(getApiErrorDetail(error), '扫描服务暂不可用');

    error.response.data.detail = [{ msg: 'Invalid input' }];
    assert.equal(getApiErrorDetail(error), undefined);
    assert.equal(getApiErrorDetail(new Error('offline')), undefined);
    assert.equal(getApiErrorDetail(null), undefined);
});
