import { afterEach, expect, it, vi } from 'vitest';
import { createApi } from './api.js';

afterEach(() => vi.unstubAllGlobals());

it('keeps the local session token in memory and sends it only on JSON mutations', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ token: 'session' }) })
    .mockResolvedValueOnce({ ok: true, json: async () => [] })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ requested: true }) });
  vi.stubGlobal('fetch', fetch);
  const api = createApi();
  await api.start(); await api.get('/api/preferences');
  await api.post('/api/approval', { proposal_id: 'exact', explicitly_approve: true });
  expect(fetch.mock.calls[1]).toEqual(['/api/preferences', {}]);
  expect(fetch.mock.calls[2]).toEqual(['/api/approval', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TuneTrail-Token': 'session' },
    body: JSON.stringify({ proposal_id: 'exact', explicitly_approve: true }),
  }]);
});
