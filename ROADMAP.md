# TuneTrail Roadmap

## Vision

TuneTrail is a personal, terminal-first Spotify controller that turns natural-language requests into personalized, reviewable music queues.

Examples:

- “Play something peaceful for studying.”
- “Play Taylor Swift from 2008 to 2012.”
- “Give me familiar music with a few discoveries.”
- “More like this, but calmer.”

Spotify remains the catalog and playback authority. TuneTrail interprets intent, gathers candidates, ranks them against the user’s taste, and asks for approval before changing playback.

## Product Boundaries

### Goals

- Control an active Spotify device from a keyboard-first TUI.
- Translate natural-language requests into validated listening constraints.
- Create personalized queues using listening history and explicit feedback.
- Support optional music-discovery sources without making them required.
- Explain why tracks were selected.
- Keep user profile data local by default.

### Non-goals for v1

- Playing or streaming audio inside the application.
- A public, multi-user Spotify product.
- Training models on Spotify data.
- Unattended playback changes made by an autonomous agent.
- Replacing Spotify’s native recommendation system.

## Core Architecture

```text
┌─────────────┐
│ Textual TUI │
└──────┬──────┘
       │
┌──────▼──────────────────┐
│ Application / Services   │
├─────────────────────────┤
│ LangGraph request graph │
│ Spotify gateway          │
│ Recommendation service   │
│ LLM provider factory     │
│ SQLite repositories      │
└──────┬──────────────────┘
       │
 ┌─────┴────────────────────────────────────┐
 │ Spotify │ Last.fm │ MusicBrainz │ ReccoBeats │
 └──────────────────────────────────────────┘
```

### Technology choices

| Area | Choice |
| --- | --- |
| Language | Python 3.12+ |
| TUI | Textual |
| Workflow orchestration | LangGraph |
| Default LLM gateway | OpenRouter via `langchain-openrouter` |
| Future direct LLMs | Anthropic and OpenAI adapters |
| Data validation | Pydantic |
| HTTP | httpx |
| Persistence | SQLite |
| Testing | pytest, pytest-asyncio, respx/httpx mocks |
| Quality tooling | Ruff and Pyright |
| Spotify authentication | Authorization Code with PKCE |

## Request-to-Playback Flow

```text
Natural-language request
  → LLM parses a structured ListeningIntent
  → validate constraints / ask clarification if necessary
  → load local taste profile and current session context
  → retrieve Spotify baseline candidates
  → optionally enrich or expand from external sources
  → resolve every candidate to a playable Spotify track
  → apply deterministic ranking, diversity, and repetition rules
  → display a queue preview and explanations
  → user approves, edits, regenerates, or cancels
  → execute Spotify playback action
  → save local feedback and session signals
```

The LLM never invents final track IDs or directly mutates Spotify state. Candidate matching, ranking, and player actions remain deterministic application code.

## LangGraph Design

```text
START
  → parse_intent
  → validate_or_request_clarification
  → load_profile_and_session
  → retrieve_spotify_baseline
  → retrieve_optional_sources
  → resolve_to_spotify
  → rank_and_validate_queue
  → present_queue_preview
  → interrupt: approve | edit | regenerate | cancel
  → execute_spotify_action
  → persist_feedback_and_session
  → END
```

The approval interrupt is mandatory before `play now`, replacing a queue, appending tracks, or creating a playlist.

## Personalization Strategy

### Spotify baseline

Required, local taste signals:

- top tracks and artists over short, medium, and long windows;
- recently played tracks;
- user-owned or collaborative playlists explicitly selected for ingestion;
- current playback and active-device state;
- constrained Spotify catalog search for exact tracks, artists, and years.

Searching Spotify for generic “peaceful playlists” is only a fallback, not personalized discovery.

For vague requests such as “something peaceful,” the intended strategy is: derive seeds from the user's profile, expand those seeds with enabled discovery sources, filter/rank for the requested mood and activity, then resolve the result to Spotify. It is **not** a generic public-playlist search.

### External discovery adapters

| Source | Role | Delivery phase |
| --- | --- | --- |
| Last.fm | Historical listening, tags, similar artists/tracks | First optional source |
| MusicBrainz | Canonical metadata, IDs, years, aliases, editions | Second optional source |
| ReccoBeats | Candidate expansion and mood/audio-feature signals | Experimental, feature-gated |

All external candidates must be resolved to a playable Spotify URI before they can be shown in a final queue.

### Source-specific operating rules

