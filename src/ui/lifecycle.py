"""Release parent-owned modal widgets after their result has been consumed."""
from contextlib import contextmanager


@contextmanager
def owned_dialog(dialog):
    try:
        yield dialog
    finally:
        dialog.deleteLater()
