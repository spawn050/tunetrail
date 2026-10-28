import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App.jsx';

const item = { title: 'Example', artists: ['Independent Artist'], recording_id: '00000000-0000-0000-0000-000000000001' };

function mockApi() {
  let profile = [];
  return {
    start: vi.fn(async () => ({ token: 'local-token' })),
    get: vi.fn(async (path) => {
      if (path === '/api/preferences') return profile;
      if (path === '/api/devices') return [{ id: 'device', name: 'Desk', is_active: true }];
      if (path.startsWith('/api/catalog-search')) return [{ title: 'Spotify result', spotify_uri: 'spotify:track:a' }];
      throw new Error('Unexpected GET');
    }),
    post: vi.fn(async (path, body) => {
      if (path === '/api/chat') { profile = [{ subject: 'artist', name: 'Independent Artist', artist: null, musicbrainz_id: null, strength: 1, source: 'chat' }]; return { intent: { queue_size: 1 }, preferences: profile }; }
      if (path === '/api/discovery') return { discovery_id: 'discovery', result: { candidates: [item], source_failures: 0, needs_more_seeds: true } };
      if (path === '/api/preview') return { proposal_id: 'proposal', matches: [{ title: 'Example', artists: ['Independent Artist'], uri: 'spotify:track:a', evidence: 'Exact match', ranked: { score: 1, signals: [['preference', 1]] } }], issues: [] };
      if (path === '/api/feedback') { if (body.response === 'like') profile = [{ subject: 'recording', name: item.title, artist: item.artists[0], musicbrainz_id: item.recording_id, strength: 1, source: body.source }]; return {}; }
      if (path === '/api/preferences/reset') { profile = []; return {}; }
      if (['/api/cancel', '/api/approval', '/api/preferences/remove'].includes(path)) return {};
      throw new Error('Unexpected POST');
    }),
  };
}

async function discover(user) {
  await user.type(screen.getByLabelText('Independent MusicBrainz UUID'), item.recording_id);
  await user.click(screen.getByLabelText('I obtained this ID independently of Spotify.'));
  await user.click(screen.getByRole('button', { name: 'Discover' }));
  await screen.findByText('Suggested music (MusicBrainz)');
}

describe('local interface', () => {
  it('moves from chat and independent discovery to explicit single-use approval', async () => {
    const user = userEvent.setup(); const api = mockApi(); render(<App api={api} />);
    await screen.findByText('No explicit preferences yet.');
    await user.type(screen.getByLabelText('Request or lasting preference'), 'I like this artist');
    await user.click(screen.getByRole('button', { name: 'Send chat' }));
    await screen.findByText(/artist: Independent Artist/);
    await discover(user);
    expect(api.post).toHaveBeenCalledWith('/api/discovery', expect.objectContaining({ independently_obtained: true }));
    await user.click(screen.getByRole('button', { name: 'Refresh devices' }));
    await screen.findByRole('option', { name: 'Desk (active)' });
    await user.selectOptions(screen.getByLabelText('Device'), 'device');
    await user.click(screen.getByRole('button', { name: 'Create preview' }));
    await screen.findByText('Review before approval');
    expect(screen.getByText(/Exact match/)).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalledWith('/api/approval', expect.anything());
    await user.click(screen.getByRole('button', { name: 'Approve play now' }));
    await screen.findByText(/Playback request sent/);
    expect(api.post).toHaveBeenCalledWith('/api/approval', { proposal_id: 'proposal', explicitly_approve: true });
    expect(screen.queryByRole('button', { name: 'Approve play now' })).not.toBeInTheDocument();
  });

  it('cancels preview, records survey feedback, and requires reset confirmation', async () => {
    const user = userEvent.setup(); const api = mockApi(); render(<App api={api} />);
    await screen.findByText('No explicit preferences yet.'); await discover(user);
    await user.click(screen.getByRole('button', { name: 'Create preview' }));
    await screen.findByText('Review before approval');
    await user.click(screen.getByRole('button', { name: 'Cancel preview' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/cancel', { proposal_id: 'proposal' }));
    expect(api.post).not.toHaveBeenCalledWith('/api/approval', expect.anything());
    await user.click(screen.getByRole('button', { name: 'Start survey' }));
    const survey = screen.getByText('Survey 1 of 1').parentElement;
    await user.click(within(survey).getByRole('button', { name: 'not familiar' }));
    await screen.findByText('Survey complete.');
    expect(api.post).toHaveBeenCalledWith('/api/feedback', expect.objectContaining({ response: 'not_familiar', source: 'survey' }));
    expect(screen.getByText('No explicit preferences yet.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Like Example' }));
    await screen.findByText(/recording: Example/);
    expect(screen.getByRole('button', { name: 'Reset explicit profile' })).toBeDisabled();
    await user.click(screen.getByLabelText('Confirm removal of all explicit preferences only'));
    await user.click(screen.getByRole('button', { name: 'Reset explicit profile' }));
    await screen.findByText('No explicit preferences yet.');
  });

  it('does not retry a failed approval and warns about uncertain playback', async () => {
    const user = userEvent.setup(); const api = mockApi();
    const original = api.post; api.post = vi.fn((path, body) => path === '/api/approval' ? Promise.reject(new Error('Provider unavailable')) : original(path, body));
    render(<App api={api} />); await screen.findByText('No explicit preferences yet.'); await discover(user);
    await user.click(screen.getByRole('button', { name: 'Create preview' })); await screen.findByText('Review before approval');
    await user.click(screen.getByRole('button', { name: 'Approve play now' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('inspect Spotify playback');
    expect(screen.queryByRole('button', { name: 'Approve play now' })).not.toBeInTheDocument();
  });

  it('keeps Spotify search separate and invalidates previews when the action changes', async () => {
    const user = userEvent.setup(); const api = mockApi(); render(<App api={api} />);
    await screen.findByText('No explicit preferences yet.');
    await user.selectOptions(screen.getByLabelText('Search type'), 'artist');
    await user.type(screen.getByLabelText('Search Spotify'), 'example');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByText('Spotify result');
    expect(screen.getByLabelText('Independent MusicBrainz UUID')).toHaveValue('');
    expect(api.get).toHaveBeenCalledWith('/api/catalog-search?q=example&kind=artist');
    await discover(user);
    await user.click(screen.getByRole('button', { name: 'Create preview' }));
    await screen.findByText('Review before approval');
    await user.selectOptions(screen.getByLabelText('Action'), 'append_queue');
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/cancel', { proposal_id: 'proposal' }));
    expect(screen.queryByText('Review before approval')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create preview' }));
    await screen.findByText('Review before approval');
    expect(api.post).toHaveBeenCalledWith('/api/preview', expect.objectContaining({ intent: expect.objectContaining({ action: 'append_queue', queue_size: 1 }) }));
  });
});
