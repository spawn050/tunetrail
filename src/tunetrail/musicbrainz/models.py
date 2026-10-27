"""Small normalized MusicBrainz entities, not raw API responses."""

from pydantic import BaseModel, ConfigDict, Field


class Artist(BaseModel):
    """A canonical artist and known alternative names."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list[str])


class Release(BaseModel):
    """A release and its date and release-group type when available."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    title: str
    date: str | None = None
    release_type: str | None = None


class Recording(BaseModel):
    """Bounded identity and edition evidence for a MusicBrainz recording."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    id: str
    title: str
    artists: list[Artist] = Field(default_factory=list[Artist])
    duration_ms: int | None = None
    isrcs: list[str] = Field(default_factory=list[str])
    releases: list[Release] = Field(default_factory=list[Release])
    releases_complete: bool = False
    tags: list[str] = Field(default_factory=list[str])
