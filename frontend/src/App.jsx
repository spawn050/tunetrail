import React, { useEffect, useRef, useState } from 'react';
import { createApi } from './api.js';

const apiDefault = createApi();

export default function App({ api = apiDefault }) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [profile, setProfile] = useState([]);
  const [message, setMessage] = useState('');
  const [interpretation, setInterpretation] = useState(null);
  const [query, setQuery] = useState('');
  const [searchKind, setSearchKind] = useState('track');
  const [results, setResults] = useState([]);
  const [seed, setSeed] = useState('');
  const [seedKind, setSeedKind] = useState('artist');
  const [independent, setIndependent] = useState(false);
  const [enrich, setEnrich] = useState(false);
  const [discovery, setDiscovery] = useState(null);
  const [survey, setSurvey] = useState(-1);
  const [devices, setDevices] = useState([]);
  const [device, setDevice] = useState('');
  const [action, setAction] = useState('play_now');
  const [preview, setPreview] = useState(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const previewRef = useRef(null);
  const pending = useRef(false);

  useEffect(() => {
    let alive = true;
    api.start().then(() => api.get('/api/preferences')).then((data) => {
      if (alive) { setProfile(data); setReady(true); }
    }).catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [api]);

  async function run(fn) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(''); setNotice('');
    try { await fn(); } catch (e) { setError(e.message || 'Request failed'); }
    finally { pending.current = false; setBusy(false); }
  }
  async function refreshProfile() { setProfile(await api.get('/api/preferences')); }
  async function invalidate() {
    const old = previewRef.current;
    previewRef.current = null; setPreview(null);
    if (old?.proposal_id) await api.post('/api/cancel', { proposal_id: old.proposal_id });
  }
  function changePreviewInput(change) {
    // Drop the visible approval immediately, even if cancellation fails.
    run(async () => { await invalidate(); change(); });
  }
  async function feedback(item, response, source) {
    await invalidate();
    await api.post('/api/feedback', {
      subject: 'recording', name: item.title, artist: item.artists.join(', '),
      musicbrainz_id: item.recording_id, response, source,
    });
    await refreshProfile();
    setNotice(response === 'skip' || response === 'not_familiar' ? 'No preference changed.' : 'Lasting preference saved.');
  }
  const candidates = discovery?.result.candidates ?? [];

  return <main>
    <header><h1>TuneTrail</h1><p>Local discovery, explicit taste, and playback only after your approval.</p></header>
    <p className="disclosure">Chat is sent to your configured hosted LLM and leaves this machine. Spotify operations require existing local credentials; sign-in is not available here.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!ready && <p>Connecting to the local API…</p>}
    <fieldset disabled={!ready || busy}>
      <section><h2>Chat</h2>
        <form onSubmit={(e) => { e.preventDefault(); run(async () => {
          await invalidate();
          const result = await api.post('/api/chat', { message });
          setInterpretation(result); setMessage(''); await refreshProfile();
        }); }}>
          <label htmlFor="message">Request or lasting preference</label>
          <textarea id="message" value={message} maxLength={4000} required onChange={(e) => setMessage(e.target.value)} />
          <button>Send chat</button>
        </form>
        {interpretation?.clarification && <p>Clarification needed: {interpretation.clarification}</p>}
        {interpretation?.preferences?.length > 0 && <p>Clear lasting preferences saved to your profile.</p>}
        {interpretation?.intent && <details open><summary>Interpreted request</summary><pre>{JSON.stringify(interpretation.intent, null, 2)}</pre></details>}
      </section>

      <section><h2>Spotify catalog search</h2>
        <p>Search is for reference only. Spotify results are never used as independent discovery seeds or feedback identities.</p>
        <form onSubmit={(e) => { e.preventDefault(); run(async () => setResults(await api.get(`/api/catalog-search?q=${encodeURIComponent(query)}&kind=${searchKind}`))); }}>
          <label>Search type <select value={searchKind} onChange={(e) => setSearchKind(e.target.value)}><option value="track">Track</option><option value="artist">Artist</option></select></label>
          <label>Search Spotify <input value={query} onChange={(e) => setQuery(e.target.value)} required maxLength={200} /></label><button>Search</button>
        </form>
        <ul>{results.map((r) => <li key={r.spotify_uri}>{r.title}{r.subtitle ? ` — ${r.subtitle}` : ''} <small>Spotify</small></li>)}</ul>
      </section>

      <section><h2>Independent discovery</h2>
        <p>Supply a MusicBrainz UUID obtained independently, not translated from Spotify data. Namesake search is not available. ReccoBeats estimates are optional and provisional; its foundational metadata is Spotify-derived.</p>
        <form onSubmit={(e) => { e.preventDefault(); run(async () => {
          await invalidate(); setDiscovery(null); setSurvey(-1);
          const result = await api.post('/api/discovery', { seeds: [{ kind: seedKind, musicbrainz_id: seed }], independently_obtained: independent, limit: 10, enrich });
          setDiscovery(result);
        }); }}>
          <label>Seed type <select value={seedKind} onChange={(e) => setSeedKind(e.target.value)}><option value="artist">Artist</option><option value="recording">Recording</option></select></label>
          <label>Independent MusicBrainz UUID <input value={seed} onChange={(e) => setSeed(e.target.value)} required /></label>
          <label><input type="checkbox" checked={independent} onChange={(e) => setIndependent(e.target.checked)} required /> I obtained this ID independently of Spotify.</label>
          <label><input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} /> Request provisional ReccoBeats estimates</label>
          <button>Discover</button>
        </form>
        {discovery && <><p>{discovery.result.source_failures} source failures. {discovery.result.needs_more_seeds ? 'Fewer results than requested; try more independent seeds.' : ''}</p>
          <h3>Suggested music (MusicBrainz)</h3>
          {candidates.length === 0 && <p>No suggestions available.</p>}
          <ul className="tiles">{candidates.map((item) => <li key={item.recording_id}><strong>{item.title}</strong> — {item.artists.join(', ')}
            <div><button onClick={() => run(() => feedback(item, 'like', 'tile'))}>Like {item.title}</button> <button onClick={() => run(() => feedback(item, 'dislike', 'tile'))}>Dislike {item.title}</button></div>
          </li>)}</ul>
          {candidates.length > 0 && <button onClick={() => setSurvey(0)}>Start survey</button>}
          {survey >= 0 && survey < candidates.length && <div><h3>Survey {survey + 1} of {candidates.length}</h3><p>{candidates[survey].title} — {candidates[survey].artists.join(', ')}</p>
            {['like', 'dislike', 'not_familiar', 'skip'].map((value) => <button key={value} onClick={() => run(async () => { await feedback(candidates[survey], value, 'survey'); setSurvey(survey + 1); })}>{value.replace('_', ' ')}</button>)}
          </div>}
          {survey === candidates.length && survey >= 0 && <p>Survey complete.</p>}
        </>}
      </section>

      <section><h2>Profile</h2><p>Strength: -2 strongly dislike, -1 dislike, 0 neutral/mixed, +1 like, +2 strongly like.</p>
        {profile.length === 0 ? <p>No explicit preferences yet.</p> : <ul>{profile.map((p) => <li key={`${p.subject}-${p.name}-${p.artist || ''}-${p.musicbrainz_id || ''}`}>
          {p.subject}: {p.name}{p.artist ? ` — ${p.artist}` : ''} ({p.strength > 0 ? '+' : ''}{p.strength}; {p.source}){' '}
          <button onClick={() => run(async () => { await invalidate(); await api.post('/api/preferences/remove', { subject: p.subject, name: p.name, artist: p.artist, musicbrainz_id: p.musicbrainz_id, strength: p.strength }); await refreshProfile(); })}>Remove {p.name}</button>
        </li>)}</ul>}
        <label><input type="checkbox" checked={confirmReset} onChange={(e) => setConfirmReset(e.target.checked)} /> Confirm removal of all explicit preferences only</label>
        <button disabled={!confirmReset} onClick={() => run(async () => { await invalidate(); await api.post('/api/preferences/reset', { confirm_reset: true }); await refreshProfile(); setConfirmReset(false); setNotice('Explicit preferences reset.'); })}>Reset explicit profile</button>
      </section>

      <section><h2>Playback preview</h2>
        <p>Spotify Premium and an active device are required. Queue append requires one track per approval.</p>
        <button onClick={() => run(async () => setDevices(await api.get('/api/devices')))}>Refresh devices</button>
        <label>Device <select value={device} onChange={(e) => { const value = e.target.value; changePreviewInput(() => setDevice(value)); }}><option value="">Spotify active device (may change)</option>{devices.map((d) => <option key={d.id} value={d.id}>{d.name}{d.is_active ? ' (active)' : ''}</option>)}</select></label>
        <label>Action <select value={action} onChange={(e) => { const value = e.target.value; changePreviewInput(() => setAction(value)); }}><option value="play_now">Play now</option><option value="append_queue">Append one track</option></select></label>
        <button disabled={!discovery || !!interpretation?.clarification || !!interpretation?.intent?.clarification_needed} onClick={() => run(async () => {
          await invalidate();
          const intent = { ...(interpretation?.intent || {}), action, ...(action === 'append_queue' ? { queue_size: 1 } : {}) };
          const result = await api.post('/api/preview', { discovery_id: discovery.discovery_id, intent, device_id: device || null });
          previewRef.current = result; setPreview(result);
        })}>Create preview</button>
        {preview && <div><h3>Review before approval</h3>
          <p>Action: {action === 'play_now' ? 'Play now' : 'Append to queue'}; device: {devices.find((d) => d.id === device)?.name || (device || 'Spotify active device')}.</p>
          <ol>{preview.matches.map((match) => <li key={match.uri}><strong>{match.title}</strong> — {match.artists.join(', ')}<p>Spotify URI: {match.uri}</p><p>Match: {match.evidence}</p><p>Ranking: {JSON.stringify(match.ranked.signals)}; score: {match.ranked.score}</p></li>)}</ol>
          {preview.issues.map((issue, i) => <p className="error" key={i}>{issue}</p>)}
          {!preview.proposal_id && <p>No approvable proposal is available.</p>}
          {preview.proposal_id && <button onClick={() => run(async () => {
            const id = previewRef.current?.proposal_id; previewRef.current = null; setPreview(null);
            try { await api.post('/api/approval', { proposal_id: id, explicitly_approve: true }); setNotice('Playback request sent; this is not evidence of listening.'); }
            catch (e) { throw new Error(`${e.message}. The request may have succeeded; inspect Spotify playback before retrying.`); }
          })}>Approve {action === 'play_now' ? 'play now' : 'queue append'}</button>}
          <button onClick={() => run(invalidate)}>Cancel preview</button>
        </div>}
      </section>
    </fieldset>
  </main>;
}
