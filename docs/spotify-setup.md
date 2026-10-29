# Spotify developer-app setup

> The local interface supports Spotify sign-in, but not logout. Live read-only
> evaluation and playback acceptance require separate authorization; every playback
> action also requires explicit approval.

TuneTrail is a personal Spotify controller. Spotify Premium is required for
playback and queue control.

## Create the application

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an app for personal use and copy its **Client ID**. Do not create or
   store a client secret: TuneTrail uses Authorization Code with PKCE.
3. In the app settings, add this Redirect URI exactly:

   ```text
   http://127.0.0.1:8765/callback
   ```

   If you set a different local callback URI, add that exact URI to Spotify and
   set `TUNETRAIL_SPOTIFY_REDIRECT_URI` to the same value.

4. Create the local, Git-ignored configuration file from the example:

   If `.env` does not already exist, run `cp .env.example .env`. If it exists,
   edit it instead; do not overwrite it.

   Add the Client ID to `.env`:

   ```text
   TUNETRAIL_SPOTIFY_CLIENT_ID=your-client-id
   ```

   TuneTrail loads `.env` automatically at startup. Shell environment variables
   override `.env` values when needed. The client ID is public. Never put an
   access token, refresh token, API key, or any other secret in a tracked file.

## Requested scopes

TuneTrail currently requests only:

- `user-read-private`;
- `user-read-playback-state` — list devices and inspect playback;
- `user-read-currently-playing`;
- `user-modify-playback-state` — start playback and add a track to the queue;
- `user-read-recently-played` — retrieve a bounded page of recent plays on request.

Recent plays are filtered and returned locally, not sent to the hosted model or
persisted. At most 50 plays are fetched; coverage of a requested period is not
guaranteed. This use has unresolved Spotify policy questions; local processing does
not establish clearance. Existing credentials may lack the new scope: reauthorization
requires explicit confirmation before replacing the credential file. Check Spotify's
current developer documentation before using live features.

The chat API returns `recent_plays`, `period`, and a coverage `notice` for supported
requests. Each play contains only title, artists, and play time, displayed in chat. Supported windows
are recent, the last 24 hours, and a rolling seven days; statistics are not supported.

## Connect from the interface

Start TuneTrail, open `http://127.0.0.1:8766/`, and send `connect Spotify` in chat.
If configured, select **Open Spotify authorization** in the conversation.
Sign in and approve the requested scopes on Spotify; return to TuneTrail to see the
result. The local callback listener times out after three minutes. If a credential
file already exists, replacement requires the separate confirmation message. If it changes while
authorization is in progress, TuneTrail preserves it and reports a failure. A
successful sign-in does not verify playback or imply provider-policy clearance.

Chat also accepts the exact messages `connect Spotify`, `log in to Spotify`,
`Spotify connection status`, and `cancel Spotify connection`. If a credential file
exists, the API requests a separate `confirm replace Spotify credentials` message;
the confirmation expires after five minutes and is bound to the existing file.
Natural-language connection requests, status, confirmation and cancellation are also
interpreted by the configured hosted chat provider. Clear confirmation is required;
ambiguous replies should be clarified. The conversation shows an authorization link when ready and reports the result.
Never paste authorization URLs or tokens into chat.

On success, Spotify tokens are stored at `~/.config/tunetrail/auth.json` with
owner-only (`0600`) permissions. There is currently no logout control in the
interface. Failed token refresh preserves the existing credential file. Ask the
owner before removing it. Chat asks for confirmation before replacement.
