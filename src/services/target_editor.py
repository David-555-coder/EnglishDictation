import hashlib
from src.models.article import RunStyle, TextSegment, WordItem
from src.utils.text_utils import ENGLISH_TARGET_RE, normalize_answer, extract_chinese_note, sentence_bounds


def rebuild_segments(article, paragraph_index):
    paragraph = next(p for p in article.paragraphs if p.index == paragraph_index)
    style = paragraph.segments[0].style if paragraph.segments else RunStyle()
    items = sorted((i for i in article.word_items if i.paragraph_index == paragraph_index), key=lambda i: i.start)
    segments, cursor = [], 0
    for item in items:
        if item.start > cursor: segments.append(TextSegment(paragraph.text[cursor:item.start], style=style))
        segments.append(TextSegment(item.answer, item.id, item.style))
        cursor = item.end
    if cursor < len(paragraph.text): segments.append(TextSegment(paragraph.text[cursor:], style=style))
    paragraph.segments = segments or [TextSegment(paragraph.text, style=style)]
    article.word_items.sort(key=lambda i: (i.paragraph_index, i.start))
    article.metadata["manually_adjusted"] = True


def set_target(article, paragraph_index, start, end):
    paragraph = next(p for p in article.paragraphs if p.index == paragraph_index)
    if not 0 <= start < end <= len(paragraph.text):
        raise ValueError("请先选中一个英文单词或短语。")
    while start < end and paragraph.text[start].isspace(): start += 1
    while end > start and paragraph.text[end - 1].isspace(): end -= 1
    answer = paragraph.text[start:end]
    if not ENGLISH_TARGET_RE.fullmatch(answer):
        raise ValueError("请只选择英文单词或短语，不要包含中文解释、括号或句末标点。")
    article.word_items[:] = [i for i in article.word_items
                            if i.paragraph_index != paragraph_index or i.end <= start or i.start >= end]
    item_id = hashlib.sha1(f"{article.source_path}|{paragraph_index}|{start}|{end}|{answer}".encode()).hexdigest()[:20]
    left, right = sentence_bounds(paragraph.text, start, end)
    article.word_items.append(WordItem(item_id, answer, normalize_answer(answer),
        extract_chinese_note(paragraph.text, end), paragraph.text[left:right], paragraph.text,
        article.filename, article.source_path, paragraph_index, start, end, left, right))
    rebuild_segments(article, paragraph_index)


def remove_target(article, item_id):
    item = article.item_map[item_id]
    article.word_items[:] = [i for i in article.word_items if i.id != item_id]
    rebuild_segments(article, item.paragraph_index)