- **Last.fm:** highest-value optional source. It can provide a configured user's long-term top/recent listening history, similar artists/tracks, and community tags. Public reads need the application's API key; a Last.fm session is not required for these reads. It must remain optional because the user may not have a Last.fm account or scrobbling history.
- **MusicBrainz:** metadata/resolution source, not a real-time recommender. It is appropriate for MusicBrainz IDs, ISRCs, aliases, release dates, editions, and genres. Use a meaningful `User-Agent`, keep traffic at or below one request per second, cache stable metadata, and do not block every interactive request on it.
- **ReccoBeats:** experimental candidate/audio-feature source. It currently requires no API key, has unpublished internal rate limits, and states that core track metadata is aggregated from Spotify data. Keep it off by default, avoid broad/long-lived caching, and make it a measured go/no-go decision rather than an MVP dependency.
- **Spotify:** final catalog and playback authority only. An external source may suggest a name, but it does not make a track playable until the resolver maps it to a Spotify URI with sufficient confidence.

### Scoring

Initial ranking is transparent and deterministic:

```text
score =
  request constraint match
  + taste affinity
  + mood/activity compatibility
  + familiarity/discovery preference
  + source confidence
  + diversity bonus
  - recent-play penalty
  - duplicate artist/album penalties
  - low-confidence Spotify-match penalty
```

Hard constraints include artist, year range, explicit-content preference, availability, and valid Spotify resolution.

## Data Model

### Listening intent

The LLM outputs a validated schema such as:

```text
ListeningIntent
  action: play_now | append_queue | replace_queue | refine
  artists: list[str]
  track_terms: list[str]
  year_start/year_end: int | None
  moods: list[str]
  activities: list[str]
  duration_minutes: int | None
  queue_size: int
  familiarity: familiar | mixed | discovery
  explicit_allowed: bool | None
  clarification_needed: bool
```

### Candidate lifecycle

```text
Candidate
  → external/source metadata and provenance
  → Spotify resolver
  → ResolvedTrack with URI, match confidence, and score breakdown
  → approved queue item
```

Resolution prefers ISRC matching, then conservative normalized artist/title/duration matching. Low-confidence matches are excluded or require explicit review.

Spotify identifiers and external metadata can be incomplete or disagree across editions, remasters, clean/explicit releases, and regional catalog variants. Resolver output must therefore retain provenance and a confidence score rather than assuming every name match is correct.

### Local profile

SQLite stores only application-owned signals:

- compact taste preferences derived from top/recent listening;
- explicit feedback: like, dislike, more/less like this, too familiar, too distracting;
- opted-in playlist references;
- generated queue/session history used to prevent repetition;
- optional external-source configuration and cached metadata.

## Credentials and Privacy

Keyring is optional rather than required for the personal-tool use case.

- Static setup uses the Git-ignored project-root `.env` file, loaded automatically; shell environment variables override it. Copy `.env.example` to begin.
- Spotify token material belongs in `~/.config/tunetrail/auth.json`, also `0600`.
- Use Spotify PKCE, so no Spotify client secret is stored locally.
- The Spotify client ID is not secret; refresh tokens and LLM/API keys are secret.
- Do not log tokens, full credentials, or raw listening history.
- Do not send raw Spotify listening history, playlist contents, or a persistent catalog export to the LLM. The normal LLM context is the user request plus a compact, local, non-sensitive taste summary.
- Spotify content and metadata must not be used to train an ML/AI model. TuneTrail uses LLM inference only for request interpretation and explanation.
- An optional OS-keyring backend can be added behind a credential-store interface later.

OpenRouter is the initial LLM gateway. Provider selection should be configuration-driven:

```text
TUNETRAIL_LLM_PROVIDER=openrouter | anthropic | openai
TUNETRAIL_LLM_MODEL=<model identifier>
```

Use strict structured output, low temperature, pinned model versions for evaluations, and no-data-collection routing where supported.

OpenRouter is a gateway, not a hard dependency in the domain layer. Every graph node consumes the provider-neutral LangChain chat-model interface so an agent can later add `ChatAnthropic` or `ChatOpenAI` without changing the intent schema or recommendation workflow.

## Spotify Constraints

- Spotify Premium is required for queue and playback-control APIs.
- The project owner has Spotify Premium; this satisfies the personal-use playback prerequisite.
- Use PKCE with a `http://127.0.0.1:<port>/callback` redirect URI.
- Require an active device and make device selection visible in the TUI.
- Respect API rate limits and `Retry-After` responses.
- Do not rely on deprecated Spotify recommendation or audio-feature APIs.
- Serialize related player commands and refresh state after mutations: Spotify does not guarantee execution order when queue and other player endpoints are used together.
- Request only the scopes needed for enabled features:
  - `user-modify-playback-state` for player controls, start playback, and queue changes;
  - `user-read-playback-state` for current playback/devices;
  - `user-read-currently-playing` for current track details;
  - `user-top-read` for taste bootstrap;
  - `user-read-recently-played` for repetition avoidance;
  - `playlist-read-private` and `playlist-read-collaborative` only for opted-in playlist signals;
  - playlist-write scopes only if save-as-playlist is implemented.
