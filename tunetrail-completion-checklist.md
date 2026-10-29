# TuneTrail completion and manual acceptance checklist

Phase 7 is deferred. Archived TUI, Spotify-history personalization, Last.fm, and other superseded checklist items are not automatically part of the redesign. Complete one phase per implementation pass, with small commits and evidence for completed items.

## 1. Establish the baseline

- [ ] Confirm the working tree and current roadmap status.
- [ ] Run the existing offline Python and frontend checks.
- [ ] Run the opt-in headless browser regression.
- [ ] Record failures before changing behavior.
- [ ] Preserve existing databases, credentials, and exports; ask before deleting, moving, or replacing them.

## 2. Complete Phase 0: CI

Deferred by the owner until closeout or future development.

- [ ] Add GitHub Actions or equivalent CI for formatting, linting, type checks, and Python tests.
- [ ] Include frontend tests and build as appropriate.
- [ ] Ensure CI uses synthetic data and needs neither credentials nor live provider access.
- [ ] Verify the workflow where possible; distinguish local checks from an actual CI run.
- [ ] Update the Phase 0 roadmap checklist after its stated verification passes.

## 3. Test Phase 6 clean-machine installation

- [ ] Choose a separate clean machine or fresh VM and record its OS and versions.
- [ ] Follow documented checkout, dependency installation, frontend build, and startup instructions.
- [ ] Verify the app opens locally and the backend binds to loopback.
- [ ] Verify credentials remain server-side. Real credentials are not required for this installation check.
- [ ] Fix and retest installation or documentation issues.
- [ ] Record the environment and results; update Phase 6 only after the check passes.

## 4. Prepare manual-test prerequisites and credentials

- [ ] Configure the OpenRouter API key and model in the local `.env` file. Hosted chat leaves the machine.
- [ ] Configure the Spotify client ID and check whether a usable local token exists, without exposing its contents.
- [ ] Use the React Connect Spotify flow if a token is missing or needs replacement; replacement requires the owner's explicit confirmation. Live authorization remains unverified.
- [ ] Never paste API keys or tokens into chat.
- [ ] Obtain a small set of MusicBrainz artist or recording IDs independently, not by translating Spotify identifiers or metadata.
- [ ] Decide whether to test hosted chat and optional ReccoBeats enrichment. ReccoBeats is provisional and off by default.
- [ ] Prepare test preferences and expected outcomes; avoid using valuable existing profile data for reset tests.
- [ ] Identify usability blockers and agree on scope before implementing fixes.

## 5. Complete Phase 5 offline evidence

- [ ] Re-run synthetic evaluation, boundary, and approval tests after fixes.
- [ ] Describe results as fixture regressions, not real-world accuracy.
- [ ] Independently review evaluation cases. **Deferred at the owner's request**; manual acceptance does not substitute for independent review.

## 6. Authorize and perform read-only manual testing

Before live calls, separately agree on providers, requests, test data, and scope. Never send Spotify IDs, URIs, or Spotify-derived metadata to MusicBrainz or ReccoBeats, including during translation.

- [ ] Obtain explicit authorization for the specified live read-only evaluation.
- [ ] Start the app and verify connection and configuration-only source-status messaging.
- [ ] Check the configured LLM with a small test chat; inspect clear lasting preferences, temporary requests, ambiguity, and the resulting profile.
- [ ] Check Spotify authentication and access using an authorized read-only request, such as catalog search or device listing; do not expose secrets.
- [ ] Discover candidates using independent MusicBrainz IDs; inspect explanations and insufficient results.
- [ ] If included in the authorized scope, test optional ReccoBeats enrichment and observe missing features or failures.
- [ ] Review Spotify playback previews, including match details.
- [ ] Check that ambiguous matches and unsupported constraints do not become approvable proposals.
- [ ] Cancel a preview and verify no playback action is requested.
- [ ] Record outcomes and limitations separately from offline results.

## 7. Authorize and perform playback acceptance

Playback acceptance requires separate authorization, a Spotify Premium account, an active device, and explicit approval for every playback or queue mutation.

- [ ] Obtain separate authorization for playback acceptance and confirm an active device.
- [ ] Review a fresh play-now preview and explicitly approve that action.
- [ ] Verify the observed Spotify result.
- [ ] If desired, review and explicitly approve a separate queue-append proposal.
- [ ] Verify the queue result; each additional track requires its own preview and approval.
- [ ] After any failed request, inspect Spotify before retrying: it may have succeeded. Use a fresh preview and approval for any retry.
- [ ] Record results without treating a playback request as proof of listening.

## 8. Close out

- [ ] Fix acceptance defects and rerun relevant offline and authorized manual checks.
- [ ] Update the roadmap with actual evidence and remaining limitations.
- [ ] Leave independent review open unless the owner chooses to pursue it.
- [ ] Keep provider-policy questions open; successful tests do not establish clearance.
- [ ] Decide whether remaining open items are acceptable before revisiting Phase 7.

## Conversational backend follow-up

- [ ] Build and verify chat-first backend capabilities in small phases before the UI revamp.
- [ ] Chat Spotify login: offline-tested exact commands for connection, status and cancellation, with separate credential-replacement confirmation. Chat UI rendering is mocked-tested; live authorization remains unverified.
- [ ] Recent-play queries: structured bounded request, local filtering and chat rendering, no Spotify results sent to the LLM; offline tests only. Live access requires authorization and potentially explicit credential replacement for the new scope.
- [ ] Add remaining chat capabilities, including device selection, specific playback, independent discovery, general music questions and profile queries, with explicit approval for playback mutations.
- [ ] Basic chat-first UI implemented for manual testing; expand it as backend chat capabilities are added, and take feedback through chat instead of clickable controls.
- [ ] Natural-language Spotify connection and device listing/selection implemented with mocked tests; device transfer requires separate confirmation. Live read-only and playback/transfer acceptance remain separately unauthorized.
- [ ] Page-lifetime follow-up context: bounded user excerpts, extractive compression and clearing on Spotify results; mocked tests only. Context cannot authorize Spotify actions or save preferences, and general conversational answers remain unimplemented.
- [ ] Playback controls: bounded local recognition of clear pause/resume, next/previous, volume and shuffle requests, with exact single-use approvals and mocked tests. Wording coverage is limited; live playback acceptance remains separately unauthorized.
- [ ] Specific-song chat playback: bounded first-page Spotify search, numbered version selection and explicit single-use confirmation on a rechecked active device. Offline mocked verification only; live read-only search and playback acceptance remain separately unauthorized.
- [ ] Read-only chat recommendations: ask for an artist when needed, search independently supplied names on MusicBrainz, require numbered selection, and display bounded recordings without claiming similarity or Spotify availability. Offline mocked verification only; broader similarity discovery and separately authorized live evaluation remain open. No playback or queue mutation.
- [ ] Resolve provider-policy concerns separately; local processing is not policy clearance.
