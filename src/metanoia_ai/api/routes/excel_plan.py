"""HTTP endpoints for the structured Excel import."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from metanoia_ai.api.auth import get_current_user_id
from metanoia_ai.api.deps import get_excel_plan_reader, get_import_excel_plan
from metanoia_ai.application.use_cases.import_excel_plan import (
    ExcelPreview,
    ImportExcelPlan,
    InvalidExcelPlan,
    PartialExcelImport,
    SavedExcelPlan,
)
from metanoia_ai.application.use_cases.import_projects import ProjectLimitError
from metanoia_ai.application.use_cases.list_chat_history import UserNotFoundError
from metanoia_ai.domain.excel_plan import LIMIT, PlanRow, PlanRows, WorkbookError
from metanoia_ai.domain.project_import import MAX_IMPORT_PROJECTS

router = APIRouter(prefix="/projects/import/plan", tags=["Excel plan import"])
MAX_FILE_BYTES = 20 * 1024 * 1024

UserId = Annotated[UUID, Depends(get_current_user_id)]
Importer = Annotated[ImportExcelPlan, Depends(get_import_excel_plan)]


class ConfirmBody(BaseModel):
    request_id: UUID
    projects: list[PlanRow] = Field(min_length=1, max_length=MAX_IMPORT_PROJECTS)
    milestones: list[PlanRow] = Field(max_length=LIMIT)
    tasks: list[PlanRow] = Field(max_length=LIMIT)


@router.post("/preview")
async def preview(
    user_id: UserId,
    use_case: Importer,
    file: Annotated[UploadFile, File()],
    reader: Annotated[Callable[[bytes], PlanRows], Depends(get_excel_plan_reader)],
) -> ExcelPreview:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(422, detail={"code": "xlsx_required"})
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(422, detail={"code": "workbook_too_large"})
    try:
        rows = await run_in_threadpool(reader, data)
        return await use_case.preview(user_id, rows)
    except WorkbookError as error:
        code, _, source = str(error).partition(": ")
        raise HTTPException(422, detail={"code": code, "source": source}) from error
    except UserNotFoundError as error:
        raise HTTPException(404, detail="User not found") from error


@router.post("", status_code=201)
async def confirm(
    body: ConfirmBody, user_id: UserId, use_case: Importer
) -> SavedExcelPlan:
    try:
        return await use_case.confirm(
            user_id,
            body.request_id,
            PlanRows(body.projects, body.milestones, body.tasks),
        )
    except InvalidExcelPlan as error:
        raise HTTPException(
            422,
            detail={
                "code": "invalid_plan",
                "issues": [asdict(i) for i in error.issues],
            },
        ) from error
    except ProjectLimitError as error:
        raise HTTPException(
            409,
            detail={
                "code": "project_limit",
                "limit": error.limit,
                "used": error.used,
                "wanted": error.wanted,
            },
        ) from error
    except PartialExcelImport as error:
        raise HTTPException(409, detail={"code": "partial_import"}) from error
    except UserNotFoundError as error:
        raise HTTPException(404, detail="User not found") from error