- Treat TuneTrail as a personal/private application. Current Spotify development mode requires the app owner to remain Premium and supports only five allowlisted users for newly created apps. Extended-quota access is not a realistic public-product path for this project: Spotify currently restricts new applications to established organizations meeting high-scale requirements.
- Playlist-item access in current development mode is limited to playlists the user owns or collaborates on. Do not design personalization around reading arbitrary public playlist contents.
- Spotify search results are catalog discovery only. Search returns a limited page of results and must be paginated deliberately when more candidates are needed.
- The recommendation endpoint appears in some Spotify documentation but is deprecated; do not use it. Likewise, do not build features around deprecated audio features, popularity, or other removed fields.
- Spotify API behavior changes regularly. Before implementing an endpoint, verify it against Spotify's current OpenAPI specification and current developer documentation rather than relying on this roadmap alone.

### References to recheck during implementation

- Spotify Web API OpenAPI schema: `https://developer.spotify.com/reference/web-api/open-api-schema.yaml`
- Spotify authorization with PKCE: `https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow`
- Spotify quota modes and current development-mode limits: `https://developer.spotify.com/documentation/web-api/concepts/quota-modes`
- Spotify current migration/change guidance: `https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide`
- LangGraph overview: `https://docs.langchain.com/oss/python/langgraph/overview`
- LangChain OpenRouter integration: `https://docs.langchain.com/oss/python/integrations/chat/openrouter`
- Last.fm API: `https://www.last.fm/api`
- MusicBrainz API and rate limiting: `https://musicbrainz.org/doc/MusicBrainz_API` and `https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting`
- ReccoBeats documentation and terms: `https://reccobeats.com/docs/documentation/introduction` and `https://reccobeats.com/docs/documentation/terms-of-service`

## Delivery Phases

### Scoreboard Rules

- Mark an item complete only after its tests or stated verification have passed.
- Keep unchecked items in place; add brief implementation notes below a phase when a decision or deviation matters.
- Do not begin a phase's user-facing features until its **phase gate** is satisfied. Preparatory research may happen earlier.
- Every agent should update this file in the same change set as the completed work.

### Phase 0 — Foundation


**Goal:** establish a typed, testable, secure project base.

- [x] Initialize a Python 3.12+ package with `pyproject.toml` and `src/tunetrail/` layout.
- [x] Add runtime dependencies: Textual, Pydantic, httpx, LangGraph, LangChain core, and OpenRouter integration.
- [x] Add development dependencies: pytest, pytest-asyncio, respx, Ruff, and Pyright.
- [x] Configure Ruff formatting/linting and Pyright strictness appropriate for the project.
- [x] Configure pytest, including async test support and a separate test package structure.
- [ ] Add a GitHub Actions or equivalent CI workflow that runs format/lint, type checks, and tests.
- [x] Implement typed application settings sourced from environment variables and a local config file.
- [x] Define standard local paths for configuration, state, cache, and SQLite data.
- [x] Implement redacted structured logging; ensure tokens and authorization headers cannot be logged.
- [x] Implement file-permission checks for local secret/token files.
- [x] Define initial Pydantic domain models: `ListeningIntent`, `Candidate`, `ResolvedTrack`, device, playback, queue, feedback, and profile summaries.
- [x] Define protocol interfaces for Spotify, credential storage, LLM creation, repositories, candidate sources, resolver, and ranker.
- [x] Add test fixtures and factories for domain objects without real credentials.

**Phase gate:** a clean checkout installs successfully; formatting/lint, type checks, and tests pass; no secret path is tracked by Git.

### Phase 1 — Spotify control


**Goal:** securely authenticate and control Spotify from a minimal TUI.

- [x] Create Spotify developer-app setup documentation, including the required `127.0.0.1` redirect URI and scopes.
- [x] Implement PKCE authorization URL generation with state, verifier, and challenge validation.
- [x] Implement a local loopback callback receiver and OAuth code exchange.
- [x] Implement refresh-token persistence using the default `0600` local credential file.
- [x] Implement token refresh and expired/revoked-token recovery.
- [x] Implement a logout command that removes local Spotify token material.
- [x] Build a typed Spotify HTTP client with bearer-token injection and normalized API errors.
- [x] Implement retry/backoff behavior, including Spotify `429` and `Retry-After` handling.
- [x] Implement current-user and playback-state retrieval.
- [x] Implement active-device listing and device selection.
- [x] Implement player controls: play/resume, pause, previous, next, and selected-device targeting.
- [x] Implement track search and add-to-queue for an explicit Spotify URI.
- [x] Handle no-device, Premium/access, missing-scope, unavailable-track, and authorization errors in user-facing messages.
- [x] Build a minimal Textual shell showing auth state, active device, and now-playing state.
- [x] Add mocked Spotify-client unit/integration tests for auth, refresh, player calls, and error handling.

**Phase gate:** the app can authenticate, survive restart via token refresh, select an active device, queue a known track, and control playback with mocked coverage for failures.

