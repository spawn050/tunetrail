"""LangGraph workflow that never mutates Spotify before an approval interrupt."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, Protocol, TypedDict, cast
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from tunetrail.domain.models import (
    ActionHistoryEntry,
    ActionOutcome,
    Candidate,
    Feedback,
    ListeningIntent,
    PlaybackAction,
    ProfileSummary,
    QueuePreview,
    ResolvedTrack,
)
from tunetrail.domain.protocols import (
    CandidateSourceProvider,
    IntentParser,
    PersonalizationStore,
    SpotifyGateway,
)
from tunetrail.graph.models import ApprovalDecision, RequestGraphResult, RequestOutcome
from tunetrail.llm.intent_parser import IntentParseError
from tunetrail.recommendation.ranking import (
    PersonalizationClarification,
    build_personalized_queue_preview,
    rank_personalized_candidates,
    validate_personalized_intent,
)
from tunetrail.recommendation.retrieval import SpotifyProfileCandidateRetriever


class RequestState(TypedDict, total=False):
    """Ephemeral state for a single LangGraph natural-language request."""

    request: str
    device_id: str | None
    intent: ListeningIntent
    profile: ProfileSummary | None
    feedback: list[Feedback]
    generated_uris: list[str]
    candidates: list[Candidate]
    tracks: list[ResolvedTrack]
    preview: QueuePreview
    clarification: str
    decision: str
    outcome: RequestOutcome
    error: str


class CompiledRequestGraph(Protocol):
    """The small compiled-LangGraph surface used by the request service."""

    async def ainvoke(self, input: object, *, config: object) -> Mapping[str, object]: ...


class NaturalLanguageRequestGraph:
    """Execute a request graph using only an active in-memory TUI session."""

    def __init__(
        self,
        *,
        parser: IntentParser,
        spotify: SpotifyGateway,
        store: PersonalizationStore | None = None,
        lastfm_enrichment: CandidateSourceProvider | None = None,
    ) -> None:
        self._parser = parser
        self._spotify = spotify
        self._graph: CompiledRequestGraph = _build_graph(parser, spotify, store, lastfm_enrichment)
        self._thread_id: str | None = None

    async def start(self, request: str, *, device_id: str | None = None) -> RequestGraphResult:
        """Run through preview construction and stop at approval or clarification."""
        self._thread_id = uuid4().hex
        state = await self._graph.ainvoke(
            {"request": request, "device_id": device_id}, config=self._config()
        )
        return _result_from_state(state)

    async def resume(self, decision: ApprovalDecision) -> RequestGraphResult:
        """Resume the current preview only with an explicit supported decision."""
        if self._thread_id is None:
            raise ValueError("There is no natural-language request awaiting a decision.")
        state = await self._graph.ainvoke(Command(resume=decision.value), config=self._config())
        return _result_from_state(state)

    def _config(self) -> dict[str, Mapping[str, str]]:
        """Create the LangGraph checkpointer configuration for this transient session."""
        if self._thread_id is None:
            raise AssertionError("a request graph thread must exist before invocation")
        return {"configurable": {"thread_id": self._thread_id}}


def _build_graph(
    parser: IntentParser,
    spotify: SpotifyGateway,
    store: PersonalizationStore | None,
    lastfm_enrichment: CandidateSourceProvider | None,
):  # type: ignore[no-untyped-def]
    """Compile the fixed graph; all player mutation is downstream of `interrupt`."""
    graph = StateGraph(RequestState)

    async def parse_intent(state: RequestState) -> RequestState:
        """Use the LLM only to produce a validated intent schema."""
        try:
            return {"intent": await parser.parse(_request(state))}
        except IntentParseError as error:
            return {
                "clarification": str(error),
                "outcome": RequestOutcome.CLARIFICATION_NEEDED,
            }

    async def load_profile_and_session(state: RequestState) -> RequestState:
        """Load compact local preference signals; raw Spotify history stays outside graph state."""
        del state
        if store is None:
            return {"profile": None, "feedback": [], "generated_uris": []}
        profile, feedback, generated_uris = await asyncio.gather(
            store.get(), store.recent(), store.recent_track_uris()
        )
        return {
            "profile": profile,
            "feedback": list(feedback),
            "generated_uris": list(generated_uris),
        }

    def validate_intent(state: RequestState) -> RequestState:
        """Keep unsupported or vague requests out of Spotify retrieval."""
        try:
            validate_personalized_intent(_intent(state), _profile(state))
        except PersonalizationClarification as error:
            return {
                "clarification": str(error),
                "outcome": RequestOutcome.CLARIFICATION_NEEDED,
            }
        return {}

    async def retrieve_spotify(state: RequestState) -> RequestState:
        """Retrieve Spotify candidates from deterministic field-filtered query text."""
        intent = _intent(state)
        try:
            candidates = await SpotifyProfileCandidateRetriever(spotify).find(
                intent, _profile(state)
            )
        except PersonalizationClarification as error:
            return {
                "clarification": str(error),
                "outcome": RequestOutcome.CLARIFICATION_NEEDED,
            }
        if lastfm_enrichment is None:
            return {"candidates": candidates}
        enriched: Sequence[Candidate]
        try:
            enriched = await lastfm_enrichment.find(intent, _profile(state))
        except Exception:
            # Optional discovery must never block a valid Spotify-only preview.
            enriched = []
        return {"candidates": _merge_candidates(candidates, enriched)}

    def resolve_and_rank(state: RequestState) -> RequestState:
        """Resolve Spotify candidates and preserve transparent constraint scores."""
        try:
            intent = _intent(state)
            tracks = rank_personalized_candidates(
                _candidates(state),
                intent,
                profile=_profile(state),
                feedback=_feedback(state),
                generated_uris=_generated_uris(state),
            )
            preview = build_personalized_queue_preview(intent, tracks)
        except PersonalizationClarification as error:
            return {
                "clarification": str(error),
                "outcome": RequestOutcome.CLARIFICATION_NEEDED,
            }
        return {"tracks": tracks, "preview": preview}

    def await_approval(state: RequestState) -> RequestState:
        """Suspend before all playback mutations and validate the eventual resume value."""
        decision = interrupt(
            {
                "kind": "queue_preview",
                "preview": _preview(state).model_dump(mode="json"),
                "allowed_decisions": [decision.value for decision in ApprovalDecision],
            }
        )
        try:
            return {"decision": ApprovalDecision(decision).value}
        except ValueError:
            return {"decision": ApprovalDecision.CANCEL.value}

    async def execute_spotify_action(state: RequestState) -> RequestState:
        """Mutate Spotify only on the graph's explicit approved route."""
        preview = _preview(state)
        uris = [item.track.spotify_uri for item in preview.items]
        try:
            if preview.intent.action is PlaybackAction.PLAY_NOW:
                await spotify.start_playback(uris, device_id=_device_id(state))
            else:
                for uri in uris:
                    await spotify.add_to_queue(uri, device_id=_device_id(state))
        except (
            Exception
        ) as error:  # Spotify gateway errors are returned to the TUI without tracebacks.
            await _record_session(store, preview, _device_id(state), ActionOutcome.FAILED)
            return {"error": str(error), "outcome": RequestOutcome.FAILED}
        await _record_session(store, preview, _device_id(state), ActionOutcome.SUCCEEDED)
        return {"outcome": RequestOutcome.COMPLETED}

    def cancelled(state: RequestState) -> RequestState:
        """End safely after an explicit cancellation."""
        del state
        return {"outcome": RequestOutcome.CANCELLED}

    def edit_requested(state: RequestState) -> RequestState:
        """End safely so the caller can place the request back in its input field."""
        del state
        return {"outcome": RequestOutcome.EDIT_REQUESTED}

    graph.add_node("parse_intent", parse_intent)
    graph.add_node("load_profile_and_session", load_profile_and_session)
    graph.add_node("validate_intent", validate_intent)
    graph.add_node("retrieve_spotify", retrieve_spotify)
    graph.add_node("resolve_and_rank", resolve_and_rank)
    graph.add_node("await_approval", await_approval)
    graph.add_node("execute_spotify_action", execute_spotify_action)
    graph.add_node("cancelled", cancelled)
    graph.add_node("edit_requested", edit_requested)
    graph.add_edge(START, "parse_intent")
    graph.add_conditional_edges(
        "parse_intent", _after_validation, {"end": END, "next": "load_profile_and_session"}
    )
    graph.add_edge("load_profile_and_session", "validate_intent")
    graph.add_conditional_edges(
        "validate_intent", _after_validation, {"end": END, "next": "retrieve_spotify"}
    )
    graph.add_edge("retrieve_spotify", "resolve_and_rank")
    graph.add_conditional_edges(
        "resolve_and_rank", _after_validation, {"end": END, "next": "await_approval"}
    )
    graph.add_conditional_edges(
        "await_approval",
        _after_decision,
        {
            "approve": "execute_spotify_action",
            "cancel": "cancelled",
            "edit": "edit_requested",
            "regenerate": "retrieve_spotify",
        },
    )
    graph.add_edge("execute_spotify_action", END)
    graph.add_edge("cancelled", END)
    graph.add_edge("edit_requested", END)
    return cast(CompiledRequestGraph, graph.compile(checkpointer=MemorySaver()))


