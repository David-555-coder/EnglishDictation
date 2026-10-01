import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.models.rules import RecognitionRules
from src.ui.editing_dialogs import RulesDialog


def test_recognition_dialog_exposes_exactly_three_presets():
    app = QApplication.instance() or QApplication([])
    dialog = RulesDialog(RecognitionRules())
    assert [dialog.mode.itemData(index) for index in range(dialog.mode.count())] == [
        "standard", "lenient", "strict"]

    dialog.mode.setCurrentIndex(dialog.mode.findData("standard"))
    assert dialog.rules() == RecognitionRules("red_or_bold", True, True)
    dialog.mode.setCurrentIndex(dialog.mode.findData("lenient"))
    assert dialog.rules() == RecognitionRules("any", True, True)
    dialog.mode.setCurrentIndex(dialog.mode.findData("strict"))
    assert dialog.rules() == RecognitionRules("red_and_bold", False, True)
    dialog.close()
