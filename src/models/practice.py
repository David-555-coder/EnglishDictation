from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .article import Article


class PracticeMode(str, Enum):
    FULL_ARTICLE = "整篇文章"
    CURRENT_WRONG = "本次错词复习"
    LONG_TERM_WRONG = "长期错词复习"
    DAILY_REVIEW = "今日复习"


@dataclass(slots=True)
class AnswerResult:
    item_id: str
    user_answer: str
    correct_answer: str
    is_correct: bool


@dataclass
class PracticeSession:
    article: Article
    mode: PracticeMode
    target_ids: list[str]
    database_id: int | None = None
    answers: dict[str, str] = field(default_factory=dict)
    submitted: bool = False
    viewed_answer: bool = False  # Legacy draft compatibility only; never affects results.
    results: list[AnswerResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    submitted_at: datetime | None = None
    session_key: str = field(default_factory=lambda: uuid4().hex)
    view_state: dict = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.target_ids)

    @property
    def filled(self) -> int:
        return sum(bool(self.answers.get(item_id, "").strip()) for item_id in self.target_ids)

    @property
    def unfilled(self) -> int:
        return self.total - self.filled

    @property
    def correct_count(self) -> int:
        return sum(result.is_correct for result in self.results)

    @property
    def wrong_count(self) -> int:
        return self.total - self.correct_count if self.submitted else 0

    @property
    def accuracy(self) -> float:
        return self.correct_count * 100 / self.total if self.total else 0.0

    @property
    def wrong_ids(self) -> list[str]:
        return [result.item_id for result in self.results if not result.is_correct]
