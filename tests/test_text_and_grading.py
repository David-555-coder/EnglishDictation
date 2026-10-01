from pathlib import Path

from src.models.article import Article, Paragraph, WordItem
from src.models.practice import PracticeMode, PracticeSession
from src.services.grading import grade_session
from src.utils.text_utils import normalize_answer, sentence_bounds


def item(answer="ambition"):
    text = f"I have {answer}（抱负，雄心）."
    start = text.index(answer)
    return WordItem("1", answer, normalize_answer(answer), "抱负，雄心", text, text,
                    "a.docx", str(Path("a.docx")), 0, start, start + len(answer), 0, len(text))


def session(user_answer):
    word = item()
    article = Article("a.docx", "a.docx", [Paragraph(0, word.paragraph_text, [])], [word])
    result = PracticeSession(article, PracticeMode.FULL_ARTICLE, [word.id], answers={word.id: user_answer})
    grade_session(result)
    return result


def test_normalize_ignores_case_outer_and_repeated_spaces():
    assert normalize_answer(" Take   Advantage Of ") == "take advantage of"


def test_grading_accepts_case_but_rejects_spelling_change():
    assert session("Ambition").correct_count == 1
    assert session("ambitious").wrong_count == 1


def test_sentence_bounds_extracts_target_sentence():
    text = "First one. The company gained momentum rapidly! Last."
    start = text.index("momentum")
    left, right = sentence_bounds(text, start, start + 8)
    assert text[left:right] == "The company gained momentum rapidly!"
