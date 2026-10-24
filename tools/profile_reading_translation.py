"""Offscreen, synthetic reading profile. Never opens normal learning data."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from docx import Document
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    import psutil
    from src.database.repository import Repository
    from src.ui.main_window import MainWindow
    from src.ui.widgets.text_menus import TextLabel, plain_label_text, text_menu
    from src.ui.widgets.reading_selection import units
    app = QApplication.instance() or QApplication([]); app.setStyle('Fusion')
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if path.exists(): QFontDatabase.addApplicationFont(str(path))
    process = psutil.Process()
    with tempfile.TemporaryDirectory(prefix='EnglishDictationTranslationProfile_') as folder:
        root = Path(folder); document = Document()
        for text in ('I was utterly exhausted.', 'Please do not spill the beans.',
                     'Her goal was to outdo her prior self.', 'They commute to work.',
                     'The bank will charge a substantial fee.', 'We need sustainable development.'):
            document.add_paragraph(text)
        path = root / 'synthetic.docx'; document.save(path)
        window = MainWindow(Repository(root / 'test.db')); window.show()
        window._article_switch_delay = 0; window._import_paths([str(path)])
        app.processEvents(); QTest.qWait(200)
        before = process.memory_info().rss
        private_before = getattr(process.memory_info(), 'private', None)
        assert window._reading_translation is None
        assert 'src.services.dictionary_lookup' not in sys.modules
        assert 'src.ui.widgets.translation_popup' not in sys.modules
        assert 'PySide6.QtNetwork' not in sys.modules
        times = []
        for word in ('utterly', 'spill the beans', 'outdo', 'commute', 'bank', 'charge', 'substantial', 'sustainable', 'utterly'):
            window.reading_selection.clear()
            label = next(w for w in window.content.findChildren(TextLabel)
                         if w.isVisibleTo(window.content) and word in plain_label_text(w))
            text = plain_label_text(label); start = text.index(word)
            label.setSelection(units(text[:start]), units(word))
            started = time.perf_counter(); menu = text_menu(label)
            next(a for a in menu.actions() if a.text() == '翻译').trigger(); menu.deleteLater()
            deadline = time.monotonic() + 3
            while not window._reading_translation.popup.lookup_done and time.monotonic() < deadline:
                app.processEvents(); QTest.qWait(1)
            assert window._reading_translation.popup.lookup_done
            times.append(round((time.perf_counter() - started) * 1000, 3))
        controller = window._reading_translation
        assert controller.network is None and not controller.dictionary._jobs
        after = process.memory_info().rss
        private_after = getattr(process.memory_info(), 'private', None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        controller.popup.grab().save(str(args.output.with_suffix('.png')))
        for _ in range(30):
            window.show_preview(); app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert len(window.findChildren(type(controller.popup))) == 1
        assert controller.network is None
        result = {'scope': 'Qt offscreen, synthetic text; not desktop frame-rate proof',
                  'startup_translation_modules_absent': True, 'startup_network_module_absent': True,
                  'rss_before_lookup_bytes': before, 'rss_after_lookup_bytes': after,
                  'rss_delta_lookup_bytes': after-before, 'lookup_to_popup_ms': times,
                  'private_before_lookup_bytes': private_before, 'private_after_lookup_bytes': private_after,
                  'private_delta_lookup_bytes': private_after-private_before if private_before is not None else None,
                  'first_lookup_including_lazy_import_ms': times[0], 'cached_lookup_ms': times[-1],
                  'warm_lookup_median_ms': statistics.median(times[1:-1]),
                  'local_queries': controller.dictionary.lookups, 'network_requests': 0,
                  'cache_entries': len(controller.dictionary._cache),
                  'remaining_popups_after_30_page_changes': 1,
                  'normal_learning_database_opened': False}
        window.close(); window.deleteLater(); app.processEvents()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
