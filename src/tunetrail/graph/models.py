"""Typed public outcomes for a single natural-language graph session."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from tunetrail.domain.models import QueuePreview


class ApprovalDecision(StrEnum):
    """The only decisions accepted after a queue preview interrupt."""

    APPROVE = "approve"
    CANCEL = "cancel"
    EDIT = "edit"
    REGENERATE = "regenerate"


class RequestOutcome(StrEnum):
    """A user-facing lifecycle point reached by the request graph."""

    PREVIEW_READY = "preview_ready"
    CLARIFICATION_NEEDED = "clarification_needed"
    CANCELLED = "cancelled"
    EDIT_REQUESTED = "edit_requested"
    COMPLETED = "completed"
    FAILED = "failed"


class RequestGraphResult(BaseModel):
    """A safe, inspectable graph result for the TUI to render."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: RequestOutcome
    preview: QueuePreview | None = None
    clarification: str | None = None
    error: str | None = None
