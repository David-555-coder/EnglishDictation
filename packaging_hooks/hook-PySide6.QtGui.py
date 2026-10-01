"""This Widgets-only app does not render PDFs or use Qt's QML virtual keyboard.

Filter their entry plugins before dependency analysis, not their DLLs afterwards.
Keep Windows IME, platform plugins and the software OpenGL fallback untouched.
"""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
unused_plugins = {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}
binaries = [(source, destination) for source, destination in binaries
            if Path(source).name.casefold() not in unused_plugins]
