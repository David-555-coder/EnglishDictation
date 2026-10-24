"""Release parent-owned modal widgets after their result has been consumed."""
from contextlib import contextmanager


@contextmanager
def owned_dialog(dialog):
    from src.ui.widgets.text_menus import bind_text_menus
    bind_text_menus(dialog)
    try:
        yield dialog
    finally:
        dialog.deleteLater()
