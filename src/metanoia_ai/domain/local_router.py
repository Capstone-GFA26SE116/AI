"""FR-22b: pick the assistant tool without the LLM when the sentence is a familiar one.

Pure Python, no extra library. The sentence is compared with hand-written examples:
character n-grams (3–5, on accent-free text) weighted by TF-IDF, cosine similarity,
the label of the closest example wins. It is used only when it is clearly ahead (#1, #8);
anything unclear goes to the LLM as before (FR-22). Write requests never become a read
tool (AC2): a rule sends them to `unsupported` first.

Examples of named tools hold "{x}" where the task / project name goes. The words of
those examples are the "frame" of the tool: what is left of a sentence after removing
them is the spoken name (#4).
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from metanoia_ai.domain.assistant_tools import (
    DEFAULT_EXTRA_MINUTES,
    NAMED_TOOLS,
    WRITE_TOOLS,
    SmalltalkKind,
    ToolChoice,
    ToolName,
    clamp_minutes,
)
from metanoia_ai.domain.name_matching import fold
from metanoia_ai.domain.spoken_period import SpokenPeriod

NGRAM_SIZES = (3, 4, 5)
NAME_SLOT = "{x}"
COMMAND_LABELS = frozenset(t.value for t in WRITE_TOOLS)  # FR-22c
SMALLTALK_LABELS = {
    "smalltalk_greet": SmalltalkKind.GREET,
    "smalltalk_thanks": SmalltalkKind.THANKS,
    "smalltalk_help": SmalltalkKind.HELP,
}

# Phrases that ask to DO something (#7). Checked on accent-free words, whole words only.
WRITE_PHRASES = (
    "bat dau",
    "dung lai",
    "dung timer",
    "tat timer",
    "bat timer",
    "tao",
    "them",
    "xoa",
    "sua",
    "doi ten",
    "duyet",
    "huy",
    "thanh toan",
    "nang cap",
    "danh dau",
    "chuyen sang",
    "start",
    "stop",
    "create",
    "add",
    "delete",
    "remove",
    "edit",
    "rename",
    "approve",
    "cancel",
    "pay",
    "upgrade",
    "mark",
    "accept",
    "chap nhan",
    "dong y",
)
# A sentence with one of these is a question ("Tôi đã bắt đầu việc gì?") ...
QUESTION_WORDS = (
    "gi",
    "nao",
    "bao nhieu",
    "bao lau",
    "khi nao",
    # not plain "dau": "bắt đầu" folds to "bat dau"
    "toi dau",
    "o dau",
    "den dau",
    "the nao",
    "sao",
    "khong",
    "chua",
    "what",
    "which",
    "how",
    "when",
    "is",
    "do",
)
# ... unless it asks for help doing it ("tạo task giúp tôi được không?").
REQUEST_MARKERS = (
    "giup",
    "dum",
    "di",
    "nhe",
    "duoc khong",
    "please",
    "can you",
    "could you",
)

# Follow-ups need the earlier turns: "còn ngày mai thì sao?", "dự án đó" (#6).
FOLLOW_UP_PHRASES = (
    "thi sao",
    "con ",
    "du an do",
    "viec do",
    "cai do",
    "what about",
    " it ",
    "that one",
)

# Words a sentence must hold for the router to pick that label on its own. Char n-grams
# alone mix up sentences that share words like "hôm nay" ("Thời tiết hôm nay thế nào?"
# looked like the briefing): without an anchor the LLM decides (#8). Accent-free.
ANCHORS: dict[str, tuple[str, ...]] = {
    "today_briefing": (
        "lam gi",
        "lam truoc",
        "uu tien",
        "quan trong",
        "can lam",
        "ke hoach",
        "tom tat",
        "should i do",
        "work on first",
        "focus on",
        "plan for",
        "overview",
        "important",
        "summarize",
        "my day",
    ),
    "at_risk_deadlines": ("deadline", "han", "tre", "due", "late", "at risk", "urgent"),
    "schedule_for": (
        "lich",
        "hop",
        "ban",
        "calendar",
        "schedule",
        "meeting",
        "busy",
        "do i have",
    ),
    "running_timer": (
        "bam gio",
        "timer",
        "dong ho",
        "tap trung vao",
        "timing",
        "working on this",
        "phien tap trung hien tai",
    ),
    "estimation_habits": (
        "uoc luong",
        "doan",
        "tre",
        "lau hon",
        "thieu",
        "lech",
        "nhanh hay cham",
        "estimate",
        "estimates",
        "estimating",
        "underestimate",
        "late",
        "longer than",
    ),
    "productive_hours": (
        "hieu qua",
        "tap trung",
        "nang suat",
        "gio vang",
        "lam viec tot nhat",
        "productive",
        "focus best",
        "best working",
        "most focused",
    ),
    "similar_task_time": ("bao lau", "mat", "ton", "how long", "how much time"),
    "project_progress": (
        "du an",
        "tien do",
        "project",
        "progress",
        "far along",
    ),
    "list_projects": ("du an", "project", "projects"),
    "ai_quota": ("luot", "ai", "han muc", "quota", "credits"),
    "pending_confirmations": (
        "xac nhan",
        "tu dung",
        "confirm",
        "confirmation",
        "stopped automatically",
    ),
    "unread_notifications": ("thong bao", "nhac", "notification", "notifications"),
    "pending_proposal": (
        "de xuat",
        "xep lich",
        "proposal",
        "proposed",
        "make a schedule",
    ),
    "upcoming_milestones": ("milestone", "milestones", "giai doan", "ban giao"),
    "smalltalk_greet": ("chao", "hello", "hi", "alo", "hey", "good morning"),
    "smalltalk_thanks": ("cam on", "thank", "thanks"),
    "smalltalk_help": (
        "lam duoc gi",
        "giup duoc gi",
        "giup gi",
        "hoi ban",
        "huong dan",
        "biet lam",
        "can you do",
        "help me",
        "ask you",
    ),
    # FR-22c commands
    "start_timer": ("bat dau", "bam gio cho", "bat timer", "start"),
    "stop_timer": ("dung", "tam dung", "tam nghi", "nghi chut", "stop", "pause"),
    "finish_timer": ("xong", "hoan thanh", "done", "finished", "finish"),
    "extend_timer": ("them", "gia han", "more", "extra", "extend", "add"),
    "approve_proposal": ("duyet", "chap nhan lich", "dong y lich", "approve", "accept"),
    "unsupported": (),  # open-ended: reached only by clear examples and the write rule
}

# Spoken part of the day (#5), longest first so "chieu nay" wins over "nay".
PERIOD_PHRASES: tuple[tuple[str, SpokenPeriod], ...] = (
    ("sang nay", SpokenPeriod.MORNING),
    ("buoi sang", SpokenPeriod.MORNING),
    ("this morning", SpokenPeriod.MORNING),
    ("chieu nay", SpokenPeriod.AFTERNOON),
    ("buoi chieu", SpokenPeriod.AFTERNOON),
    ("this afternoon", SpokenPeriod.AFTERNOON),
    ("toi nay", SpokenPeriod.EVENING),
    ("buoi toi", SpokenPeriod.EVENING),
    ("this evening", SpokenPeriod.EVENING),
    ("tonight", SpokenPeriod.EVENING),
    ("ngay mai", SpokenPeriod.TOMORROW),
    ("tomorrow", SpokenPeriod.TOMORROW),
    ("mai", SpokenPeriod.TOMORROW),
)


@dataclass(frozen=True)
class Example:
    label: str  # a ToolName value, or one of SMALLTALK_LABELS
    text: str


@dataclass(frozen=True)
class Guess:
    label: str
    score: float  # cosine with the closest example of this label, 0–1
    runner_up: float  # the best score of any other label
    nearest: str  # the closest example (for logs and tests)


def _words(text: str) -> str:
    """Accent-free words with one space at both ends, for whole-word checks."""
    return f" {fold(text)} "


def has_phrase(text: str, phrases: Iterable[str]) -> bool:
    words = _words(text)
    return any(f" {p.strip()} " in words for p in phrases)


def ngrams(text: str) -> Counter[str]:
    padded = f" {fold(text.replace(NAME_SLOT, ' '))} "
    grams: Counter[str] = Counter()
    for size in NGRAM_SIZES:
        grams.update(padded[i : i + size] for i in range(len(padded) - size + 1))
    return grams


class LocalRouter:
    """Nearest example by TF-IDF cosine. Built once from the examples file."""

    def __init__(self, examples: Sequence[Example]) -> None:
        if not examples:
            raise ValueError("the local router needs examples")
        self.examples = tuple(examples)
        counts = [ngrams(e.text) for e in self.examples]
        documents = Counter(gram for grams in counts for gram in grams)
        total = len(counts)
        self.idf = {
            g: math.log((1 + total) / (1 + n)) + 1 for g, n in documents.items()
        }
        self.vectors = [self._vector(grams) for grams in counts]
        self.frames = _frames(self.examples)

    def _vector(self, grams: Mapping[str, int]) -> dict[str, float]:
        weights = {
            g: (1 + math.log(n)) * self.idf[g]
            for g, n in grams.items()
            if g in self.idf
        }
        norm = math.sqrt(sum(w * w for w in weights.values()))
        return {g: w / norm for g, w in weights.items()} if norm else {}

    def guess(self, text: str) -> Guess | None:
        """The closest label, or None when no n-gram is known at all."""
        vector = self._vector(ngrams(text))
        if not vector:
            return None
        best: dict[str, tuple[float, str]] = {}
        for example, other in zip(self.examples, self.vectors, strict=True):
            similarity = sum(w * other.get(g, 0.0) for g, w in vector.items())
            if similarity > best.get(example.label, (-1.0, ""))[0]:
                best[example.label] = (similarity, example.text)
        ranked = sorted(best.items(), key=lambda item: -item[1][0])
        label, (score, nearest) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else 0.0
        return Guess(label, score, runner_up, nearest)

    def spoken_name(self, text: str, label: str) -> str | None:
        """What is left after the tool's frame words: the name the user said, if any."""
        frame = self.frames.get(label, frozenset())
        left = [w for w in fold(text).split() if w not in frame]
        return " ".join(left) or None


