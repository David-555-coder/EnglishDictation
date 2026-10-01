from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RunStyle:
    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_rgb: str | None = None


@dataclass(slots=True)
class TextSegment:
    text: str
    target_id: str | None = None
    style: RunStyle = field(default_factory=RunStyle)

    @property
    def is_target(self) -> bool:
        return self.target_id is not None


@dataclass(slots=True)
class WordItem:
    id: str
    answer: str
    normalized_answer: str
    chinese: str
    sentence: str
    paragraph_text: str
    filename: str
    source_path: str
    paragraph_index: int
    start: int
    end: int
    sentence_start: int
    sentence_end: int
    style: RunStyle = field(default_factory=RunStyle)
    user_answer: str = ""
    is_correct: bool | None = None

    @property
    def sentence_key(self) -> tuple[int, int, int]:
        return self.paragraph_index, self.sentence_start, self.sentence_end


@dataclass(slots=True)
class Paragraph:
    index: int
    text: str
    segments: list[TextSegment]
    alignment: int | None = None
    style_name: str | None = None


@dataclass(slots=True)
class ArticleSummary:
    """Small library row; the full immutable snapshot is loaded on selection."""
    source_path: str
    filename: str
    target_count: int


@dataclass(slots=True)
class Article:
    source_path: str
    filename: str
    paragraphs: list[Paragraph]
    word_items: list[WordItem]
    metadata: dict = field(default_factory=dict)

    @property
    def target_count(self) -> int:
        return len(self.word_items)

    @property
    def source(self) -> Path:
        return Path(self.source_path)

    @property
    def item_map(self) -> dict[str, WordItem]:
        return {item.id: item for item in self.word_items}
