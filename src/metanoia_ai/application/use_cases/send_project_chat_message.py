"""FR-08b-1: the user sends a message (usually a client brief) in a project chat;
the AI asks back or answers with a draft of milestones + tasks.

1 message = 1 AI call from the quota, charged only for a valid answer (#12).
The LLM call runs outside any transaction; everything is saved in one at the end.

FR-08b-2: a message may carry up to 3 documents (picked from the project's
library, only when the project allows it, or uploaded). Their text is read
before the LLM call; any document problem stops the message before it, so
nothing is charged (#19).
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.llm_chat import ChatQuota
from metanoia_ai.application.ports.document_source import (
    DocumentNotFoundError,
    DocumentSource,
)
from metanoia_ai.application.ports.project_chat_repository import (
    AttachmentSource,
    ChatAttachment,
    ChatProject,
    ChatRole,
    ConversationStatus,
    DocumentSourceType,
    ExistingWork,
    NewAttachment,
    ProjectChatMessage,
    ProjectChatRepository,
    ProjectConversation,
    ProjectDocument,
    SavedProjectDraft,
)
from metanoia_ai.application.ports.quota_repository import (
    QuotaRepository,
    Subscription,
)
from metanoia_ai.application.project_chat_writer import (
    DraftContext,
    ProjectChatLine,
    ProjectChatWriter,
    ProjectReply,
    PromptDocument,
    PromptDomain,
    PromptProject,
)
from metanoia_ai.application.reason_writer import LLMCall
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
    extra_percent,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    open_project,
)
from metanoia_ai.domain.document_text import (
    DocumentError,
    DocumentErrorCode,
    cut_text,
    extract_text,
    fit_to_budget,
    truncation_warning,
)
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import (
    project_numbers,
    reply_language,
    restore_client_in_notes,
)


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


class DocumentAccessOffError(Exception):
    """Library documents asked for, but the project does not allow it (AC1). → 409."""


# ChatMessage.Content when the user sends documents without a text (API, FR-08b-2).
DOCUMENTS_ONLY_TEXT = {
    Language.VI: "(Gửi tài liệu)",
    Language.EN: "(Sent documents)",
}


@dataclass(frozen=True)
class DocumentLimits:
    """Config for attached documents (CHAT_DOCUMENT_*); defaults as in config."""

    max_files: int = 3
    max_file_bytes: int = 20 * 1024 * 1024
    max_chars: int = 80_000
    max_pdf_pages: int = 100


DEFAULT_DOCUMENT_LIMITS = DocumentLimits()


@dataclass(frozen=True)
class UploadedFile:
    name: str  # as uploaded; gives the extension (#11)
    data: bytes


@dataclass(frozen=True)
class AttachedDocuments:
    """What a message carries besides its text."""

    document_ids: tuple[UUID, ...] = ()  # from the project's library
    uploads: tuple[UploadedFile, ...] = ()
    auth_header: str = ""  # the user's Authorization header, for the Backend (#18)

    @property
    def count(self) -> int:
        return len(self.document_ids) + len(self.uploads)


NO_DOCUMENTS = AttachedDocuments()


@dataclass(frozen=True)
class ProjectChatTurn:
    conversation_id: UUID
    user_message: ProjectChatMessage
    ai_message: ProjectChatMessage  # its `draft` holds the numbers
    time_zone: ZoneInfo  # the user's, for created_at in the answer
    attachments: tuple[ChatAttachment, ...] = ()  # of the user message


def pick_history(
    messages: Sequence[ProjectChatMessage], recent: int
) -> list[ProjectChatMessage]:
    """The first message (the brief) + the `recent` newest ones, oldest first (#18)."""
    if len(messages) <= recent + 1:
        return list(messages)
    return [messages[0], *messages[-recent:]]


def check_open(conversation: ProjectConversation) -> None:
    if conversation.status is not ConversationStatus.OPEN:
        raise ConversationAppliedError(str(conversation.conversation_id))


@dataclass(frozen=True)
class _Prepared:
    """Everything read before the LLM call."""

    project: ChatProject
    subscription: Subscription
    history: list[ProjectChatMessage]
    domains: dict[UUID, str]
    k_bias: dict[UUID, Decimal]
    work: ExistingWork
    library: list[ProjectDocument] = field(default_factory=list)  # asked for, checked
    earlier: list[ChatAttachment] = field(default_factory=list)  # of this chat


class SendProjectChatMessage:
    def __init__(
        self,
        repo: ProjectChatRepository,
        quota_repo: QuotaRepository,
        writer: ProjectChatWriter,
        *,
        initial_k: Decimal,
        history_limit: int,
        max_message_chars: int,
        max_prompt_chars: int,
        existing_titles_limit: int,
        daily_work_hours: float,
        now: Callable[[], datetime],
        document_source: DocumentSource | None = None,
        document_limits: DocumentLimits = DEFAULT_DOCUMENT_LIMITS,
    ) -> None:
        self.repo = repo
        self.quota_repo = quota_repo  # shares the repo's session
        self.writer = writer
        self.initial_k = initial_k
        self.history_limit = history_limit
        self.max_message_chars = max_message_chars
        self.max_prompt_chars = max_prompt_chars
        self.existing_titles_limit = existing_titles_limit
        self.daily_work_hours = daily_work_hours
        self.now = now
        self.quota = ChatQuota(quota_repo, now=now)
        self.documents = document_source
        self.limits = document_limits

    async def execute(
        self,
        user_id: UUID,
        conversation_id: UUID,
        message: str,
        attached: AttachedDocuments = NO_DOCUMENTS,
    ) -> ProjectChatTurn:
        text = message.strip()
        self._check_input(text, attached)

        # 1-3. Read everything in one short transaction. No LLM yet.
        async with self.repo.transaction():
            prepared = await self._prepare(user_id, conversation_id, attached)
        project = prepared.project
        today = self.now().astimezone(project.time_zone).date()

        # FR-08b-2: read the new documents (Backend + uploads). Errors stop here.
        new_attachments = await self._read_documents(prepared.library, attached)
        # #35; a message with documents only takes the language of the documents.
        latest = text or "\n".join(a.extracted_text for a in new_attachments)
        language = reply_language(latest, self._brief(prepared.history))
        if not text:
            text = DOCUMENTS_ONLY_TEXT[language]
        documents = self._prompt_documents(new_attachments, prepared.earlier, project)

        # 4. One LLM call (plus a retry), outside any transaction.
        client = project.client_name
        prompt_project = self._prompt_project(project, prepared.work)
        domains = self._prompt_domains(prepared.domains, prepared.k_bias)
        masked_text = mask_contact(text, client)
        history = self._fit_history(
            prepared.history,
            prompt_project,
            domains,
            masked_text,
            client,
            today,
            language,
        )
        context = DraftContext(
            domains=prepared.domains,
            project_deadline=project.deadline,
            today=today,
            existing_titles=tuple(
                mask_contact(t, client) for t in prepared.work.task_titles
            ),
            language=language,
        )
        result = await self.writer.write(
            prompt_project, domains, history, masked_text, context, documents
        )
        if result.reply is None:
            async with self.repo.transaction():
                await self.quota.log(prepared.subscription, result.calls, charged=False)
            raise AIUnavailableError(str(conversation_id))

        # 5. Save both messages, charge, log: all or nothing.
        try:
            return await self._save(
                user_id,
                conversation_id,
                text,
                self._with_truncation_warnings(result.reply, new_attachments),
                result.calls,
                prepared,
                today,
                new_attachments,
            )
        except (_LostLastCallError, ConversationAppliedError) as error:
            async with self.repo.transaction():
                await self.quota.log(prepared.subscription, result.calls, charged=False)
            if isinstance(error, ConversationAppliedError):
                raise
            raise QuotaExceededError("plan limit reached") from None

    def _check_input(self, text: str, attached: AttachedDocuments) -> None:
        if len(text) > self.max_message_chars or (not text and not attached.count):
            raise InvalidMessageError(
                f"message must be 1-{self.max_message_chars} characters; "
                "paste the main part of the brief"
            )
        if attached.count > self.limits.max_files:
            raise InvalidMessageError(
                f"at most {self.limits.max_files} documents per message"
            )
        if len(set(attached.document_ids)) != len(attached.document_ids):
            raise InvalidMessageError("the same document was attached twice")

    async def _prepare(
        self, user_id: UUID, conversation_id: UUID, attached: AttachedDocuments
    ) -> _Prepared:
        conversation = await self.repo.get_conversation(user_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        check_open(conversation)
        project = await open_project(self.repo, user_id, conversation.project_id)
        # FR-08b-2 step 3: library documents are checked before the quota (AC1, AC2).
        library = await self._library_documents(user_id, project, attached)
        subscription = await self.quota.check(user_id)
        messages = await self.repo.list_messages(user_id, conversation_id)
        return _Prepared(
            project=project,
            subscription=subscription,
            history=pick_history(messages, self.history_limit),
            domains=await self.repo.active_domains(),
            k_bias=await self.repo.get_k_biases(user_id),
            work=await self.repo.existing_work(user_id, project.project_id),
            library=library,
            earlier=await self.repo.list_attachments(user_id, conversation_id),
        )

    async def _library_documents(
        self, user_id: UUID, project: ChatProject, attached: AttachedDocuments
    ) -> list[ProjectDocument]:
        """The asked library files: this project's, files (not links). AC1, AC2."""
        if not attached.document_ids:
            return []
        if not project.allow_ai_documents:
            raise DocumentAccessOffError(str(project.project_id))
        found = []
        for document_id in attached.document_ids:
            document = await self.repo.get_document(user_id, document_id)
            if (
                document is None
                or document.project_id != project.project_id
                or document.source_type is not DocumentSourceType.FILE
            ):
                raise DocumentNotFoundError(str(document_id))
            size = document.size_bytes
            if size is not None and size > self.limits.max_file_bytes:
                raise DocumentError(DocumentErrorCode.TOO_LARGE, document.name)
            found.append(document)
        return found

    async def _read_documents(
        self, library: list[ProjectDocument], attached: AttachedDocuments
    ) -> list[NewAttachment]:
        """Download (in parallel) and read every new document, then share the
        text budget between them (#13). Any problem raises before the LLM."""
        if not library and not attached.uploads:
            return []
        if library and self.documents is None:
            raise RuntimeError("no DocumentSource wired for library documents")
        source = self.documents
        downloads = (
            await asyncio.gather(
                *(source.download(d.document_id, attached.auth_header) for d in library)
            )
            if library
            else []
        )
        files = [
            (
                AttachmentSource.HUB,
                d.document_id,
                d.name,
                d.storage_path or d.name,
                data,
            )
            for d, data in zip(library, downloads, strict=True)
        ] + [
            (AttachmentSource.UPLOAD, None, f.name, f.name, f.data)
            for f in attached.uploads
        ]
        texts = await asyncio.gather(
            *(
                asyncio.to_thread(
                    extract_text,
                    data,
                    name=name,
                    path=path,
                    max_bytes=self.limits.max_file_bytes,
                    max_pdf_pages=self.limits.max_pdf_pages,
                    enough_chars=self.limits.max_chars,
                )
                for _, _, name, path, data in files
            )
        )
        fitted = fit_to_budget(texts, self.limits.max_chars)
        return [
            NewAttachment(source, document_id, name[:255], f.text, f.is_truncated)
            for (source, document_id, name, _, _), f in zip(files, fitted, strict=True)
        ]

    def _prompt_documents(
        self,
        new: list[NewAttachment],
        earlier: list[ChatAttachment],
        project: ChatProject,
    ) -> list[PromptDocument]:
        """This message's documents, then earlier ones newest first, until the
        budget is used (#14). Library files are left out once access is off or
        the file was deleted. Names and texts are masked (#15, AC5)."""
        kept = [(a.name, a.extracted_text, a.is_truncated) for a in new]
        for old in reversed(earlier):
            if old.source is AttachmentSource.HUB and (
                old.document_id is None or not project.allow_ai_documents
            ):
                continue
            kept.append((old.name, old.extracted_text, old.is_truncated))
        documents = []
        left = self.limits.max_chars
        for name, text, truncated in kept:
            if left <= 0:
                break
            if len(text) > left:
                text, truncated = cut_text(text, left), True
            left -= len(text)
            client = project.client_name
            documents.append(
                PromptDocument(
                    mask_contact(name, client), mask_contact(text, client), truncated
                )
            )
        return documents

    @staticmethod
    def _with_truncation_warnings(
        reply: ProjectReply, new: list[NewAttachment]
    ) -> ProjectReply:
        """#17: tell the user which of their new documents were cut."""
        warnings = [
            truncation_warning(a.name, reply.language) for a in new if a.is_truncated
        ]
        if not warnings:
            return reply
        if reply.draft is not None:
            draft = replace(reply.draft, warnings=(*reply.draft.warnings, *warnings))
            return replace(reply, draft=draft)
        return replace(reply, text="\n\n".join([reply.text, *warnings]))

    def _prompt_project(
        self, project: ChatProject, work: ExistingWork
    ) -> PromptProject:
        """What the LLM may see (#3, #19): masked texts, no client name or contacts."""
        client = project.client_name
        limit = self.existing_titles_limit
        return PromptProject(
            name=mask_contact(project.name, client),
            description=mask_contact(project.description, client)
            if project.description
            else None,
            deadline=project.deadline,
            existing_milestones=tuple(
                mask_contact(t, client) for t in work.milestone_titles[:limit]
            ),
            existing_tasks=tuple(
                mask_contact(t, client) for t in work.task_titles[:limit]
            ),
        )

    def _prompt_domains(
        self, domains: dict[UUID, str], k_bias: dict[UUID, Decimal]
    ) -> list[PromptDomain]:
        return [
            PromptDomain(
                domain_id, name, extra_percent(k_bias.get(domain_id, self.initial_k))
            )
            for domain_id, name in domains.items()
        ]

    @staticmethod
    def _brief(history: Sequence[ProjectChatMessage]) -> str | None:
        """The first message of the chat when the user wrote it (#18, #35)."""
        if history and history[0].role is ChatRole.USER:
            return history[0].content
        return None

    @staticmethod
    def _line(message: ProjectChatMessage, client: str | None) -> ProjectChatLine:
        if message.role is ChatRole.USER:
            return ProjectChatLine("user", mask_contact(message.content, client))
        draft = message.draft.draft.milestones if message.draft else ()
        return ProjectChatLine("assistant", message.content, draft)

    def _fit_history(
        self,
        history: list[ProjectChatMessage],
        project: PromptProject,
        domains: list[PromptDomain],
        message: str,
        client: str | None,
        today: date,
        language: Language,
    ) -> list[ProjectChatLine]:
        """Keep the brief; drop the oldest of the others until the prompt fits (#18)."""
        lines = [self._line(m, client) for m in history]
        while len(lines) > 1:
            prompt = self.writer.render(
                project, domains, lines, message, today, language
            )
            if len(prompt) <= self.max_prompt_chars:
                break
            del lines[1]  # lines[0] is the brief
        return lines

    async def _save(
        self,
        user_id: UUID,
        conversation_id: UUID,
        text: str,
        reply: ProjectReply,
        calls: Sequence[LLMCall],
        prepared: _Prepared,
        today: date,
        new_attachments: Sequence[NewAttachment] = (),
    ) -> ProjectChatTurn:
        project = prepared.project
        async with self.repo.transaction():
            # Lock the chat: an Apply in another tab meanwhile wins (#5 of the flow).
            conversation = await self.repo.get_conversation(
                user_id, conversation_id, lock=True
            )
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            check_open(conversation)

            saved_draft = None
            if reply.draft is not None:
                numbers = project_numbers(
                    reply.draft,
                    prepared.k_bias,
                    self.initial_k,
                    project.deadline,
                    today,
                    prepared.subscription.is_free,
                    self.daily_work_hours,
                    reply.language,
                )
                draft = reply.draft
                if numbers.warning:
                    draft = replace(draft, warnings=(*draft.warnings, numbers.warning))
                draft = restore_client_in_notes(draft, project.client_name)  # #36
                saved_draft = SavedProjectDraft(draft, numbers)
            user_message = await self.repo.add_message(
                user_id,
                conversation_id,
                ChatRole.USER,
                text,  # as written
            )
            attachments = [
                await self.repo.add_attachment(user_id, user_message.message_id, a)
                for a in new_attachments
            ]
            ai_message = await self.repo.add_message(
                user_id, conversation_id, ChatRole.ASSISTANT, reply.text, saved_draft
            )
            if not await self.quota_repo.try_charge(prepared.subscription):
                raise _LostLastCallError
            await self.quota.log(prepared.subscription, calls, charged=True)
        return ProjectChatTurn(
            conversation_id,
            user_message,
            ai_message,
            project.time_zone,
            tuple(attachments),
        )