### Phase 2 — Safe TUI queue workflow


**Goal:** make TuneTrail a useful keyboard-first Spotify controller before AI features.

- [x] Design the Textual layout for now playing, device, command input, results, queue preview, notifications, and shortcuts.
- [x] Implement keyboard command focus, history, validation, and help/shortcut display.
- [x] Implement explicit catalog-search commands for track, artist, album, and playlist contexts.
- [x] Display catalog search results with Spotify attribution and selection controls.
- [x] Implement a reviewable queue-preview model independent of Spotify mutations.
- [x] Implement `play now` confirmation and selected-device playback.
- [x] Implement `append to queue` confirmation.
- [x] Implement cancel/back behavior that guarantees no player mutation occurs.
- [x] Retrieve and display the current Spotify queue when available.
- [x] Show active device, current track, playback progress, and command/action status.
- [x] Persist a minimal local action history without storing tokens or unnecessary catalog data.
- [x] Add Textual interaction tests for search, selection, confirmation, cancellation, and error states.

**Phase gate:** keyboard-only use can search, preview, play, append, cancel, and recover from common Spotify failures without AI features.

### Phase 3 — LangGraph and natural language


**Goal:** turn clear natural-language requests into safe, reviewable Spotify actions.

- [x] Implement an LLM provider factory with OpenRouter as the first backend.
- [x] Add configuration for model name, temperature, retry behavior, and provider data-collection policy.
- [x] Define a strict Pydantic `ListeningIntent` schema and prompt contract.
- [x] Implement OpenRouter structured-output parsing with schema validation and malformed-response recovery.
- [x] Add direct Anthropic and OpenAI adapters, or leave clearly tested extension points if credentials are unavailable.
- [x] Implement LangGraph state models and graph compilation.
- [x] Implement `parse_intent` and deterministic intent-validation nodes.
- [x] Implement clarification flow for material ambiguity, such as an unspecified artist/version or conflicting years.
- [x] Implement a Spotify-only candidate retrieval node for direct artist/track/year constraints.
- [x] Implement a deterministic resolver/ranker stub sufficient for direct requests.
- [x] Implement queue-preview and explicit approval interrupt/resume flow.
- [x] Implement safe cancellation, edit, and regenerate graph exits.
- [x] Add graph tests for successful parsing, malformed output, ambiguity, approval, cancellation, and LLM timeouts.
- [x] Add canonical prompt fixtures, including Taylor Swift era requests and invalid/adversarial prompts.

**Phase gate:** direct natural-language requests produce an inspectable structured intent and never mutate Spotify playback before an approval interrupt resumes the graph.

### Phase 4 — Spotify-only personalization


**Goal:** make vague requests more personal than generic keyword search without external services.

- [x] Add SQLite schema and migrations for profile summaries, feedback, sessions, action history, and cache metadata.
- [x] Implement repositories with transaction boundaries and migration tests.
- [x] Request and ingest Spotify top tracks/artists for short, medium, and long time windows.
- [x] Request and ingest recently played tracks for repetition avoidance.
- [x] Add opt-in selection of user-owned/collaborative playlists as taste signals.
- [x] Build a compact local profile summary from Spotify signals; do not create a broad permanent catalog copy.
- [x] Implement feedback actions: like, dislike, more like this, less like this, too familiar, and too distracting.
- [x] Add feedback-derived local preference weights with clear precedence over inferred signals.
- [x] Implement candidate retrieval from profile seeds and constrained Spotify catalog search.
- [x] Implement hard filters for artist, year, explicit-content, availability, and valid URI resolution.
- [x] Implement deterministic score components for taste affinity, familiarity/discovery, recency, diversity, and request fit.
- [x] Implement artist/album deduplication, recently-played penalties, and queue-duration fitting.
- [x] Implement source/score explanations for each final track.
- [x] Add evaluation fixtures for “peaceful study,” familiar versus discovery, duration, explicit-content, and repetition behavior.
- [x] Add a local profile reset/export command.

**Phase gate:** vague requests produce a personalized, diverse, explainable queue using Spotify-only signals and pass the fixed personalization evaluation suite.

### Phase 5 — Last.fm enrichment


**Goal:** optionally improve discovery through Last.fm history, tags, and similarity links.

- [x] Add opt-in Last.fm username/API-key configuration and credential handling.
- [x] Implement a typed Last.fm client with timeout, error normalization, retry, and rate-limit behavior.
- [x] Implement top-track, top-artist, and recent-track import for the configured user.
- [x] Implement artist-similarity and track-similarity candidate expansion.
- [x] Implement artist/track tag retrieval and normalization into TuneTrail mood/activity tags.
- [x] Add cache expiry rules for Last.fm profile and similarity responses.
- [x] Add Last.fm provenance to candidates and queue explanations.
- [x] Resolve Last.fm candidates conservatively through Spotify before ranking.
- [x] Ensure missing, private, stale, or mismatched Last.fm history does not prevent Spotify-only operation.
- [x] Add mocked Last.fm tests for valid data, unavailable API, rate limiting, and non-resolvable candidates.
- [x] Compare Spotify-only and Last.fm-enhanced outputs with the fixed evaluation suite; record findings below this phase.