def _frames(examples: Sequence[Example]) -> dict[str, frozenset[str]]:
    """Per named tool: every word of its examples except the name slot."""
    frames: dict[str, set[str]] = {}
    for example in examples:
        if example.label in NAMED_TOOLS:
            words = fold(example.text.replace(NAME_SLOT, " ")).split()
            frames.setdefault(example.label, set()).update(words)
    return {label: frozenset(words) for label, words in frames.items()}


def spoken_period(text: str) -> SpokenPeriod:
    for phrase, period in PERIOD_PHRASES:
        if has_phrase(text, (phrase,)):
            return period
    return SpokenPeriod.TODAY


def is_write_request(text: str) -> bool:
    """Asks to do something (#7). "Tôi đã bắt đầu việc gì?" is a question, not a request."""
    if not has_phrase(text, WRITE_PHRASES):
        return False
    return has_phrase(text, REQUEST_MARKERS) or not has_phrase(text, QUESTION_WORDS)


def is_follow_up(text: str, has_history: bool) -> bool:
    return has_history and has_phrase(text, FOLLOW_UP_PHRASES)


@dataclass(frozen=True)
class LocalSettings:
    min_score: float
    min_gap: float


def local_choice(
    router: LocalRouter,
    text: str,
    has_history: bool,
    settings: LocalSettings,
) -> ToolChoice | None:
    """A sure tool choice, or None to ask the LLM. Names are found by the caller (#4)."""
    if is_follow_up(text, has_history):
        return None  # needs the earlier turns: the LLM reads it better (#6)
    guess = router.guess(text)
    sure = guess is not None and (
        guess.score >= settings.min_score
        and guess.score - guess.runner_up >= settings.min_gap
    )
    if guess is not None and guess.label in COMMAND_LABELS:
        # FR-22c: one of the 5 commands the app runs after a "yes". A question
        # ("Tôi đã bắt đầu việc gì?") is not a command, unless it asks for help.
        is_question = has_phrase(text, QUESTION_WORDS) and not has_phrase(
            text, REQUEST_MARKERS
        )
        if sure and not is_question and has_phrase(text, ANCHORS[guess.label]):
            return _command_choice(router, text, guess)
        # Otherwise fall through: another write request is still refused below.
    if is_write_request(text):
        if any(has_phrase(text, ANCHORS[label]) for label in COMMAND_LABELS):
            return (
                None  # may be one of the 5 commands said another way: the LLM decides
            )
        return ToolChoice(ToolName.UNSUPPORTED, 1.0)  # any other write: not supported
    if has_phrase(text, WRITE_PHRASES):
        # A question with a write verb ("Sửa lỗi mất bao lâu?" is a task name).
        return None
    if guess is None or not sure or guess.label in COMMAND_LABELS:
        return None
    if guess.label == "unsupported":
        # Only when nothing in the sentence points to a read tool ("What's on my
        # calendar tomorrow?" must not be refused).
        if any(has_phrase(text, words) for words in ANCHORS.values()):
            return None
    elif not has_phrase(text, ANCHORS[guess.label]):
        return None
    confidence = round(guess.score, 3)
    if guess.label in SMALLTALK_LABELS:
        return ToolChoice(
            ToolName.SMALLTALK, confidence, smalltalk=SMALLTALK_LABELS[guess.label]
        )
    tool = ToolName(guess.label)
    if tool is ToolName.SCHEDULE_FOR:
        return ToolChoice(tool, confidence, period=spoken_period(text))
    if tool in NAMED_TOOLS:
        name = router.spoken_name(text, guess.label)
        if name is None and tool is not ToolName.UPCOMING_MILESTONES:
            return None  # a task / project is needed but none was said
        return ToolChoice(tool, confidence, name=name)
    return ToolChoice(tool, confidence)


