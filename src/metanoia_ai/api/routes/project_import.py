"""FR-08c: one file → several projects.

/projects/import/preview: the AI splits the file (1 AI call, nothing created).
/projects/import: create the projects the user kept, each with a project chat
whose first message is its part of the file (no AI call).
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_import_projects, get_preview_project_import
from metanoia_ai.api.routes.chat import conflict, not_found
from metanoia_ai.application.use_cases.import_projects import (
    ImportProjects,
    ProjectLimitError,
)
from metanoia_ai.application.use_cases.list_chat_history import UserNotFoundError
from metanoia_ai.application.use_cases.preview_project_import import (
    PreviewProjectImport,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    PlanFeatureError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain.document_text import DocumentError, DocumentErrorCode
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_import import ImportInput
from metanoia_ai.domain.task_breakdown import BadDraftError

router = APIRouter(prefix="/projects/import", tags=["project import"])


class ImportedProjectBody(BaseModel):
    name: str
    description: str | None = None
    deadline: date | None = None
    source: str | None = None  # where in the file, shown to the user
    brief: str  # this project's part of the file


class PreviewResponse(BaseModel):
    file_name: str
    projects: list[ImportedProjectBody]
    warnings: list[str]
    project_limit: int | None  # None: no limit
    projects_left: int | None  # how many more the plan allows; None: no limit


class ImportRequest(BaseModel):
    file_name: str
    language: Language = Language.VI
    projects: list[ImportedProjectBody]


class CreatedProjectResponse(BaseModel):
    project_id: UUID
    conversation_id: UUID  # open it with GET /chat/conversations/{id}
    name: str


class ImportResponse(BaseModel):
    projects: list[CreatedProjectResponse]


def unprocessable(detail: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
    )


@router.post("/preview")
async def preview(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[PreviewProjectImport, Depends(get_preview_project_import)],
    file: Annotated[UploadFile, File()],
    language: Annotated[Language, Form()] = Language.VI,
) -> PreviewResponse:
    """Split the file into projects. 1 AI call; nothing is created."""
    name = file.filename or "file"
    data = await file.read(use_case.limits.max_file_bytes + 1)
    if len(data) > use_case.limits.max_file_bytes:
        raise unprocessable({"code": str(DocumentErrorCode.TOO_LARGE), "file": name})
    try:
        result = await use_case.execute(user_id, name, data, language)
    except DocumentError as error:
        raise unprocessable({"code": str(error.code), "file": error.name}) from error
    except UserNotFoundError as error:
        raise not_found("User") from error
    except PlanFeatureError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "plan_feature_locked", "feature": error.feature},
        ) from error
    except NoSubscriptionError as error:
        raise conflict("no_active_plan") from error
    except QuotaExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="AI quota exceeded"
        ) from error
    except AIUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI could not read the file. Please try again.",
        ) from error
    return PreviewResponse(
        file_name=result.file_name,
        projects=[
            ImportedProjectBody(
                name=p.name,
                description=p.description,
                deadline=p.deadline,
                source=p.source,
                brief=p.brief,
            )
            for p in result.projects
        ],
        warnings=list(result.warnings),
        project_limit=result.project_limit,
        projects_left=result.projects_left,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: ImportRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    use_case: Annotated[ImportProjects, Depends(get_import_projects)],
) -> ImportResponse:
    """Create the projects (all or none) and their chats. No AI call."""
    items = [
        ImportInput(p.name, p.brief, p.description, p.deadline, p.source)
        for p in body.projects
    ]
    try:
        created = await use_case.execute(user_id, body.file_name, items, body.language)
    except BadDraftError as error:
        raise unprocessable(str(error)) from error
    except UserNotFoundError as error:
        raise not_found("User") from error
    except ProjectLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "project_limit",
                "limit": error.limit,
                "used": error.used,
                "wanted": error.wanted,
            },
        ) from error
    return ImportResponse(
        projects=[
            CreatedProjectResponse(
                project_id=c.project_id, conversation_id=c.conversation_id, name=c.name
            )
            for c in created
        ]
    )
