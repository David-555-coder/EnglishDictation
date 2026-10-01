import threading
from PySide6.QtCore import QThread, Signal


class ImportWorker(QThread):
    articleReady = Signal(object)
    progressChanged = Signal(int, str)
    failed = Signal(str, str)

    def __init__(self, library, paths, rules, parent=None):
        super().__init__(parent)
        self.library, self.paths, self.rules = library, paths, rules
        self.canceled = threading.Event()

    def cancel(self):
        self.canceled.set()

    def run(self):
        for index, path in enumerate(self.paths):
            if self.canceled.is_set(): break
            self.progressChanged.emit(index, str(path))
            try: self.articleReady.emit(self.library.import_file(path, self.rules))
            except Exception as exc: self.failed.emit(str(path), str(exc))
        self.progressChanged.emit(len(self.paths), "")