def _command_choice(router: LocalRouter, text: str, guess: Guess) -> ToolChoice | None:
    tool = ToolName(guess.label)
    confidence = round(guess.score, 3)
    if tool is ToolName.START_TIMER:
        name = router.spoken_name(text, guess.label)
        return None if name is None else ToolChoice(tool, confidence, name=name)
    if tool is ToolName.EXTEND_TIMER:
        return ToolChoice(tool, confidence, minutes=spoken_minutes(text))
    return ToolChoice(tool, confidence)


_MINUTES = re.compile(r"\b(\d{1,3})\s*(phut|p|minute|minutes|mins|min)\b")
_HOURS = re.compile(r"\b(\d{1,2})\s*(tieng|gio|hour|hours|h)\b")
_HALF_HOUR = ("nua tieng", "nua gio", "half an hour", "half hour")
_ONE_HOUR = ("mot tieng", "mot gio", "an hour", "one hour")


def spoken_minutes(text: str) -> int:
    """Extra minutes said in the sentence (FR-22c #3), clamped; nothing said → 15."""
    words = fold(text)
    total = 0
    for match in _HOURS.finditer(words):
        total += int(match.group(1)) * 60
    for match in _MINUTES.finditer(words):
        total += int(match.group(1))
    if total == 0 and has_phrase(text, _HALF_HOUR):
        total = 30
    if total == 0 and has_phrase(text, _ONE_HOUR):
        total = 60
    return clamp_minutes(total) if total else DEFAULT_EXTRA_MINUTES


def load_examples(raw: object) -> list[Example]:
    """The examples file: {"label": ["sentence", …], …}. Unknown labels are rejected."""
    if not isinstance(raw, Mapping):
        raise TypeError("examples must be an object of label → sentences")
    known = {t.value for t in ToolName} - {ToolName.SMALLTALK.value} | set(
        SMALLTALK_LABELS
    )
    examples = []
    for label, sentences in raw.items():
        if label not in known:
            raise ValueError(f"unknown label {label}")
        examples += [Example(label, str(s)) for s in sentences]
    return examples
