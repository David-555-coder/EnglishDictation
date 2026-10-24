from __future__ import annotations

from collections import OrderedDict
import json
from copy import deepcopy

from src.models.article import Article, Paragraph, RunStyle, TextSegment, WordItem
from src.models.practice import PracticeMode, PracticeSession
from src.utils.text_utils import normalize_answer, sentence_bounds
from src.services.learning_evidence import begin, first_round_result, restore_correction_context


def new_full_session(article: Article) -> PracticeSession:
    from src.services.learning_content import prepare_article
    article = prepare_article(article)
    return begin(PracticeSession(article, PracticeMode.FULL_ARTICLE, [item.id for item in article.word_items]))


def new_wrong_session(previous: PracticeSession) -> PracticeSession:
    session = begin(PracticeSession(deepcopy(previous.article), PracticeMode.CURRENT_WRONG, previous.wrong_ids), "relearning", previous)
    session.view_state["first_round_result"] = first_round_result(previous)
    return session


def group_items_by_sentence(session: PracticeSession) -> list[list[WordItem]]:
    item_map = session.article.item_map
    grouped: OrderedDict[tuple[int, int, int], list[WordItem]] = OrderedDict()
    for item_id in session.target_ids:
        item = item_map[item_id]
        grouped.setdefault(item.sentence_key, []).append(item)
    return list(grouped.values())


def new_saved_wrong_session(record: dict, answers: list[dict]) -> PracticeSession:
    """从数据库快照恢复一次历史错词练习，即使原 Word 已移动也能使用。"""
    items: list[WordItem] = []
    paragraphs_by_index: dict[int, str] = {}
    paragraph_keys = {}
    for n, row in enumerate(answers):
        original_index = row.get("paragraph_index", -1)
        paragraph_text = row.get("paragraph_text") or row.get("sentence") or ""
        answer = row.get("answer") or row.get("correct_answer") or row.get("normalized_answer") or ""
        if not answer.strip():
            raise ValueError("历史记录中缺少正确答案，无法开始练习。")
        key = (row.get("source_path", record.get("source_path", "")), original_index, paragraph_text)
        paragraph_index = paragraph_keys.setdefault(key, len(paragraph_keys))
        start = row.get("item_start")
        if start is None or start < 0 or paragraph_text[start:start + len(answer)] != answer:
            start = paragraph_text.find(answer)
        if start < 0:
            raise ValueError("历史记录的原句中找不到对应答案，请重新导入原文练习。")
        start = int(start)
        end = start + len(answer)
        sentence_start = row.get("sentence_start", -1)
        sentence_end = row.get("sentence_end", -1)
        if not (isinstance(sentence_start, int) and isinstance(sentence_end, int)
                and 0 <= sentence_start <= start < end <= sentence_end <= len(paragraph_text)):
            sentence_start, sentence_end = sentence_bounds(paragraph_text, start, end)
        item_id = f"saved-{record['id']}-{row.get('id', n)}"
        item = WordItem(item_id, answer, normalize_answer(answer), row.get("chinese") or "",
                              row.get("sentence") or paragraph_text, paragraph_text,
                              row.get("source_article") or record.get("article_name") or "历史练习",
                              row.get("source_path") or record.get("source_path") or "",
                              original_index, start, end, sentence_start, sentence_end, RunStyle())
        raw_context = row.get("learning_context") or {}
        item.learning_context = json.loads(raw_context) if isinstance(raw_context, str) else dict(raw_context)
        from src.services.learning_content import context_for
        item.learning_context = context_for(item)
        item.paragraph_index = paragraph_index
        items.append(item)
        paragraphs_by_index[paragraph_index] = paragraph_text
    paragraphs = [Paragraph(index, text, [TextSegment(text)]) for index, text in sorted(paragraphs_by_index.items())]
    article = Article(record.get("source_path") or "", record.get("article_name") or "历史练习", paragraphs, items)
    session = begin(PracticeSession(article, PracticeMode.CURRENT_WRONG, [item.id for item in items]))
    restore_correction_context(session, record.get("memory_context"), record.get("submission_key"))
    return session


def new_long_term_session(records):
    session = new_saved_wrong_session({"id": "book", "article_name": "长期错词本", "source_path": ""}, records)
    session.mode = PracticeMode.LONG_TERM_WRONG
    return session
