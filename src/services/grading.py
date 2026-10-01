from __future__ import annotations

from datetime import datetime

from src.models.practice import AnswerResult, PracticeSession
from src.utils.text_utils import normalize_answer


def grade_session(session: PracticeSession) -> list[AnswerResult]:
    if session.submitted:
        return session.results
    item_map = session.article.item_map
    results = []
    for item_id in session.target_ids:
        item = item_map[item_id]
        user_answer = session.answers.get(item_id, "")
        correct = normalize_answer(user_answer) == item.normalized_answer
        item.user_answer, item.is_correct = user_answer, correct
        results.append(AnswerResult(item_id, user_answer, item.answer, correct))
    session.results = results
    session.submitted = True
    session.submitted_at = datetime.now()
    return results