**Phase gate:** Last.fm is opt-in, failure-tolerant, and measurably increases valid candidate diversity without lowering Spotify-resolution accuracy.

### Phase 6 — MusicBrainz enrichment


**Goal:** improve metadata quality and cross-source matching without slowing interactive use.

- [x] Implement a typed MusicBrainz client with an application-specific User-Agent.
- [ ] Implement a global one-request-per-second client-side rate limiter and `503` recovery.
- [ ] Add canonical artist/recording/release lookup models.
- [ ] Implement ISRC-first lookup and link storage.
- [ ] Implement conservative normalized artist/title/duration fallback matching with confidence scores.
- [ ] Retrieve aliases, release dates, release types, and relevant genres/tags.
- [ ] Improve era/year validation and identify likely remaster/edition ambiguity.
- [x] Cache stable MusicBrainz metadata; keep enrichment outside the interactive hot path whenever possible.
- [ ] Add queue/explanation annotations when canonical metadata changes an interpretation.
- [ ] Add tests for aliases, duplicate recordings, remasters, ISRC matches, low-confidence matches, limiter behavior, and API failures.

**Phase gate:** era-based requests and external-source resolutions are more accurate, confidence-scored, rate-safe, and explainable.

### Phase 7 — ReccoBeats evaluation


**Goal:** make a measured decision on using ReccoBeats as an optional recommendation signal.

- [ ] Document the current ReccoBeats API contract, terms, known rate behavior, and Spotify-data provenance considerations.
- [ ] Add a feature flag and disabled-by-default ReccoBeats configuration.
- [ ] Implement a typed, timeout-bounded ReccoBeats client behind the candidate-source protocol.
- [ ] Implement seed-track recommendation retrieval and optional audio-feature mapping.
- [ ] Resolve every ReccoBeats candidate through Spotify; discard low-confidence or unavailable results.
- [ ] Add a source-specific cache policy that avoids broad/long-lived catalog caching.
- [ ] Measure latency, API-error rate, Spotify-match coverage, and candidate diversity on the evaluation suite.
- [ ] Compare queue quality against Spotify-only and Last.fm-enhanced baselines.
- [ ] Review Spotify-policy compatibility before enabling the source for normal use.
- [ ] Record a go/no-go decision and rationale below this phase.
- [ ] If approved, add explicit source provenance and an opt-out setting; otherwise remove the runtime dependency and retain evaluation notes.

**Phase gate:** a documented, evidence-based go/no-go decision exists. If enabled, ReccoBeats is optional, failure-tolerant, and never bypasses Spotify resolution or user approval.

### Phase 8 — Evaluation and polish


**Goal:** make the application trustworthy and maintainable for daily personal use.

- [ ] Define local-only evaluation metrics: approval rate, skips, feedback, source contribution, duplicate rate, resolution rate, and preview latency.
- [ ] Add privacy-preserving local metrics storage and reset/export controls.
- [ ] Create a fixed regression evaluation suite of representative requests and expected constraints.
- [ ] Add resilience tests for no device, expired/revoked token, missing scope, Spotify `429`, optional-source outage, invalid LLM output, and network timeout.
- [ ] Add TUI recovery actions for every expected failure state.
- [ ] Add source enable/disable controls and a clear source-status view.
- [ ] Add credential cleanup and complete-profile deletion commands.
- [ ] Write setup, Spotify developer-app, configuration, privacy, troubleshooting, and contribution documentation.
- [ ] Add packaging/install instructions and test the clean-machine installation path.
- [ ] Run an end-to-end manual acceptance pass and record results below this phase.

**Phase gate:** TuneTrail has passed the regression suite and end-to-end manual acceptance pass, recovers clearly from expected failures, and is documented for repeatable personal installation.

## Phase Notes and Decisions

Use this section for short, durable notes that help later agents understand completed work or intentional deviations.

### Phase 0

Implemented the package, `uv`-managed dependency lockfile, strict local quality tooling, typed configuration and XDG paths, secure file helpers, redacted JSON logging, domain models, protocols, and test factories. Local verification passes with `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest` (12 tests).

GitHub Actions CI is intentionally deferred so it can be added as a separate learning task. The CI checkbox and Phase 0 gate therefore remain open.

### Phase 1

Implemented Spotify Authorization Code with PKCE using a state-bound local loopback callback, owner-only token persistence, refresh-token rotation, revoked-session cleanup, and logout. The typed async Spotify client adds bearer authentication, safe GET retries for `429`/transient server errors, normalized user-facing errors, player-command serialization, and a state refresh after every player mutation.

