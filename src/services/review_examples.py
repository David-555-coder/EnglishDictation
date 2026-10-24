"""Read examples with one bounded parsing context per synchronous transaction.

No process-wide cache: a subsequent call sees saved content edits immediately.
History order, cancelled-target fallback, and caller order are unchanged.
"""
import json

from src.services.learning_content import ContentResolver, row_for
from src.services.serialization import article_from_dict


class ReviewExampleLookup:
    def __init__(self, connection, words=()):
        self.connection = connection
        self.resolver = ContentResolver(connection)
        self.words = {row['id']: row for row in words}
        self._snapshots = None
        self._next_snapshot = 0
        self._fallback_items = {}

    def _fallback(self, normalized):
        # Parse only the same prefix the original first-match search would visit.
        # In particular, a corrupt later snapshot must not break an earlier match.
        if self._snapshots is None:
            self._snapshots = self.connection.execute(
                "SELECT snapshot_json FROM article_library WHERE snapshot_json<>''").fetchall()
        while normalized not in self._fallback_items and self._next_snapshot < len(self._snapshots):
            raw = self._snapshots[self._next_snapshot][0]
            article = article_from_dict(json.loads(raw))
            self._next_snapshot += 1
            for item in article.word_items:
                self._fallback_items.setdefault(item.normalized_answer, item)
        item = self._fallback_items.get(normalized)
        return row_for(item) if item is not None else None

    def get(self, word_ids):
        word_ids = list(word_ids)
        missing = list(dict.fromkeys(i for i in word_ids if i not in self.words))
        for offset in range(0, len(missing), 400):
            batch = missing[offset:offset+400]
            marks = ','.join('?' for _ in batch)
            self.words.update((r['id'], r) for r in self.connection.execute(
                f'SELECT * FROM wrong_words WHERE id IN ({marks})', batch))
        records = []
        for word_id in word_ids:
            word = self.words.get(word_id)
            if word is None:
                continue
            cursor = self.connection.execute("""SELECT a.*, s.source_path AS legacy_path, s.article_name AS legacy_name
                FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id
                WHERE a.normalized_answer=? AND a.is_correct=0 ORDER BY a.id DESC"""
                + ('' if self.resolver.edits else ' LIMIT 1'), (word['normalized_answer'],))
            data, had_history = None, False
            try:
                for row in cursor:
                    had_history = True
                    candidate = dict(row)
                    candidate['source_path'] = candidate['source_path'] or candidate['legacy_path']
                    candidate['source_article'] = candidate['source_article'] or candidate['legacy_name']
                    data = self.resolver.resolve(candidate)
                    if data is not None:
                        break
            finally:
                cursor.close()
            if data is None and not had_history:
                cursor = self.connection.execute(
                    'SELECT * FROM word_examples WHERE wrong_word_id=? ORDER BY id DESC', (word_id,))
                try:
                    for example in cursor:
                        candidate = dict(example)
                        candidate.update(answer=word['answer'], normalized_answer=word['normalized_answer'],
                                         chinese=word['chinese'], paragraph_text=example['sentence'],
                                         paragraph_index=-1, item_start=example['sentence'].find(word['answer']))
                        data = self.resolver.resolve(candidate)
                        if data is not None:
                            break
                finally:
                    cursor.close()
            if data is None and self.resolver.edits:
                data = self._fallback(word['normalized_answer'])
            if data is None:
                continue
            data['id'] = f'book-{word_id}'
            data['review_pool'] = word['review_pool']
            records.append(data)
        return records
