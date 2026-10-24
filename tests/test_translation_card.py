"""Compact card and temporary outside-dismiss listener regression checks."""
from copy import deepcopy

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from test_reading_translation import APP, reader, translate
from src.services.dictionary_lookup import Entry, Lookup
from src.services.reading_translation import Query
from src.ui.widgets.text_menus import text_menu


def test_short_card_is_narrower_and_source_is_not_repeated(reader):
    popup = translate(reader, 'utterly').popup
    assert 320 <= popup.width() <= 360 and popup.height() <= 210
    assert popup.notice.text() == 'ECDICT'
    assert not hasattr(popup, 'badge')


def test_first_outside_click_dismisses_and_still_activates_the_button(reader):
    popup = translate(reader, 'utterly').popup
    button = QPushButton('outside', reader); button.setGeometry(10, 10, 80, 30); button.show()
    clicks = []; button.clicked.connect(lambda: clicks.append(True))
    assert popup._outside_filter_installed
    QTest.mouseClick(button, Qt.LeftButton)
    assert clicks == [True] and not popup.isVisible() and not popup._outside_filter_installed


def test_click_copy_inside_keeps_card_open_and_preserves_full_gloss(reader):
    popup = translate(reader, 'utterly').popup
    expected = popup._copy_text
    QTest.mouseClick(popup.copy, Qt.LeftButton)
    assert APP.clipboard().text() == expected and popup.isVisible()
    assert popup._outside_filter_installed


def test_owned_context_menu_outside_card_is_not_an_outside_reading_click(reader):
    popup = translate(reader, 'utterly').popup
    popup.result.selectAll(); expected = popup.result.textCursor().selectedText()
    menu = text_menu(popup.result)
    copy = next(action for action in menu.actions() if action.text() == '复制')
    menu.popup(popup.mapToGlobal(QPoint(12, popup.height() + 8))); APP.processEvents()
    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(copy).center())
    assert popup.isVisible() and APP.clipboard().text().replace('\n', '\u2029') == expected
    menu.close(); menu.deleteLater()


def test_mouse_movement_outside_does_not_dismiss(reader):
    popup = translate(reader, 'utterly').popup
    QTest.mouseMove(reader, QPoint(5, 5))
    assert popup.isVisible()


def test_deactivate_and_reopen_attach_listener_only_while_shown(reader):
    popup = translate(reader, 'utterly').popup
    APP.sendEvent(APP, QEvent(QEvent.ApplicationDeactivate))
    assert not popup.isVisible() and not popup._outside_filter_installed
    for _ in range(8):
        popup = translate(reader, 'utterly').popup
        assert popup._outside_filter_installed
        popup.close(); assert not popup._outside_filter_installed


def test_online_copy_never_changes_local_copy_target(reader):
    popup = translate(reader, 'utterly').popup
    original = popup._copy_text
    popup.show_online(Query('utterly', 'I was utterly tired.', True), 'done', '我累极了。')
    QTest.mouseClick(popup.copy, Qt.LeftButton)
    assert APP.clipboard().text() == original and popup.copy.text() == '复制释义'
    QTest.mouseClick(popup.net_copy, Qt.LeftButton)
    assert APP.clipboard().text() == '我累极了。' and popup.net_copy.text() == '复制译文'
    assert popup._copy_text == original and popup.isVisible()


def test_inflection_note_moves_to_meta_but_full_copy_is_unchanged(reader):
    popup = translate(reader, 'utterly').popup
    original = 'v. 漂洗( rinse的过去式和过去分词 ); 冲洗；用清水漂洗'
    result = Lookup('rinsed', (Entry('rinsed', 'rinst', original),), 'exact')
    popup.present(Query('rinsed', 'rinsed'), QPoint(100, 100)); popup.show_lookup(result)
    assert 'rinse · 过去式／过去分词' in popup.meta.text()
    assert '过去式' not in popup.result.toPlainText()
    assert popup._copy_text == original and result.entries[0].translation == original


def test_uncertain_inflection_description_is_not_rewritten(reader):
    popup = translate(reader, 'utterly').popup
    original = 'v. 漂洗（rinse的其他变化形式）'
    popup.show_lookup(Lookup('rinsed', (Entry('rinsed', '', original),), 'exact'))
    assert popup.result.toPlainText() == original and popup._copy_text == original


def test_single_long_line_has_expand_action_and_bounded_height(reader):
    popup = translate(reader, 'utterly').popup
    original = '很长的释义内容，' * 45
    popup.show_lookup(Lookup('long', (Entry('long', '', original),), 'exact'))
    assert popup.more.isVisible() and popup.result.height() <= 84
    assert popup.result.toPlainText().endswith('…')
    QTest.mouseClick(popup.more, Qt.LeftButton)
    assert popup._expanded and popup.result.height() <= 208 and popup.isVisible()
    assert popup._copy_text == original


def test_online_section_background_is_white_not_inherited_grey(reader):
    popup = translate(reader, 'utterly').popup
    popup.show_online(Query('utterly', 'I was utterly tired.', True), 'done', '我累极了。')
    APP.processEvents()
    point = popup.network.mapTo(popup, QPoint(popup.network.width() - 2, popup.network.height() // 2))
    assert popup.grab().toImage().pixelColor(point).name() == '#ffffff'


def test_network_updates_do_not_clear_local_text_selection(reader):
    popup=translate(reader,'utterly').popup
    popup.result.selectAll();selected=popup.result.textCursor().selectedText()
    popup.show_online(Query('utterly','I was utterly tired.',True),'loading')
    popup.show_online(Query('utterly','I was utterly tired.',True),'done','我累极了。')
    assert popup.result.textCursor().selectedText()==selected


def test_online_short_result_does_not_leave_stale_tall_layout(reader):
    popup=translate(reader,'utterly').popup
    popup.show_online(Query('utterly','I was utterly tired.',True),'done','我累极了。')
    assert popup.height()<=300


def test_long_selection_is_allowed_more_width_but_stays_on_screen(reader):
    popup = translate(reader, 'utterly').popup
    text = 'a somewhat longer phrase selected from the original sentence'
    popup.present(Query(text, text), QPoint(99999, 99999))
    popup.show_lookup(Lookup(text, (Entry(text, '', '比较长的词组释义。'),), 'exact'))
    assert 360 < popup.width() <= 400
    assert popup.screen().availableGeometry().contains(popup.frameGeometry())


def test_outside_click_cancels_request_without_mutating_learning_state(reader):
    controller = translate(reader, 'utterly')
    before = deepcopy(reader.repository.list_wrong_words())
    QTest.mouseClick(reader.content, Qt.LeftButton, pos=QPoint(3, 3))
    assert controller._lookup_token is None and controller._network_token is None
    assert reader.repository.list_wrong_words() == before
    assert not controller.popup.isVisible()
