# Local interface

From the repository checkout, run `npm ci` and `npm run build` in `frontend/`,
then `uv run python -m tunetrail.api` in the repository root, or use `./dev.sh`
after installing dependencies. Open `http://127.0.0.1:8766/`. Node.js 20.19+
or 22.12+ is required. The Python server serves built files from the checkout,
not a wheel. Do not expose the server publicly or use a separate frontend origin;
see `docs/phase-4u-api.md` for the security boundary.

The interface shows read-only now-playing information above chat. The album-art
area is currently a placeholder. Spotify is polled every 15 seconds while the UI
is open; the view is refreshed after player commands and successful authorization.

Send `connect Spotify` to start authorization; if credentials already exist,
replacement requires the separate confirmation shown in chat. Open the returned
link and approve access in Spotify. See `docs/spotify-setup.md` for setup, status
and cancellation commands. Recent-play results and their coverage warning appear
in chat; only a bounded page is fetched, so a full week is not guaranteed.

Exact player commands (`pause`, `play`, `resume`, `next`, `next track`, `previous`,
`previous track`) authorize one action on Spotify's active device. If a command
fails, it may still have succeeded: inspect Spotify before retrying. A playback
request is not proof of listening. Enter sends; Shift+Enter adds a newline.

Natural-language Spotify connection, status, cancellation, confirmation, device
listing and device selection use the configured hosted LLM. `list devices` also
works locally. Device names must match a unique available name exactly; restricted
or unidentified devices cannot be selected. A transfer needs a separate clear
confirmation in chat, expires after five minutes, and is not retried automatically.
If it fails, check Spotify before retrying; the transfer may have succeeded. Device
results remain local and are not sent to the LLM. The device list may change.

Other chat goes to the configured hosted LLM and leaves the machine. Clear lasting
preferences are saved immediately; ambiguity requires clarification. Spotify
recent-play results are not sent to the LLM or persisted. Do not paste credentials
or authorization links into chat. Discovery and playing a
specific song are not yet available through chat. The previous discovery, profile,
and preview controls are no longer in the UI; existing backend APIs remain available.
No data is deleted by removing the controls. An incompatible profile database still
stops startup without replacement.

Frontend tests use a mocked API; Python API tests use mocked providers. Live
read-only evaluation and playback acceptance require separate owner authorization;
playback also needs Spotify Premium, an active device and explicit approval for
every mutation. Local processing does not establish provider-policy clearance.
