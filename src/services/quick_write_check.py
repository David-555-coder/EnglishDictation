"""Isolated clipboard checks, not third-party AI or Word-generation validation."""


def verify(root, app, output):
    from PySide6.QtCore import QCoreApplication, QEvent
    from src.database.repository import Repository
    from src.services.quick_write import copy_text, load_quick_write, save_quick_write
    from src.services.quick_write_defaults import DEFAULT_STEPS
    from src.ui.main_window import MainWindow
    from src.ui.quick_write_dialogs import QuickWriteEditor, QuickWritePanel

    folder = root / 'quick-write'; folder.mkdir()
    repo = Repository(folder / 'data' / 'copy.db')
    window = MainWindow(repo); window.show()
    config = load_quick_write(repo)
    first, second = [s['prompt'] for s in config['steps']]
    assert config['filename'] == '' and len(config['steps']) == 2
    assert '无论目标词数量多少，只生成一个英文标题、一篇完整故事' in first
    assert 'happy≠happily' in first and 'take 和 take part in' in first
    assert '重新读取文件并核查' in second
    assert second.count('{{文件名}}') == 1 and 'djci.docx' not in second
    clipboard = app.clipboard().text()
    panel = QuickWritePanel(repo, window)
    try:
        panel.copy_step(0)
        assert app.clipboard().text() == DEFAULT_STEPS[0]['prompt']
        panel.copy_step(1)
        assert '未指定；请根据文章标题命名' in app.clipboard().text()
        for name in ('我的英语故事', '我的英语故事.docx', '我的英语故事.DOCX'):
            panel.filename.setText(name); panel.copy_step(1)
            assert panel.filename.text() == '我的英语故事'
            assert app.clipboard().text().count('我的英语故事.docx') == 1
            assert '{{文件名}}' not in app.clipboard().text()
        panel.show(); app.processEvents()
        panel.grab().save(str(output.with_name(output.stem + '-quick-write.png')))
    finally:
        app.clipboard().setText(clipboard)
        panel.close(); panel.deleteLater()
    editor = QuickWriteEditor(repo, window)
    editor.name_edit.setText('自定义第一轮')
    editor.save()
    assert load_quick_write(repo)['filename'] == '我的英语故事'
    assert load_quick_write(repo)['steps'][0]['name'] == '自定义第一轮'
    editor.deleteLater()
    custom = {'filename': '', 'steps': [{'name': '自定义第三轮', 'prompt': '复核 {{文件名}}'}]}
    save_quick_write(repo, custom)
    assert load_quick_write(repo) == custom
    assert copy_text(custom['steps'][0]['prompt'], '自选') == '复核 自选.docx'
    window.close(); window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete); app.processEvents()
    return ['two_step_prompt_version', 'quick_write_filename_optional', 'quick_write_filename_single_source',
            'quick_write_filename_extension_once', 'quick_write_independent_round_copy',
            'quick_write_editor_keeps_filename', 'quick_write_custom_steps_preserved']
