# External-source policy review (preliminary)

Checked on 2026-10-27. This is an engineering risk assessment, not legal advice
or a determination that any particular use is permitted. The project owner should
review current terms and obtain qualified advice or clarification as appropriate
before relying on Spotify-derived profiling, comparisons, or external enrichment.

## Spotify policy risks

[Spotify Developer Policy](https://developer.spotify.com/policy), effective
2025-05-15, section III.9 restricts transferring data to another service except
for specified user-data or playlist-transfer purposes. Section III.13 prohibits
analyzing Spotify Content or the Spotify Service for purposes including derived
metrics, benchmarking, functionality, usage statistics, and user profiles.
Section III.14 restricts ingesting Spotify Content into ML/AI models. The policy
also requires proper attribution and links to applicable Spotify content.

The historical TuneTrail architecture derived local taste signals from Spotify
history and could send Spotify-candidate artist/title/ISRC metadata to MusicBrainz.
Its planned Last.fm discovery could use Spotify profile artist names as similarity
seeds, and its earlier ReccoBeats proposal could send Spotify identifiers. These flows and proposed
local quality metrics need policy review; a personal-use label or local storage
alone does not establish compliance. Do not assume the roadmap's earlier privacy
rules settle these questions. No live use or policy clearance is claimed here.

## ReccoBeats contract and provisional decision

- [Introduction](https://reccobeats.com/docs/documentation/introduction): public
  `https://api.reccobeats.com`, no key or authentication.
- [Recommendations](https://reccobeats.com/docs/apis/get-recommendation) and
  [resource IDs](https://reccobeats.com/docs/documentation/Resources/resource-id):
  `GET /v1/track/recommendation` with `seeds` and `size`; documented seed examples
  include Spotify IDs and ReccoBeats IDs. The rendered API page does not expose a
  complete parameter or response schema, so the contract needs confirmation.
- [Rate limiting](https://reccobeats.com/docs/documentation/rate-limiting): limits
  are internal and unpublished; `429` and `Retry-After` are documented.
- [Terms](https://reccobeats.com/docs/documentation/terms-of-service), updated
  2026-05-25: track metadata is aggregated from Spotify data; users are responsible
  for applicable third-party terms. Audio features and recommendation logic are
  described as ReccoBeats' own analysis, and the service is provided as-is.

**Historical provisional no-go for runtime integration:** the data-flow/policy questions were
unresolved, the public schema shown above is incomplete, and no representative
quality, error-rate, or latency measurements have been performed. This is not the
roadmap's final evidence-based go/no-go decision. At that point ReccoBeats was
unimplemented and disabled; no API calls or Spotify data transfers were made for that review.

### Phase 4S documentation correction (2026-10-28)

The previous review missed the dynamically rendered API specification. The public
[track search](https://reccobeats.com/docs/apis/search-tracks) documentation's
client-side specification describes `GET /v1/track/search`: required `searchText`
(track title, 3–1,000 characters), optional `artist` (3–1,000 characters), and
bounded `page` and `size`. Results contain track IDs, titles, artists, duration and
optional ISRC, as well as Spotify links that must not be forwarded. The
[per-track features](https://reccobeats.com/docs/apis/get-track-audio-features)
specification documents `GET /v1/track/{id}/audio-features`, requiring a ReccoBeats
ID and describing feature fields and scales. An independent title/artist search is
therefore possible, though catalog coverage and match reliability are not established.
Short titles and artist names cannot be queried with these documented parameters.
No live API tests were made.

The owner accepts the foundational Spotify-derived metadata tradeoff for this hobby
project; that decision is not policy clearance. The terms explicitly identify this
origin and place responsibility for third-party terms on users. The earlier no-go
record above is historical; the owner has approved provisional feature enrichment
with independent inputs, not policy clearance or live testing.