The minimal Textual shell displays authorization, device, and now-playing state; supports device selection and player controls; and starts the browser-based sign-in flow. Track search and explicit URI queueing are available through the typed application client; their keyboard-first review workflow is intentionally deferred to Phase 2.

Local verification passes with `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest` (31 tests). Spotify API behavior was checked against the current OpenAPI schema during implementation; track search is capped at 10 results per request.

### Phase 2

Implemented explicit `search track|artist|album|playlist <query>` commands, typed catalog contexts, context-track retrieval, and visible current-queue retrieval. The Textual UI now provides command focus/history, Spotify attribution, catalog selection, device/current-track/progress status, a reviewable local queue preview, explicit play-now/append confirmation, cancellation that does not call a player endpoint, and compact action-status feedback.

Confirmed action outcomes are retained in owner-only `action-history.json` beneath TuneTrail's XDG state directory. They contain only timestamp, action kind, Spotify track URIs/count, selected device ID, and outcome; search text, full metadata, queue copies, and credentials are excluded. The temporary command history is in-memory only.

Playlist-context preview requires `playlist-read-private` and `playlist-read-collaborative`; users authorized before this phase must log out and sign in again. Missing permission, unavailable contexts, no active device, and failed/partial player actions surface an actionable TUI notice.

Local verification passes with `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest` (42 tests), including Textual search, selection, cancellation, confirmation, append, command-history, and error-state coverage.

### Phase 3

Implemented provider-neutral structured intent parsing with OpenRouter as the initial backend. The project-root `.env` now supports `TUNETRAIL_OPENROUTER_API_KEY`, `TUNETRAIL_LLM_MODEL`, model temperature, bounded retries, and an opt-in data-collection setting; OpenRouter routing requests no data collection by default. Anthropic/OpenAI configuration has a tested, actionable extension-point error until direct adapters are added.

The in-memory LangGraph request flow parses intent, validates that it is a clear direct artist/track/year request, retrieves Spotify candidates, applies deterministic hard filters and ranking, and builds an inspectable queue preview. It pauses with a mandatory approval interrupt before the only graph node capable of calling Spotify player APIs. Approval resumes that node; cancellation and edit exit without mutation, while regeneration retrieves/ranks again and interrupts again. Graph state and raw requests are held only for the active TUI session.

Natural-language input now shares the command field with explicit `search ...` commands. It displays the parsed intent and provides confirm, cancel, edit, and regenerate controls. Phase 3 intentionally defers mood/activity, duration, familiarity, and discovery requests to Phase 4 rather than falling back to generic playlist search. The LLM receives only the request and schema prompt; it cannot produce a final URI, rank results, access profile data, or mutate playback.

Local verification passes with `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest`, covering structured parser recovery/timeouts, direct retrieval/filtering, approval/cancellation/edit/regenerate graph paths, TUI handoff, and canonical Taylor Swift/adversarial prompt fixtures.

### Phase 4

#### SQLite persistence design

Phase 4 uses the existing XDG-aware `AppPaths.database_file` location
(`~/.local/share/tunetrail/tunetrail.sqlite3` by default) for application-owned
personalization data. It adds a small migration runner with a
`schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)` table.
Each migration runs in a transaction and records its version only after the schema
change succeeds.

The initial Phase 4 schema is deliberately compact:

```text
profile_summary
  id INTEGER PRIMARY KEY CHECK (id = 1)
  favorite_artists JSON text array
  favorite_track_uris JSON text array
  recent_track_uris JSON text array
  summary text | null
  updated_at timestamp

feedback
  id INTEGER PRIMARY KEY
  spotify_uri text
  kind like | dislike | more_like_this | less_like_this | too_familiar | too_distracting
  note text | null
  created_at timestamp

playlist_signal
  spotify_playlist_id PRIMARY KEY
  display_name text
  owner_id text | null
  selected_at timestamp
  last_ingested_at timestamp | null

listening_session
  id INTEGER PRIMARY KEY
  request_hash text | null
  action text
  device_id text | null
  outcome succeeded | failed
  created_at timestamp

session_track
  session_id REFERENCES listening_session ON DELETE CASCADE
  position integer
  spotify_uri text
  score_json text | null
  PRIMARY KEY (session_id, position)
  UNIQUE (session_id, spotify_uri)

cache_metadata
  cache_key PRIMARY KEY
  refreshed_at timestamp
  expires_at timestamp
```

`feedback(spotify_uri, created_at DESC)` and `session_track(spotify_uri)` are
indexed for preference lookup and repetition penalties. Profile-refresh writes
the summary and cache metadata in one transaction; a confirmed or failed Spotify
action writes its session and all session tracks atomically. Feedback writes are
short append-only transactions. Profile reset removes only TuneTrail-owned rows
in one transaction and does not log the user out.

