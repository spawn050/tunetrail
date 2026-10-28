# Phase 4U local API

Run `uv run python -m tunetrail.api` on the local machine. The server binds only
`127.0.0.1:8766`; it is not intended for public deployment, reverse proxies exposed
to a network, or multiple users. Access logging is disabled. This phase provides no
React interface and no Spotify sign-in endpoint. Spotify operations require an
existing local credential and configured client ID; this API does not delete or
replace credentials. Provider access and playback have not been tested live.

Requests must use host `127.0.0.1:8766`. Browser origins, when present, must be
exactly `http://127.0.0.1:8766`; cross-site fetches are rejected and CORS is not
enabled. Retrieve `GET /api/session` to obtain an in-memory session token, and
send it as `X-TuneTrail-Token` on JSON POST requests. POST bodies must have a
content length of at most 65536 bytes. Tokens change on restart and are not Spotify
credentials. This protects against unintended browser-origin requests, not malicious
software or arbitrary users with access to the same local account. A future frontend
must be served through the same origin or deliberately revisit this security design.

## Operations

- `POST /api/chat`: `{ "message": "..." }`; returns structured interpretation and
  immediately saves clear lasting preferences. **Chat is sent to the configured
  hosted LLM and leaves this machine.** The profile and credentials are not sent
  to the parser, and raw chat is not persisted by this service.
- `GET /api/preferences`, `POST /api/preferences`,
  `POST /api/preferences/remove`, and `POST /api/preferences/reset`: inspect,
  set, remove, or reset explicit preferences. Reset requires
  `{ "confirm_reset": true }` and removes only explicit preference rows.
- `POST /api/feedback`: record an explicit like or dislike; skip and not-familiar
  do not overwrite preferences.
- `GET /api/search?q=...` searches Spotify tracks; `GET /api/devices` lists devices.
  Spotify search results must never be used as external discovery inputs.
- `POST /api/discovery`: supply up to five `{ "kind": "artist" | "recording",
  "musicbrainz_id": "..." }` seeds, a `limit` of 1–20 and
  `"independently_obtained": true`. Optional `"enrich": true` requests provisional
  ReccoBeats estimates. The caller must genuinely obtain IDs independently, not
  translate Spotify metadata. There is no name search or namesake selection.
  Returns a short-lived discovery ID and independent results.
- `POST /api/preview`: supply `discovery_id`, a validated `intent`, and optionally
  `device_id`. It ranks server-held discovery results and resolves Spotify matches;
  the response contains signals, match evidence, issues, and the exact action.
  Unsupported constraints prevent proposals. Discovery and proposal state expires
  after at most five minutes or on restart and is not persisted.
- `POST /api/approval`: send `{ "proposal_id": "...", "explicitly_approve": true }`
  **only after the owner has reviewed and explicitly approved the exact preview**.
  The server executes the stored action, never client-supplied URIs or device changes.
  A proposal is single-use even if the request fails; create a new preview before
  trying again. `POST /api/cancel` with a proposal ID cancels it without playback.
  Queue append remains one track per proposal and approval.

An approval or request is not evidence of listening. A Spotify mutation may succeed
even if the subsequent state refresh fails; inspect playback before retrying. Only
mocked offline tests have been run. The independent identity assertion depends on
the caller, source coverage and matching are unverified, and ReccoBeats policy
questions remain open. Live read-only tests and playback acceptance need separate
owner authorization, and every playback/queue mutation needs explicit approval.
