"""FR-08c step 1: read the file, the AI splits it into projects. Nothing is created.

1 AI call (charged only when it works). Free plan → 403, like the AI chat.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from metanoia_ai.application.import_writer import ImportWriter
from metanoia_ai.application.llm_chat import ChatQuota
from metanoia_ai.application.ports.project_import_repository import (
    ProjectImportRepository,
)
from metanoia_ai.application.ports.quota_repository import QuotaRepository
from metanoia_ai.application.use_cases.list_chat_history import UserNotFoundError
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.send_project_chat_message import (
    DocumentLimits,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain.document_text import cut_text, extract_text
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_import import ImportedProject, projects_left

DROPPED_DEADLINE_WARNING = {
    Language.VI: "Đã bỏ {count} hạn đã qua trong file; bạn đặt lại hạn nếu cần.",
    Language.EN: "Removed {count} past deadlines from the file; set new ones if needed.",
}
CUT_WARNING = {
    Language.VI: "File dài nên AI chỉ đọc phần đầu.",
    Language.EN: "The file is long, so the AI only read the first part.",
}


@dataclass(frozen=True)
class ImportPreview:
    file_name: str
    projects: tuple[ImportedProject, ...]
    warnings: tuple[str, ...]
    project_limit: int | None
    projects_left: int | None  # None = no limit


class PreviewProjectImport:
    def __init__(
        self,
        repo: ProjectImportRepository,
        quota_repo: QuotaRepository,
        writer: ImportWriter,
        *,
        limits: DocumentLimits,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.quota_repo = quota_repo
        self.quota = ChatQuota(quota_repo, now=now)
        self.writer = writer
        self.limits = limits
        self.now = now

    async def execute(
        self, user_id: UUID, file_name: str, data: bytes, language: Language
    ) -> ImportPreview:
        """DocumentError (422), PlanFeatureError (403), NoSubscriptionError /
        QuotaExceededError, AIUnavailableError (502)."""
        name = (file_name or "file")[:255]
        zone = await self.repo.time_zone(user_id)
        if zone is None:
            raise UserNotFoundError(str(user_id))
        subscription = await self.quota.check_paid(user_id, "chat")
        allowance = await self.repo.allowance(user_id)

        text = extract_text(
            data,
            name=name,
            path=name,
            max_bytes=self.limits.max_file_bytes,
            max_pdf_pages=self.limits.max_pdf_pages,
            enough_chars=self.limits.max_chars,
        )
        warnings: list[str] = []
        if len(text) > self.limits.max_chars:
            text = cut_text(text, self.limits.max_chars)
            warnings.append(CUT_WARNING[language])
        today = self.now().astimezone(zone).date()
        result = await self.writer.split(
            mask_contact(name, None), mask_contact(text, None), today, language
        )

        async with self.repo.transaction():
            charged = False
            if result.value is not None:
                charged = await self.quota_repo.try_charge(subscription)
            await self.quota.log(subscription, result.calls, charged)
        if result.value is None:
            raise AIUnavailableError(name)
        if not charged:
            raise QuotaExceededError("plan limit reached")

        if result.value.dropped_deadlines:
            warnings.append(
                DROPPED_DEADLINE_WARNING[language].format(
                    count=result.value.dropped_deadlines
                )
            )
        return ImportPreview(
            file_name=name,
            projects=result.value.projects,
            warnings=tuple(warnings),
            project_limit=allowance.limit,
            projects_left=projects_left(allowance.limit, allowance.used),
        )
