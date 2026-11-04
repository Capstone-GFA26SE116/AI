"""FR-22 endpoint: the voice assistant, phase 1 (read only). Mobile / Frontend → AI.

POST because of the body and because it uses one AI call; it changes no business
data (AC3): it only writes AIUsageLog and the AI call counter.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_assistant_turn
from metanoia_ai.application.use_cases.assistant_turn import (
    AssistantTurn,
    HistoryLine,
    Turn,
    TurnKind,
    TurnRequest,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.config import Settings, get_settings
from metanoia_ai.domain.assistant_text import Emotion
from metanoia_ai.domain.assistant_tools import ToolName
from metanoia_ai.domain.models import Language

router = APIRouter(prefix="/assistant", tags=["assistant"])


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1)


class TurnBody(BaseModel):
    text: str = Field(min_length=1)
    language: Language = Language.VI
    history: list[HistoryItem] = Field(default_factory=list)
    selected_id: UUID | None = None  # the option picked after a "clarify" turn


class OptionResponse(BaseModel):
    id: UUID
    title: str


class TurnResponse(BaseModel):
    kind: TurnKind
    tool: ToolName | None
    reply: str  # read aloud and shown; at most 60 words
    emotion: Emotion
    options: list[OptionResponse]  # only for "clarify", at most 3
    suggestions: list[str]  # for "unsupported" and smalltalk "help"


def _bad_body(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message
    )


def check_limits(body: TurnBody, settings: Settings) -> None:
    """Size limits from config (#10): text, number of turns, total history length."""
    if len(body.text.strip()) == 0:
        raise _bad_body("text is empty")
    if len(body.text) > settings.assistant_text_max_chars:
        raise _bad_body(
            f"text is longer than {settings.assistant_text_max_chars} characters"
        )
    if len(body.history) > settings.assistant_history_max_turns:
        raise _bad_body(
            f"history has more than {settings.assistant_history_max_turns} turns"
        )
    if sum(len(h.text) for h in body.history) > settings.assistant_history_max_chars:
        raise _bad_body(
            f"history is longer than {settings.assistant_history_max_chars} characters"
        )


def to_response(turn: Turn) -> TurnResponse:
    return TurnResponse(
        kind=turn.kind,
        tool=turn.tool,
        reply=turn.reply,
        emotion=turn.emotion,
        options=[OptionResponse(id=o.id, title=o.title) for o in turn.options],
        suggestions=list(turn.suggestions),
    )


@router.post("/turn")
async def post_turn(
    body: TurnBody,
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    settings: Annotated[Settings, Depends(get_settings)],
    use_case: Annotated[AssistantTurn, Depends(get_assistant_turn)],
) -> TurnResponse:
    """One spoken question → one reply. Uses 1 AI call when the LLM answers well."""
    check_limits(body, settings)
    turn_request = TurnRequest(
        user_id=user_id,
        text=body.text.strip(),
        language=body.language,
        history=tuple(HistoryLine(h.role, h.text) for h in body.history),
        auth_header=request.headers.get("Authorization", ""),  # passed on only
        selected_id=body.selected_id,
    )
    try:
        turn = await use_case.execute(turn_request)
    except NoSubscriptionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "no_active_plan"}
        ) from error
    except QuotaExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="AI quota exceeded"
        ) from error
    except UserNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from error
    except AIUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="AI is not available"
        ) from error
    return to_response(turn)
