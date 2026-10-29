import React, { useEffect, useRef, useState } from 'react';
import { createApi } from './api.js';

const apiDefault = createApi();

function timeLabel(ms) {
  if (ms == null) return '–:––';
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function authorizationUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'accounts.spotify.com' && url.port === '' && url.pathname === '/authorize' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function connectionMessage(connection) {
  if (connection.message) return connection.message;
  if (connection.cancelled || connection.status === 'cancelled') return 'Spotify authorization cancelled.';
  if (connection.status === 'connected') return 'Spotify authorization completed. This does not verify playback.';
  if (connection.status === 'failed') return 'Spotify authorization failed or timed out. Existing credentials were preserved.';
  if (connection.status === 'pending') return 'Waiting for Spotify authorization. Open the authorization link if shown; it expires after three minutes.';
  if (connection.status === 'confirmation_required') return 'A credential file exists. Send “confirm replace Spotify credentials” to authorize replacement, or “cancel Spotify connection” to cancel.';
  return 'No Spotify authorization is in progress. Existing credential status does not verify access.';
}

function replyFor(result) {
  if (result.control) return { text: `Spotify ${result.control} request sent. Check the player for its current state.` };
  if (result.device_message) return { text: result.device_message };
  if (Array.isArray(result.devices)) return { text: result.devices.length ? 'Available Spotify devices:' : 'No Spotify devices found. Open Spotify on a device and try again.', devices: result.devices };
  if (result.spotify_connection) return { text: connectionMessage(result.spotify_connection) };
  if (Array.isArray(result.recent_plays)) return {
    text: result.recent_plays.length ? 'Recent Spotify plays:' : 'No plays found in the available results.',
    plays: result.recent_plays,
    notice: result.notice,
  };
  if (result.clarification) return { text: `Clarification needed: ${result.clarification}` };
  if (result.intent?.clarification_needed) return { text: 'Could you clarify your request?' };
  if (result.preferences?.length) return { text: 'Clear lasting preferences saved to your profile.' };
  return { text: 'I interpreted your request, but that action is not yet available through chat.' };
}

export default function App({ api = apiDefault }) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [conversation, setConversation] = useState([]);
  const [playback, setPlayback] = useState(null);
  const [playbackStatus, setPlaybackStatus] = useState('loading');
  const [playbackRefresh, setPlaybackRefresh] = useState(0);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [connection, setConnection] = useState(null);
  const pending = useRef(false);
  const conversationRef = useRef(null);
  const composerRef = useRef(null);
  const restoreFocus = useRef(false);
  // Excerpts live only in this page's memory, never browser storage or server state.
  const contextRef = useRef({ recent: [], compressed: [] });

  useEffect(() => {
    let alive = true;
    api.start().then(() => { if (alive) setReady(true); })
      .catch((e) => { if (alive) setError(e.message || 'Could not connect to the local API.'); });
    return () => { alive = false; };
  }, [api, connectionAttempt]);

  useEffect(() => {
    if (!ready) return undefined;
    let alive = true;
    let timer;
    async function refresh() {
      try {
        const state = await api.get('/api/now-playing');
        if (alive) { setPlayback(state); setPlaybackStatus('ready'); }
      } catch {
        if (alive) { setPlayback(null); setPlaybackStatus('error'); }
      } finally {
        if (alive) timer = setTimeout(refresh, 15000);
      }
    }
    refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [api, ready, playbackRefresh]);

  useEffect(() => {
    if (connection?.status !== 'pending') return undefined;
    let alive = true;
    let timer;
    async function check() {
      try {
        const status = await api.get('/api/spotify/connect');
        if (!alive) return;
        if (status.status !== 'pending') {
          setConnection(status);
          setConversation((entries) => [...entries, { role: 'assistant', text: connectionMessage(status) }]);
          if (status.status === 'connected') setPlaybackRefresh((n) => n + 1);
          return;
        }
      } catch {
        if (!alive) return;
        setConnection(null);
        setConversation((entries) => [...entries, { role: 'assistant', error: true, text: 'Could not check Spotify connection status. Send “Spotify connection status” to check again.' }]);
        return;
      }
      timer = setTimeout(check, 2000);
    }
    timer = setTimeout(check, 2000);
    return () => { alive = false; clearTimeout(timer); };
  }, [api, connection?.status]);

  useEffect(() => {
    if (conversationRef.current) conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
  }, [conversation, connection, busy]);

  useEffect(() => {
    if (!busy && restoreFocus.current) {
      composerRef.current?.focus();
      restoreFocus.current = false;
    }
  }, [busy]);

  async function submit(e) {
    e.preventDefault();
    const submitted = message.trim();
    if (!submitted || pending.current) return;
    pending.current = true; setBusy(true); setMessage(''); setError('');
    setConversation((entries) => [...entries, { role: 'user', text: submitted }]);
    try {
      const context = [...contextRef.current.compressed, ...contextRef.current.recent];
      const result = await api.post('/api/chat', context.length ? { message: submitted, context } : { message: submitted });
      // Never retain Spotify responses or requests that produced them as model context.
      if (result.control || result.device_message || result.devices || result.spotify_connection || result.recent_plays) {
        contextRef.current = { recent: [], compressed: [] };
      } else if (!/spotify:|open\.spotify\.com|https?:\/\//i.test(submitted)) {
        const recent = [...contextRef.current.recent, submitted.slice(0, 500)];
        const compressed = [...contextRef.current.compressed];
        if (recent.length > 4) compressed.push(recent.shift().slice(0, 160));
        contextRef.current = { recent, compressed: compressed.slice(-4) };
      }
      if (result.spotify_connection) {
        const next = result.spotify_connection;
        const url = next.authorization_url ? authorizationUrl(next.authorization_url) : null;
        if (next.authorization_url && !url) throw new Error('The Spotify authorization link was invalid.');
        setConnection((previous) => next.status === 'pending' && !url && previous?.status === 'pending' ? previous : { ...next, url });
      }
      setConversation((entries) => [...entries, { role: 'assistant', ...replyFor(result) }]);
      if (result.control || result.device_transfer_requested || result.spotify_connection?.status === 'connected') setPlaybackRefresh((n) => n + 1);
    } catch (e) {
      setConversation((entries) => [...entries, { role: 'assistant', error: true, text: e.message || 'Request failed.' }]);
    } finally { pending.current = false; restoreFocus.current = true; setBusy(false); }
  }

  return <main className="app-shell">
    <header className="app-header"><h1>♪ TuneTrail</h1></header>
    {error && <p role="alert" className="error">{error}</p>}
    {!ready && <><p>Connecting to the local API…</p>{error && <button onClick={() => { setError(''); setConnectionAttempt((n) => n + 1); }}>Retry connection</button>}</>}
    <section className="now-playing" aria-label="Spotify now playing">
      <div className="album-placeholder" aria-hidden="true">♪</div>
      <div className="track-info">
        <p className="eyebrow">SPOTIFY · NOW PLAYING</p>
        {playbackStatus === 'loading' && <p>Checking Spotify…</p>}
        {playbackStatus === 'error' && <p>Could not load Spotify playback. Check your connection.</p>}
        {playbackStatus === 'ready' && !playback && <p>No Spotify track available right now.</p>}
        {playbackStatus === 'ready' && playback && <>
          <h2>{playback.title}</h2><p>{playback.artists.join(', ')}</p>
          <div className="progress-row"><span>{timeLabel(playback.progress_ms)}</span>
            <progress aria-label="Track progress" max={playback.duration_ms} value={Math.min(playback.progress_ms ?? 0, playback.duration_ms)} />
            <span>{timeLabel(playback.duration_ms)}</span></div>
          <small>{playback.is_playing ? 'Playing' : 'Paused'} · Updated every 15 seconds</small>
        </>}
      </div>
    </section>
    <section ref={conversationRef} className="conversation" aria-label="Conversation" aria-live="polite">
      {conversation.length === 0 && <div className="chat-entry assistant"><strong>TuneTrail</strong><p>Say “connect Spotify” to sign in, or ask about your recent plays.</p></div>}
      {conversation.map((entry, index) => <div className={`chat-entry ${entry.role}`} key={index}>
        <strong>{entry.role === 'user' ? 'You' : 'TuneTrail'}</strong><p role={entry.error ? 'alert' : undefined}>{entry.text}</p>
        {entry.plays?.length > 0 && <ol>{entry.plays.map((play, i) => <li key={i}><strong>{play.title}</strong> — {play.artists.join(', ')} <time dateTime={play.played_at}>{new Date(play.played_at).toLocaleString()}</time></li>)}</ol>}
        {entry.devices?.length > 0 && <ul>{entry.devices.map((device, i) => <li key={i}><strong>{device.name}</strong> — {device.kind}{device.is_active ? ' · Active' : ''}{device.is_restricted || !device.selectable ? ' · Cannot select' : ''}</li>)}</ul>}
        {entry.notice && <p className="coverage">{entry.notice}</p>}
      </div>)}
      {connection?.status === 'pending' && connection.url && <div className="chat-entry assistant"><a href={connection.url} target="_blank" rel="noopener noreferrer">Open Spotify authorization</a></div>}
      {busy && <div className="chat-entry assistant waiting" role="status"><span className="spinner" aria-hidden="true" /> Waiting for TuneTrail…</div>}
    </section>
    <form className="composer" onSubmit={submit}>
      <label className="sr-only" htmlFor="message">Request or lasting preference</label>
      <textarea ref={composerRef} id="message" value={message} maxLength={4000} required placeholder="Ask TuneTrail about music…" disabled={!ready || busy} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
          e.preventDefault();
          if (!e.repeat) e.currentTarget.form.requestSubmit();
        }
      }} />
      <button disabled={!ready || busy}>Send chat</button>
      <p className="disclosure">Enter sends; Shift+Enter adds a line. Exact Spotify login and playback commands run locally. Other chat and bounded earlier user excerpts go to your configured hosted AI provider; context clears on refresh or close. Do not paste secrets or authorization links.</p>
    </form>
  </main>;
}
