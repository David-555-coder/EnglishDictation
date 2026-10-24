"""Lazy reading-only adapter. No repository, grading or scheduler dependency."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QKeySequence, QShortcut

from src.services.dictionary_lookup import DictionaryClient
from src.services.reading_translation import TranslationClient, TranslationError, prepare_query
from src.ui.widgets.translation_popup import TranslationPopup


class ReadingTranslation(QObject):
    def __init__(self, window, allowed, dictionary=None, network=None):
        super().__init__(window)
        self.window, self.allowed = window, allowed
        self.dictionary = dictionary or DictionaryClient(self)
        self.network = network  # Even the client is absent until an online action.
        self.popup = TranslationPopup(self, window)
        self.query = self.online_query = self.lookup = None
        self._lookup_token = self._network_token = None
        self._online_allowed = False  # Consent is session-only, never in learning storage.
        self._generation = None
        self.dictionary.completed.connect(self._lookup_completed)
        if self.network is not None:
            self.network.completed.connect(self._network_completed)
        self.escape = QShortcut(QKeySequence('Escape'), window)
        self.escape.setEnabled(False)
        self.escape.activated.connect(self.dismiss)

    def _current(self):
        return (self.allowed() and self.popup.isVisible()
                and self._generation == self.window._content_generation)

    def show_selection(self, selection, point):
        if not self.allowed():
            return
        document, start, end = selection.selected_context()
        try:
            query = prepare_query(document, start, end, False)
            online = prepare_query(document, start, end, True)
        except TranslationError as error:
            self.window.statusBar().showMessage(str(error), 6000)
            return
        self.cancel_queries()
        self.query, self.online_query, self.lookup = query, online, None
        self._generation = self.window._content_generation
        self.popup.present(query, point)
        self.escape.setEnabled(True)
        self._lookup_token = self.dictionary.request(query.selected)

    def cancel_queries(self):
        self._lookup_token = self._network_token = None
        self.dictionary.cancel()
        if self.network is not None:
            self.network.cancel()
        self.escape.setEnabled(False)

    def dismiss(self):
        self.cancel_queries()
        self.popup.hide()

    def _lookup_completed(self, token, result, error, cached):
        if token != self._lookup_token or not self._current():
            return
        self.lookup = result
        self.popup.show_lookup(result, error)

    def online_action(self):
        if not self.query or not self._current():
            return
        state = self.popup.net_state
        if state == 'done':
            self.popup.network.hide()
            self.popup.net_state = 'closed'
            self.popup.retry.setText('联网参考')
            if self.lookup is not None:
                self.popup.show_lookup(self.lookup)
            self.popup._fit()
            return
        if not self._online_allowed and state != 'consent':
            self.popup.show_online(self.online_query, 'consent')
            return
        self._online_allowed = True
        if self.network is None:
            self.network = TranslationClient(self)
            self.network.completed.connect(self._network_completed)
        self.popup.show_online(self.online_query, 'loading')
        self._network_token = self.network.request(self.online_query.text)

    def _network_completed(self, token, text, error, cached):
        if token != self._network_token or not self._current():
            return
        self.popup.show_online(self.online_query, 'error' if error else 'done', error or text)
