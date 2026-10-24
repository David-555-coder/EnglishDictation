"""Local sentence-specific learning revisions. Submitted snapshots stay immutable."""
from copy import deepcopy
from dataclasses import asdict
from difflib import SequenceMatcher
import hashlib
import json
from uuid import uuid4

from src.models.article import Article, Paragraph, RunStyle, TextSegment, WordItem
from src.services.target_editor import rebuild_segments, set_target, remove_target
from src.utils.text_utils import ENGLISH_TARGET_RE, CHINESE_NOTE_RE, normalize_answer, sentence_bounds, extract_chinese_note


def fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def context_for(item):
    if item.learning_context:
        return deepcopy(item.learning_context)
    return {"key": fingerprint(item.source_path, item.paragraph_index, item.paragraph_text,
                               item.start, item.end),
            "paragraph_key": fingerprint(item.source_path, item.paragraph_index, item.paragraph_text),
            "source_path": item.source_path, "paragraph_index": item.paragraph_index,
            "base_text": item.paragraph_text, "start": item.start, "end": item.end, "note": ""}


def prepare_article(article):
    article = deepcopy(article)
    origins = article.metadata.setdefault("learning_paragraphs", {})
    for p in article.paragraphs:
        items = [i for i in article.word_items if i.paragraph_index == p.index]
        for item in items:
            item.learning_context = context_for(item)
        if str(p.index) not in origins:
            context = items[0].learning_context if items else {}
            source = context.get("source_path", article.source_path)
            index = context.get("paragraph_index", p.index)
            origins[str(p.index)] = {"source_path": source, "paragraph_index": index,
                "key": context.get("paragraph_key", fingerprint(source, index, p.text)),
                "aliases": [context.get("base_text", p.text), p.text],
                "complete_targets": not article.metadata.get('learning_partial', False)}
    return article


def note_for(item):
    return item.learning_context.get("note", "")


def _refresh(article, index):
    p = next(p for p in article.paragraphs if p.index == index)
    for item in article.word_items:
        if item.paragraph_index != index:
            continue
        item.paragraph_text = p.text
        item.sentence_start, item.sentence_end = sentence_bounds(p.text, item.start, item.end)
        item.sentence = p.text[item.sentence_start:item.sentence_end]
        item.chinese = extract_chinese_note(p.text, item.end)
    rebuild_segments(article, index)
    article.metadata["learning_edited"] = True


def edit_paragraph(article, index, text):
    """Map all target boundaries together; reject ambiguous/cross-target replacements."""
    if "\n" in text or "\r" in text or not text.strip():
        raise ValueError("请修改当前段落的文字；本版不新增、删除或拆分段落。")
    p = next(p for p in article.paragraphs if p.index == index)
    old = p.text
    if old == text:
        return
    opcodes = SequenceMatcher(None, old, text, autojunk=False).get_opcodes()

    def position(value, end=False):
        for tag, a, b, c, d in opcodes:
            if tag == "insert" and value == a:
                return c if end else d
            if (a < value <= b) if end else (a <= value < b):
                return c + value - a if tag == "equal" else (d if end else c)
        return len(text)

    updates = []
    for item in sorted((i for i in article.word_items if i.paragraph_index == index), key=lambda i: i.start):
        for tag, a, b, c, d in opcodes:
            if tag in {'replace', 'delete'} and a < item.end and b > item.start and not (item.start <= a and b <= item.end):
                raise ValueError(f"这段改动较大，无法可靠对应“{item.answer}”。请先取消这一处挖空，修改正文后重新选取。")
        start, end = position(item.start), position(item.end, True)
        # Character diffs can align only 'cat' inside 'cats', or 'not' inside
        # 'nothing'. Preserve complete English tokens, never accept fragments.
        token = lambda char: char.isascii() and (char.isalpha() or char.isdigit()) or char in "'-’"
        while start > 0 and start < len(text) and token(text[start-1]) and token(text[start]): start -= 1
        while end > 0 and end < len(text) and token(text[end-1]) and token(text[end]): end += 1
        answer = text[start:end]
        if not answer or not ENGLISH_TARGET_RE.fullmatch(answer):
            raise ValueError(f"无法确定“{item.answer}”修改后的挖空范围。请先取消这一处挖空，修改正文后重新选取。")
        if updates and start < updates[-1][2]:
            raise ValueError("修改后的挖空范围重叠，请先调整相关挖空。")
        updates.append((item, start, end, answer))
    for item, start, end, answer in updates:
        item.learning_context = context_for(item)
        if normalize_answer(answer) != item.normalized_answer:
            item.learning_context["key"] = uuid4().hex
        item.start, item.end, item.answer = start, end, answer
        item.normalized_answer = normalize_answer(answer)
    origins = article.metadata["learning_paragraphs"][str(index)]
    origins["aliases"] = list(dict.fromkeys(origins["aliases"] + [old, text]))
    p.text = text
    _refresh(article, index)


