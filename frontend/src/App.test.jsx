import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import App from './App.jsx';

function mockApi() {
  return {
    start: vi.fn(async () => ({ token: 'local-token' })),
    get: vi.fn(async (path) => {
      if (path === '/api/now-playing') return null;
      if (path === '/api/spotify/connect') return { status: 'pending' };
      throw new Error('Unexpected GET');
    }),
    post: vi.fn(async () => ({ preferences: [{ name: 'Artist' }] })),
  };
}

async function send(user, text) {
  const input = await screen.findByLabelText('Request or lasting preference');
  await waitFor(() => expect(input).toBeEnabled());
  await user.type(input, `${text}{Enter}`);
}

describe('chat-focused interface', () => {
  it.each(['success', 'failure'])('shows a spinner until the chat request ends: %s', async (outcome) => {
    const api = mockApi(); const user = userEvent.setup();
    let finish;
    api.post = vi.fn(() => new Promise((resolve, reject) => { finish = outcome === 'success' ? () => resolve({ preferences: [{ name: 'Artist' }] }) : () => reject(new Error('Request failed')); }));
    render(<App api={api} />);
    await send(user, 'I like this artist');
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for TuneTrail');
    expect(screen.getByLabelText('Request or lasting preference')).toBeDisabled();
    await act(async () => { finish(); });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Request or lasting preference')).toBeEnabled();
    expect(screen.getByLabelText('Request or lasting preference')).toHaveFocus();
    expect(await screen.findByText(outcome === 'success' ? 'Clear lasting preferences saved to your profile.' : 'Request failed')).toBeInTheDocument();
  });
  it('shows now playing, chat, and no legacy panels', async () => {
    const api = mockApi(); const user = userEvent.setup(); const original = api.get;
    api.get = vi.fn((path) => path === '/api/now-playing' ? Promise.resolve({ title: 'A song', artists: ['An artist'], duration_ms: 200000, progress_ms: 65000, is_playing: false }) : original(path));
    render(<App api={api} />);
    expect(await screen.findByText('A song')).toBeInTheDocument();
    expect(screen.getByText('An artist')).toBeInTheDocument();
    expect(screen.getByText('1:05')).toBeInTheDocument();
    expect(screen.getByText(/Paused/)).toBeInTheDocument();
    expect(screen.queryByText('Settings and discovery')).not.toBeInTheDocument();
    await send(user, 'I like this artist');
    expect(await screen.findByText('Clear lasting preferences saved to your profile.')).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith('/api/chat', { message: 'I like this artist' });
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it('shows a chat confirmation and only opens the returned authorization link', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async (_path, body) => body.message === 'connect Spotify'
      ? { spotify_connection: { status: 'confirmation_required', message: 'Send confirm replace Spotify credentials.' } }
      : { spotify_connection: { status: 'pending', authorization_url: 'https://accounts.spotify.com/authorize?state=test' } });
    render(<App api={api} />);
    await send(user, 'connect Spotify');
    expect(await screen.findByText('Send confirm replace Spotify credentials.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open Spotify authorization' })).not.toBeInTheDocument();
    await send(user, 'confirm replace Spotify credentials');
    const link = await screen.findByRole('link', { name: 'Open Spotify authorization' });
    expect(link).toHaveAttribute('href', 'https://accounts.spotify.com/authorize?state=test');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post).toHaveBeenLastCalledWith('/api/chat', { message: 'confirm replace Spotify credentials' });
  });

  it('refuses unexpected authorization URLs', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async () => ({ spotify_connection: { status: 'pending', authorization_url: 'https://example.com/authorize' } }));
    render(<App api={api} />); await send(user, 'connect Spotify');
    expect(await screen.findByRole('alert')).toHaveTextContent('authorization link was invalid');
    expect(screen.queryByRole('link', { name: 'Open Spotify authorization' })).not.toBeInTheDocument();
  });

  it('reports completed authorization and removes its link after polling', async () => {
    const api = mockApi(); const user = userEvent.setup(); const original = api.get;
    api.post = vi.fn(async () => ({ spotify_connection: { status: 'pending', authorization_url: 'https://accounts.spotify.com/authorize?state=test' } }));
    api.get = vi.fn((path) => path === '/api/spotify/connect' ? Promise.resolve({ status: 'connected' }) : original(path));
    render(<App api={api} />); await send(user, 'connect Spotify');
    expect(await screen.findByRole('link', { name: 'Open Spotify authorization' })).toBeInTheDocument();
    expect(await screen.findByText(/Spotify authorization completed/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open Spotify authorization' })).not.toBeInTheDocument();
    await waitFor(() => expect(api.get.mock.calls.filter(([path]) => path === '/api/now-playing').length).toBeGreaterThanOrEqual(2));
  });

  it('clears the link when authorization is cancelled in chat', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn().mockResolvedValueOnce({ spotify_connection: { status: 'pending', authorization_url: 'https://accounts.spotify.com/authorize?state=test' } })
      .mockResolvedValueOnce({ spotify_connection: { status: 'cancelled', cancelled: true } });
    render(<App api={api} />); await send(user, 'connect Spotify');
    expect(await screen.findByRole('link', { name: 'Open Spotify authorization' })).toBeInTheDocument();
    await send(user, 'cancel Spotify connection');
    expect(await screen.findByText('Spotify authorization cancelled.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open Spotify authorization' })).not.toBeInTheDocument();
  });

  it('renders recent plays, timestamps, coverage warnings and empty results', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn().mockResolvedValueOnce({ recent_plays: [{ title: 'Recent song', artists: ['Recent artist'], played_at: '2026-10-29T12:00:00Z' }], notice: 'Results may not cover the entire period.' })
      .mockResolvedValueOnce({ recent_plays: [], notice: 'Results may not cover the entire period.' });
    render(<App api={api} />); await send(user, 'recent plays');
    expect(await screen.findByText('Recent song')).toBeInTheDocument();
    expect(screen.getByText(/Recent artist/)).toBeInTheDocument();
    expect(screen.getByText(/Recent artist/).querySelector('time')).toHaveAttribute('datetime', '2026-10-29T12:00:00Z');
    expect(screen.getByText('Results may not cover the entire period.')).toBeInTheDocument();
    await send(user, 'recent plays again');
    expect(await screen.findByText('No plays found in the available results.')).toBeInTheDocument();
    expect(screen.getAllByText('Results may not cover the entire period.')).toHaveLength(2);
  });

  it('renders device results and transfer confirmation in chat', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn().mockResolvedValueOnce({ devices: [{ name: 'Desk', kind: 'Computer', is_active: true, selectable: true }] })
      .mockResolvedValueOnce({ device_message: 'Transfer Spotify playback to Desk? Confirm or cancel.' });
    render(<App api={api} />);
    await send(user, 'show my devices');
    expect(await screen.findByText('Desk')).toBeInTheDocument();
    expect(screen.getByText(/Computer · Active/)).toBeInTheDocument();
    await send(user, 'make Desk active');
    expect(await screen.findByText(/Confirm or cancel/)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(2);
  });

  it('shows errors and does not retry a failed player request', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async () => { throw new Error('Check Spotify before retrying; the request may have succeeded.'); });
    render(<App api={api} />); await send(user, 'next');
    expect(await screen.findByRole('alert')).toHaveTextContent('may have succeeded');
    expect(api.post).toHaveBeenCalledTimes(1);
  });

  it('sends on Enter, allows Shift+Enter, and refreshes after a player command', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async () => ({ control: 'pause', requested: true }));
    render(<App api={api} />);
    const input = await screen.findByLabelText('Request or lasting preference');
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, 'first{Shift>}{Enter}{/Shift}second');
    expect(input).toHaveValue('first\nsecond');
    expect(api.post).not.toHaveBeenCalled();
    await user.clear(input); await user.type(input, 'pause{Enter}');
    expect(await screen.findByText(/Spotify pause request sent/)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(api.get.mock.calls.filter(([path]) => path === '/api/now-playing').length).toBeGreaterThanOrEqual(2));
  });

  it('reports unsupported requests rather than pointing to removed settings', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async () => ({ intent: { moods: ['peaceful'] }, preferences: [] }));
    render(<App api={api} />); await send(user, 'play something peaceful');
    expect(await screen.findByText(/not yet available through chat/)).toBeInTheDocument();
  });

  it('keeps bounded user-only excerpts in page memory and clears them after Spotify results', async () => {
    const api = mockApi(); const user = userEvent.setup();
    api.post = vi.fn(async (_path, body) => body.message === 'devices please'
      ? { devices: [{ name: 'Private device', kind: 'Computer' }] }
      : { clarification: 'What mood?' });
    const view = render(<App api={api} />);
    for (let i = 0; i < 10; i += 1) await send(user, `request ${i}`);
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(10));
    await send(user, 'follow up');
    expect(api.post.mock.calls[10][1].context).toEqual(['request 2', 'request 3', 'request 4', 'request 5', 'request 6', 'request 7', 'request 8', 'request 9']);
    await send(user, 'devices please');
    await send(user, 'another request');
    expect(api.post.mock.calls[12][1]).toEqual({ message: 'another request' });
    view.unmount();
    render(<App api={api} />);
    await send(user, 'fresh page');
    expect(api.post.mock.calls[13][1]).toEqual({ message: 'fresh page' });
  });
});