The database must not store OAuth tokens, API keys, raw LLM requests, raw Spotify
responses, permanent playlist copies, or a broad catalog export. Playlist opt-in
stores only a playlist reference; profile refresh derives bounded signals and
discards full playlist contents. Session request hashes are optional,
privacy-preserving digests rather than raw request text. The Phase 2
`action-history.json` remains readable; newly confirmed actions use the SQLite
session tables, with no automatic historical import unless preserving existing
local history becomes necessary.

Implemented the private SQLite store with transactional migrations, compact
profile/feedback/playlist/session/cache tables, private profile export, and local
profile reset. Profile refresh combines Spotify top artists/tracks across short,
medium, and long windows with recently played tracks and explicitly selected
owned/collaborative playlist references. Full Spotify endpoint and playlist
payloads are used transiently and are not persisted.

Natural-language graph requests now load the compact profile, feedback, and
URI-only generated-session history before retrieving Spotify candidates. Vague
requests use constrained searches derived from profile artist seeds; they do not
fall back to public playlist search. Deterministic ranking enforces hard
constraints and scores request fit, taste affinity, familiarity/discovery,
feedback, repetition, source confidence, and diversity. Spotify no longer offers
reliable current mood/audio-feature data, so the unavailable mood/activity score
is explicitly zero and explanations state that limitation rather than inventing
audio analysis.

The TUI offers profile refresh, playlist-signal opt-in, reset/export, and all six
feedback actions for the current track. All natural-language and explicit catalog
queue mutations still require approval; only approved actions create URI-only
SQLite sessions. Fixed local evaluation fixtures cover peaceful-study profile
behavior, familiar/discovery selection, duration, explicit-content, and
repetition constraints.

### Phase 5

Last.fm is enabled only when both API key and username are configured. Public
reads require no Last.fm session. Profile data is cached locally with a
username-hashed key for six hours; bounded normalized similarity results expire
after 24 hours. Profile reset removes the cache. Top and recent tracks and top
artists seed discovery; community tags contribute transparent mood/activity
evidence, not inferred audio features. Last.fm candidates must resolve to Spotify
using exact normalized artist/title and compatible duration when available.
Unexpected optional-source failures fall back to Spotify before the mandatory
approval interrupt. Long rate-limit waits cause fallback rather than early retry.

The fixed synthetic peaceful-study comparison increased candidates and distinct
artists from 2 to 4, accepted 2 of 3 external suggestions, and correctly matched
both accepted suggestions (2/2 labelled precision); it rejected an ambiguous live
edition. This small constructed fixture uses one Spotify baseline case and does not
measure real-world resolution accuracy, ranking quality, service availability, or
representative listener diversity. The Phase 5 gate **remains open** until broader,
independently labelled and preferably live comparisons establish the stated
diversity and accuracy requirements. Live acceptance also requires credentials, a
reauthorized Spotify session, and an active Premium device; it has not been run.
Local verification passes `uv run ruff format --check .`, `uv run ruff check .`,
`uv run pyright`, and `uv run pytest` (108 tests).

Additional Phase 5 evaluation uses ten hand-authored synthetic resolution cases:
five accepted matches were labelled correct, no accepted matches were labelled
incorrect, and no labelled available matches were missed. These labels were not
independently verified against real recordings; 5/5 precision is a regression
result, not an estimate of catalog accuracy. The resolver now rejects equally
supported distinct Spotify URIs rather than arbitrarily selecting one; duplicate
listings of the same URI remain acceptable.

Three synthetic graph comparisons exercise peaceful/study, discovery, and
familiar requests with different profile preferences. Each preview increased
from one to two distinct tracks and artists after enrichment. The comparisons
also check merging a duplicate URI, rejecting a mismatched edition, excluding
explicit or incomplete Spotify results, and cancellation without player or
session mutations. They use a fixture enrichment provider and mocked Spotify
search results, not a live Last.fm profile. They do not establish a representative
gain or compare independently labelled Spotify-only and enriched accuracy. The
Phase 5 gate therefore remains open; independently reviewed real-world cases and
comparative evidence are still needed. No live acceptance was performed.

Latest local verification: formatting, lint, and Pyright pass; `uv run pytest -q`
passed 113 tests on a sequential rerun. An earlier run concurrent with the other
checks failed once in an existing Textual TUI test when its playback-progress
timer queried a removed widget during teardown; the rerun passed. This intermittent
failure remains uninvestigated and is not evidence of a Phase 5 regression.

The previously observed teardown race was subsequently addressed: queued playback
progress timer callbacks now tolerate the now-playing widget having been removed.
A regression test invokes the callback after the Textual application unmounts.

### Phase 6

Phase 6 was started with the project owner's explicit override of the still-open
Phase 5 gate. The Phase 5 diversity and accuracy evidence remains outstanding and
must be revisited; this override does not mark that gate as satisfied.