def edit_hint(article, item_id, chinese, note):
    item = article.item_map[item_id]
    chinese, note = chinese.strip(), note.strip()
    if any(c in chinese for c in "\r\n（）()【】[]"):
        raise ValueError("本句释义不需要填写括号或换行。")
    if len(chinese) > 200 or len(note) > 300:
        raise ValueError("释义最多200字，易混备注最多300字。")
    p = next(p for p in article.paragraphs if p.index == item.paragraph_index)
    import re
    match = CHINESE_NOTE_RE.match(p.text[item.end:])
    if match is None:
        match = re.match(r'^[ \t]*(?:[:：—][ \t]*)?([\u3400-\u9fff][\u3400-\u9fff、，； \t]*)', p.text[item.end:])
    tail = item.end + (match.end() if match else 0)
    if match:
        matched = match.group()
        tail -= len(matched) - len(matched.rstrip(' \t'))
    replacement = f"（{chinese}）" if chinese else ""
    text = p.text[:item.end] + replacement + p.text[tail:]
    # This edit is strictly after the selected answer; preserve every answer span.
    delta = len(replacement) - (tail - item.end)
    for other in article.word_items:
        if other.paragraph_index == item.paragraph_index and other.start >= tail and other.id != item.id:
            other.start += delta; other.end += delta
    p.text = text
    item.learning_context = context_for(item)
    item.learning_context["note"] = note
    origin = article.metadata["learning_paragraphs"][str(p.index)]
    origin["aliases"] = list(dict.fromkeys(origin["aliases"] + [text]))
    _refresh(article, p.index)


def add_blank(article, index, start, end):
    text = next(p.text for p in article.paragraphs if p.index == index)
    if not 0 <= start < end <= len(text):
        raise ValueError('请先选中完整英文单词或短语。')
    while start < end and text[start].isspace(): start += 1
    while end > start and text[end-1].isspace(): end -= 1
    word_char = lambda c: c.isascii() and c.isalpha() or c in "'-’"
    if start < end and ((start > 0 and word_char(text[start-1]) and word_char(text[start]))
                       or (end < len(text) and word_char(text[end-1]) and word_char(text[end]))):
        raise ValueError('请选中完整英文单词或短语，避免把单词的一部分设为答案。')
    previous = {i.id: i for i in article.word_items}
    set_target(article, index, start, end)
    origin = article.metadata["learning_paragraphs"][str(index)]
    for item in article.word_items:
        if item.id in previous and not item.learning_context:
            item.learning_context = context_for(previous[item.id])
        elif item.id not in previous:
            item.learning_context = {"key": uuid4().hex, "paragraph_key": origin["key"],
                "source_path": origin["source_path"], "paragraph_index": origin["paragraph_index"],
                "base_text": next(p.text for p in article.paragraphs if p.index == index),
                "start": item.start, "end": item.end, "note": ""}
    _refresh(article, index)


def cancel_blank(article, item_id):
    index = article.item_map[item_id].paragraph_index
    remove_target(article, item_id)
    _refresh(article, index)


def selection_english(text):
    import re
    stripped = re.sub(r'[（(\[【][^()\n（）【】\[\]]*[）)\]】]',
        lambda m: ' ' if any('\u3400'<=c<='\u9fff' for c in m.group()) else m.group(), text)
    return ' '.join(stripped.split())