def _after_validation(state: RequestState) -> Literal["end", "next"]:
    """Finish immediately when parsing, validation, or retrieval needs clarification."""
    return "end" if state.get("outcome") is RequestOutcome.CLARIFICATION_NEEDED else "next"


def _after_decision(state: RequestState) -> Literal["approve", "cancel", "edit", "regenerate"]:
    """Route only prevalidated approval values after the interrupt resumes."""
    decision = state.get("decision")
    if not isinstance(decision, str):
        return "cancel"
    return cast(Literal["approve", "cancel", "edit", "regenerate"], decision)


def _result_from_state(state: Mapping[str, object]) -> RequestGraphResult:
    """Translate raw graph state to a small, TUI-safe result model."""
    preview = state.get("preview")
    if "__interrupt__" in state and isinstance(preview, QueuePreview):
        return RequestGraphResult(outcome=RequestOutcome.PREVIEW_READY, preview=preview)
    outcome = state.get("outcome")
    if isinstance(outcome, RequestOutcome):
        return RequestGraphResult(
            outcome=outcome,
            preview=preview if isinstance(preview, QueuePreview) else None,
            clarification=_optional_text(state.get("clarification")),
            error=_optional_text(state.get("error")),
        )
    return RequestGraphResult(
        outcome=RequestOutcome.FAILED,
        error="TuneTrail could not complete that natural-language request.",
    )


