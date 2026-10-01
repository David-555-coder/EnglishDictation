"""Versioned, JSON-only snapshots: no pickle or executable content."""
from dataclasses import asdict
from datetime import datetime

from src.models.article import Article, Paragraph, RunStyle, TextSegment, WordItem
from src.models.practice import AnswerResult, PracticeMode, PracticeSession


def article_to_dict(article):
    return asdict(article)


def article_from_dict(data):
    paragraphs = []
    for raw in data["paragraphs"]:
        row = dict(raw)
        row["segments"] = [TextSegment(text=s["text"], target_id=s.get("target_id"),
                                       style=RunStyle(**s.get("style", {}))) for s in row["segments"]]
        paragraphs.append(Paragraph(**row))
    items = []
    for raw in data["word_items"]:
        row = dict(raw)
        row["style"] = RunStyle(**row.get("style", {}))
        items.append(WordItem(**row))
    return Article(data["source_path"], data["filename"], paragraphs, items, data.get("metadata", {}))


def session_to_dict(session):
    data = asdict(session)
    data["version"] = 1
    data["mode"] = session.mode.value
    data["started_at"] = session.started_at.isoformat()
    data["submitted_at"] = session.submitted_at.isoformat() if session.submitted_at else None
    return data


def session_from_dict(data):
    row = dict(data)
    if row.pop("version", 1) != 1:
        raise ValueError("不支持的练习快照版本")
    row["article"] = article_from_dict(row["article"])
    row["mode"] = PracticeMode(row["mode"])
    row["started_at"] = datetime.fromisoformat(row["started_at"])
    row["submitted_at"] = datetime.fromisoformat(row["submitted_at"]) if row["submitted_at"] else None
    row["results"] = [AnswerResult(**result) for result in row["results"]]
    session = PracticeSession(**row)
    item_map = session.article.item_map
    if any(item_id not in item_map for item_id in session.target_ids):
        raise ValueError("练习快照中的题目位置无效")
    return session