def merge_blank(article, index, start, end, chinese):
    import re
    p = next(p for p in article.paragraphs if p.index == index)
    if not 0 <= start < end <= len(p.text): raise ValueError('请先选择需要合并的完整短语。')
    if any(c in chinese for c in '\r\n（）()【】[]') or len(chinese)>200:
        raise ValueError('本句释义最多200字，不需要填写括号或换行。')
    while start < end and p.text[start].isspace(): start+=1
    while end > start and p.text[end-1].isspace(): end-=1
    raw = p.text[start:end]
    english = selection_english(raw)
    if not ENGLISH_TARGET_RE.fullmatch(english):
        raise ValueError('请选择完整英文短语，可包含原有括号释义，但不要包含句末标点。')
    affected = [i for i in article.word_items if i.paragraph_index == index and i.start < end and i.end > start]
    if any(i.start < start or i.end > end for i in affected):
        raise ValueError('合并范围需要完整包含原有挖空。')
    tail = CHINESE_NOTE_RE.match(p.text[end:])
    if tail: end += tail.end()
    for item in affected: cancel_blank(article, item.id)
    replacement = english + (f'（{chinese.strip()}）' if chinese.strip() else '')
    edit_paragraph(article,index,p.text[:start]+replacement+p.text[end:])
    add_blank(article,index,start,start+len(english))


def paragraph_payload(article, p):
    return {"paragraph": asdict(p), "items": [asdict(i) for i in article.word_items if i.paragraph_index == p.index],
            "origin": article.metadata["learning_paragraphs"][str(p.index)]}


def row_for(item):
    context = context_for(item)
    return {"item_id": item.id, "answer": item.answer, "normalized_answer": item.normalized_answer,
        "chinese": item.chinese, "sentence": item.sentence, "paragraph_text": item.paragraph_text,
        "paragraph_index": context["paragraph_index"], "item_start": item.start, "item_end": item.end,
        "sentence_start": item.sentence_start, "sentence_end": item.sentence_end,
        "source_path": item.source_path, "source_article": item.filename,
        "learning_context": json.dumps(context, ensure_ascii=False)}