def _request(state: Mapping[str, object]) -> str:
    """Return the original in-memory request after confirming graph state integrity."""
    request = state.get("request")
    if not isinstance(request, str):
        raise AssertionError("request graph state did not contain a request")
    return request


def _intent(state: Mapping[str, object]) -> ListeningIntent:
    """Return the validated intent committed by the parser node."""
    intent = state.get("intent")
    if not isinstance(intent, ListeningIntent):
        raise AssertionError("request graph state did not contain a validated intent")
    return intent


def _profile(state: Mapping[str, object]) -> ProfileSummary | None:
    """Return the optional compact profile loaded for this active request session."""
    profile = state.get("profile")
    if profile is None or isinstance(profile, ProfileSummary):
        return profile
    raise AssertionError("request graph state contained an invalid profile")


def _feedback(state: Mapping[str, object]) -> list[Feedback]:
    """Return explicit feedback loaded before deterministic ranking."""
    feedback = state.get("feedback")
    if not isinstance(feedback, list) or not all(isinstance(item, Feedback) for item in feedback):
        raise AssertionError("request graph state did not contain feedback")
    return cast(list[Feedback], feedback)


def _generated_uris(state: Mapping[str, object]) -> list[str]:
    """Return URI-only generated-session history for repetition penalties."""
    uris = state.get("generated_uris")
    if not isinstance(uris, list) or not all(isinstance(item, str) for item in uris):
        raise AssertionError("request graph state did not contain generated track URIs")
    return cast(list[str], uris)


def _candidates(state: Mapping[str, object]) -> list[Candidate]:
    """Return Spotify candidates committed by the retrieval node."""
    candidates = state.get("candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, Candidate) for item in candidates
    ):
        raise AssertionError("request graph state did not contain Spotify candidates")
    return cast(list[Candidate], candidates)


def _preview(state: Mapping[str, object]) -> QueuePreview:
    """Return the preview committed before the mandatory approval interrupt."""
    preview = state.get("preview")
    if not isinstance(preview, QueuePreview):
        raise AssertionError("request graph state did not contain a queue preview")
    return preview


def _device_id(state: Mapping[str, object]) -> str | None:
    """Narrow the optional selected device to the Spotify gateway contract."""
    value = state.get("device_id")
    return value if isinstance(value, str) else None


def _optional_text(value: object) -> str | None:
    """Narrow optional graph text for the public result model."""
    return value if isinstance(value, str) else None


def _merge_candidates(
    spotify_candidates: list[Candidate], external_candidates: Sequence[Candidate]
) -> list[Candidate]:
    """Deduplicate matching URI candidates while retaining Last.fm tag/provenance evidence."""
    merged: dict[str, Candidate] = {}
    for candidate in [*spotify_candidates, *external_candidates]:
        uri = candidate.provenance.get("spotify_uri")
        key = uri if uri is not None else f"{candidate.source.value}:{candidate.source_id}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = candidate
            continue
        merged[key] = existing.model_copy(
            update={
                "tags": list(dict.fromkeys([*existing.tags, *candidate.tags])),
                "provenance": {**existing.provenance, **candidate.provenance},
            }
        )
    return list(merged.values())


async def _record_session(
    store: PersonalizationStore | None,
    preview: QueuePreview,
    device_id: str | None,
    outcome: ActionOutcome,
) -> None:
    """Persist URI-only session signals after the approved Spotify path has completed."""
    if store is None:
        return
    entry = ActionHistoryEntry(
        action=preview.intent.action,
        spotify_uris=[item.track.spotify_uri for item in preview.items],
        device_id=device_id,
        outcome=outcome,
        created_at=datetime.now(UTC),
    )
    try:
        await store.record_action(
            entry,
            score_json=[
                json.dumps(item.track.score_breakdown, separators=(",", ":"))
                for item in preview.items
            ],
        )
    except Exception:
        # Spotify's completed action remains true if nonessential session persistence fails.
        return
