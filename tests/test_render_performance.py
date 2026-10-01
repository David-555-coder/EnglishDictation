from PySide6.QtCore import QRect
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from test_library_ui import ui
from test_daily_review import seed
from src.database.repository import Repository
from src.ui.widgets.flow_layout import FlowLayout


def test_repeated_height_queries_do_not_remeasure_unchanged_items(ui, monkeypatch):
    parent = QWidget()
    flow = FlowLayout(parent, h_spacing=2)
    for _ in range(3):
        label = QLabel('word'); label.setFixedSize(60, 20); flow.addWidget(label)
    calls = []
    for i in range(flow.count()):
        item = flow.itemAt(i); original = item.sizeHint
        monkeypatch.setattr(item, 'sizeHint', lambda original=original: calls.append(1) or original())
    height = flow.heightForWidth(130)
    count = len(calls)
    assert count > 0
    assert flow.heightForWidth(130) == height
    assert len(calls) == count
    assert flow.heightForWidth(70) > height
    assert len(calls) > count
    parent.close()


def test_cached_height_tracks_text_font_visibility_and_item_changes(ui):
    parent = QWidget()
    flow = FlowLayout(parent, margin=3, h_spacing=2)
    first = QLabel('short'); second = QLabel('another word')
    flow.addWidget(first); flow.addWidget(second)
    parent.show(); QApplication.processEvents()

    def check():
        # A fresh layout calculation must agree after each real widget mutation.
        assert flow.heightForWidth(100) == flow._do_layout(QRect(0, 0, 100, 0), True)

    check()
    first.setText('a substantially longer text that wraps'); check()
    font = QFont(first.font()); font.setPixelSize(32); first.setFont(font); check()
    second.hide(); check()
    second.show(); check()
    first.setProperty('preferredFlowWidth', 80); flow.invalidate(); check()
    second.setProperty('flowBreak', True); flow.invalidate(); check()
    companion = QLabel('new companion', parent)
    flow.insertWidgetAfter(first, companion); check()
    removed = flow.takeAt(1); removed.widget().setParent(None); check()
    removed.widget().deleteLater()
    parent.close(); parent.deleteLater()


def test_review_lookup_index_preserves_records_and_survives_reinitialization(tmp_path):
    path = tmp_path / 'db'
    repo = Repository(path); seed(repo, 3)
    with repo.connection() as conn:
        session = conn.execute('''INSERT INTO practice_sessions
            (submitted_at,article_name,source_path,practice_type,target_count,
             correct_count,wrong_count,accuracy)
            VALUES ('2026-09-14T12:34:56','sample.docx','sample.docx','整篇文章',1,0,1,0)''').lastrowid
        conn.execute('''INSERT INTO practice_answers
            (session_id,item_id,answer,normalized_answer,user_answer,is_correct,is_blank,sentence)
            VALUES (?, 'item0', 'word0', 'word0', 'wrong', 0, 0, 'Use word0 here.')''', (session,))
    ids = [row['id'] for row in repo.list_wrong_words()]
    before_examples = repo.get_review_examples(ids)
    def contents(repository):
        with repository.connection() as conn:
            names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: [tuple(row) for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                    for name in names}
    before = contents(repo)
    reopened = Repository(path)
    assert contents(reopened) == before
    assert reopened.get_review_examples(ids) == before_examples
    with reopened.connection() as conn:
        assert [row[2] for row in conn.execute('PRAGMA index_info(practice_answers_review_lookup)')] == [
            'normalized_answer', 'is_correct', 'id']
        plan = conn.execute('''EXPLAIN QUERY PLAN SELECT a.*, s.source_path
            FROM practice_answers a JOIN practice_sessions s ON s.id=a.session_id
            WHERE a.normalized_answer=? AND a.is_correct=0 ORDER BY a.id DESC LIMIT 1''', ('word0',)).fetchall()
        assert any('USING INDEX practice_answers_review_lookup' in row[3] for row in plan)
    assert contents(Repository(path)) == before
