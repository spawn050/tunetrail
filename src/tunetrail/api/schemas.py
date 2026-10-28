"""Strict inputs for the local API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tunetrail.domain.models import ListeningIntent, PreferenceSubject
from tunetrail.recommendation.explicit_profile import FeedbackResponse
from tunetrail.recommendation.independent_discovery import SeedKind


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatInput(Input):
    message: str = Field(min_length=1, max_length=4000)


class PreferenceInput(Input):
    subject: PreferenceSubject
    name: str = Field(min_length=1, max_length=200)
    artist: str | None = Field(default=None, min_length=1, max_length=200)
    musicbrainz_id: UUID | None = None
    strength: int = Field(ge=-2, le=2, strict=True)


class FeedbackInput(Input):
    subject: PreferenceSubject
    name: str = Field(min_length=1, max_length=200)
    artist: str | None = Field(default=None, min_length=1, max_length=200)
    musicbrainz_id: UUID | None = None
    response: FeedbackResponse
    source: Literal["direct", "tile", "survey"] = "direct"


class SeedInput(Input):
    kind: SeedKind
    musicbrainz_id: UUID


class DiscoveryInput(Input):
    seeds: list[SeedInput] = Field(min_length=1, max_length=5)
    limit: int = Field(default=10, ge=1, le=20, strict=True)
    independently_obtained: Literal[True]
    enrich: bool = False


class PreviewInput(Input):
    discovery_id: str = Field(min_length=1, max_length=100)
    intent: ListeningIntent
    device_id: str | None = Field(default=None, min_length=1, max_length=200)


class ProposalInput(Input):
    proposal_id: str = Field(min_length=1, max_length=100)


class ApprovalInput(ProposalInput):
    explicitly_approve: Literal[True]