Initial work adds a read-only MusicBrainz client and normalized artist, recording,
and release models. The User-Agent identifies the project repository. A shared
lock in the TuneTrail cache directory coordinates requests and retries across local
processes using that directory, spacing starts by at least one second. It cannot
coordinate unrelated clients or TuneTrail instances with different cache directories.
On `503`, bounded retries respect `Retry-After`; waits over five seconds fall back
instead of retrying early or stalling the optional source indefinitely.
Phase 6 acceptance remains outstanding.

An isolated matcher now prefers matching ISRCs and otherwise requires normalized
artist/title agreement and compatible duration, accepting known artist aliases.
It rejects conflicting ISRCs and equally supported distinct recording IDs; no
accuracy improvement is claimed.

The ISRC-first lookup flow now calls the matcher, falls back to name search only
after an empty ISRC result, and preserves source failures instead of treating them
as negative matches.

Normalized successful recording matches can now be cached for 30 days using
hashed matching-evidence keys, with a 1,000-entry cap and profile-reset cleanup.
Failures and ambiguous misses are not cached. Opt-in graph integration reads cached
matches before ranking and refreshes up to three cache misses in the background.
Successive refresh batches rotate through unresolved candidates so early misses
do not indefinitely starve later candidates.
A missing year is filled only for an explicitly complete MusicBrainz release list
with exactly one matching album title and a valid date, excluding common edition
markers. Responses without a release count are treated as incomplete. The queue explains this year
evidence and preserves Spotify identity and the approval boundary. This is a
conservative partial implementation, not a verified improvement in era accuracy;
release/edition handling and representative comparisons remain incomplete. Phase
6's gate remains open. Name searches now fetch aliases for all artists in the
bounded result page before deciding uniqueness; pages requiring more than five
artist lookups are rejected instead of making a decision on partial evidence.
Last.fm suggestions resolved to Spotify now retain the Spotify track's ISRC for
subsequent MusicBrainz lookups, instead of losing it or trusting a different source.

A five-case hand-authored synthetic era comparison measured safe-eligibility
agreement of 4/5 for Spotify-only and 5/5 with cached enrichment, with accepted
items increasing from 1 to 2 and no false admissions in this fixture. Ambiguous
editions were labelled unsafe rather than assigned an unknown real-world truth.
Labels and mocked metadata are not independently verified, and the fixture does
not establish live accuracy, release-list availability, or representative impact.
In particular, responses lacking an explicit release count cannot improve a year
under the current conservative rule. Phase 6's gate remains open pending broader
independent and preferably live comparisons. No live acceptance was performed.

### Phase 7

Preparatory documentation and a provisional no-go are recorded in
`docs/source-policy-review.md`. The current Spotify policy raises unresolved risks
for Spotify-derived profiling, benchmarking, and transfer of candidate metadata
to external services; this also affects existing Phase 4–6 flows, not just
ReccoBeats. The ReccoBeats documentation identifies Spotify-derived base metadata
and unpublished rate limits, while its rendered recommendation API reference does
not provide a complete schema. No runtime adapter, live calls, policy clearance,
or final evidence-based go/no-go has been completed. Phase 7's gate remains open.

### Phase 8

_No implementation notes yet._

## Testing Strategy

### Unit tests

- Intent validation and ambiguity handling.
- Candidate normalization and resolution confidence.
- Ranking, hard filters, diversity, and recent-play rules.
- Credential storage permissions and secret redaction.
- Configuration and LLM-provider factory behavior.

### Integration tests

- Spotify PKCE callback, refresh, and player error paths.
- LangGraph transitions and approval interrupt/resume.
- Mocked Last.fm, MusicBrainz, and ReccoBeats adapters.
- Optional-source failures that leave Spotify-only queues functional.

### Manual acceptance scenarios

- “Play Taylor Swift from 2008 to 2012.”
- “Play something peaceful for studying for 45 minutes.”
- “Give me familiar music with a few new discoveries.”
- “More like this but calmer.”
- No active Spotify device.
- LLM timeout or invalid structured response.
- Unavailable external candidate.
- Spotify rate limiting.
- No optional services configured.

## Proposed Package Layout

```text
src/tunetrail/
  app.py
  config.py
  tui/
  auth/
  spotify/
  llm/
  graph/
  recommendation/
    profile.py
    retrieval.py
    resolver.py
    ranking.py
    sources/
      spotify.py
      lastfm.py
      musicbrainz.py
      reccobeats.py
  persistence/
  domain/
  services/
tests/
```

## Near-Term Priorities

1. Build the Spotify PKCE/control foundation.
2. Make the non-AI TUI useful and safe.
3. Add LangGraph with structured intent parsing and confirmation.
4. Add Spotify-only personalization before adding any external source.
5. Introduce Last.fm, then MusicBrainz; evaluate ReccoBeats separately.