class ContentResolver:
    def __init__(self, connection):
        self.edits = [json.loads(r[0]) for r in connection.execute("SELECT content_json FROM learning_content_edits")]

    def resolve(self, row):
        row = dict(row)
        raw = row.get("learning_context") or {}
        context = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
        source, text = row.get("source_path", ""), row.get("paragraph_text") or row.get("sentence", "")
        matches = [p for p in self.edits if p["origin"]["source_path"] == source and
                   (p["origin"]["key"] == context.get("paragraph_key") or text in p["origin"]["aliases"])]
        sentence_only = row.get('paragraph_index', -1) < 0 or context.get('paragraph_index', 0) < 0
        if not matches and sentence_only and text:
            matches = [p for p in self.edits if p['origin']['source_path'] == source
                       and any(alias.count(text) == 1 for alias in p['origin']['aliases'])]
        exact = [p for p in matches if p["origin"]["paragraph_index"] == context.get("paragraph_index", row.get("paragraph_index"))]
        if exact: matches = exact
        if len(matches) != 1:
            return row
        payload = matches[0]
        key = context.get("key")
        if not key:
            key = fingerprint(source, payload["origin"]["paragraph_index"], text,
                              row.get("item_start", -1), row.get("item_end", row.get("item_start", -1) + len(row.get("answer", ""))))
        candidates = [i for i in payload["items"] if i.get("learning_context", {}).get("key") == key]
        if not candidates:
            # Legacy sentence-only examples have no paragraph offsets. A unique
            # unchanged original answer is safe; repeated occurrences are not guessed.
            candidates = [i for i in payload["items"] if i["normalized_answer"] == row.get("normalized_answer", normalize_answer(row.get("answer", "")))
                          and i.get("learning_context", {}).get("base_text") == text
                          and i.get("learning_context", {}).get("start") == row.get("item_start")]
        if not candidates and sentence_only and text:
            positions = {(alias, alias.find(text)+row.get('item_start', -1))
                         for alias in payload['origin']['aliases'] if alias.count(text)==1}
            candidates = [i for i in payload['items'] if i['normalized_answer']==normalize_answer(row.get('answer',''))
                          and (i.get('learning_context',{}).get('base_text'),i.get('learning_context',{}).get('start')) in positions]
        if not candidates and not payload['origin'].get('complete_targets', True):
            # A historical snapshot knows only its wrong targets. Unmentioned
            # targets are not cancellations; map their unchanged English spans.
            start = row.get('item_start', text.find(row.get('answer', '')))
            answer = row.get('answer', '')
            if start < 0 or text[start:start+len(answer)] != answer: return row
            left, right = sentence_bounds(text, start, start+len(answer))
            item = WordItem('partial', answer, normalize_answer(answer), row.get('chinese',''),
                text[left:right], text, row.get('source_article',''), source, payload['origin']['paragraph_index'],
                start, start+len(answer), left, right, learning_context=context)
            temporary = prepare_article(Article(source, item.filename, [Paragraph(item.paragraph_index,text,[])], [item]))
            edit_paragraph(temporary,item.paragraph_index,payload['paragraph']['text'])
            item = temporary.word_items[0]
            row.update(row_for(item)); row['_learning_changed'] = True
            return row
        if len(candidates) != 1 or candidates[0]["normalized_answer"] != normalize_answer(row.get("answer", "")):
            return None
        item = candidates[0]
        row.update({"answer": item["answer"], "chinese": item["chinese"], "sentence": item["sentence"],
            "paragraph_text": item["paragraph_text"], "paragraph_index": payload["origin"]["paragraph_index"],
            "item_start": item["start"], "item_end": item["end"], "sentence_start": item["sentence_start"],
            "sentence_end": item["sentence_end"], "learning_context": json.dumps(item["learning_context"], ensure_ascii=False),
            "_learning_changed": True})
        return row

    def full_article(self, article):
        article = prepare_article(article)
        changed = False
        for n, p in enumerate(article.paragraphs):
            origin = article.metadata["learning_paragraphs"][str(p.index)]
            matches = [e for e in self.edits if e["origin"]["source_path"] == origin["source_path"]
                       and (e["origin"]["key"] == origin["key"] or p.text in e["origin"]["aliases"])]
            exact = [e for e in matches if e['origin']['paragraph_index'] == origin['paragraph_index']]
            if exact: matches = exact
            if len(matches) != 1: continue
            payload = deepcopy(matches[0]); data = payload["paragraph"]
            if not payload['origin'].get('complete_targets', True):
                for raw in payload['items']:
                    context = raw.get('learning_context', {})
                    matching = [i for i in article.word_items if i.paragraph_index == p.index
                                and context_for(i)['key'] == context.get('key')]
                    if not matching:
                        matching = [i for i in article.word_items if i.paragraph_index == p.index
                                    and i.normalized_answer == raw['normalized_answer']
                                    and context_for(i).get('start') == context.get('start')]
                    if len(matching) == 1:
                        edit_hint(article, matching[0].id, raw['chinese'], context.get('note', ''))
                origin = payload['origin']
                origin['complete_targets'] = not article.metadata.get('learning_partial', False)
                article.metadata['learning_paragraphs'][str(p.index)] = origin
                changed = True
                continue
            article.paragraphs[n] = Paragraph(p.index, data["text"], [], data.get("alignment"), data.get("style_name"))
            article.word_items[:] = [i for i in article.word_items if i.paragraph_index != p.index]
            for raw in payload["items"]:
                raw["style"] = RunStyle(**raw.get("style", {})); raw["paragraph_index"] = p.index
                article.word_items.append(WordItem(**raw))
            article.metadata["learning_paragraphs"][str(p.index)] = payload["origin"]
            _refresh(article, p.index); changed = True
        if changed: article.metadata["learning_edited"] = True
        return article

    def session(self, session):
        rows = [(i, self.resolve(row_for(i))) for i in session.article.word_items]
        if not any(r is None or r.get("_learning_changed") for _, r in rows):
            return session
        article = deepcopy(session.article)
        article.word_items = []; article.paragraphs = []
        groups = {}
        for original, row in rows:
            if row is None: continue
            key = (row["source_path"], row["paragraph_index"], row["paragraph_text"])
            if key not in groups:
                index = groups[key] = len(groups)
                article.paragraphs.append(Paragraph(index, row["paragraph_text"], []))
            item = deepcopy(original); item.paragraph_index = groups[key]
            for field, column in (("answer", "answer"), ("chinese", "chinese"), ("sentence", "sentence"),
                                  ("paragraph_text", "paragraph_text"), ("start", "item_start"), ("end", "item_end"),
                                  ("sentence_start", "sentence_start"), ("sentence_end", "sentence_end")):
                setattr(item, field, row[column])
            item.learning_context = json.loads(row["learning_context"])
            article.word_items.append(item)
        article.metadata.pop("learning_paragraphs", None)
        article.metadata['learning_revision_display'] = True
        article = prepare_article(article)
        for p in article.paragraphs: rebuild_segments(article, p.index)
        session.article = article
        session.target_ids = [i for i in session.target_ids if i in article.item_map]
        if not session.target_ids:
            raise ValueError("这些旧题已取消挖空或更换目标词，暂无可用题目。请在“编辑学习内容”中检查，或从修正后的文章开始新练习。")
        return session
