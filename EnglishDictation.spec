# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import PySide6

icon_file = Path(SPECPATH) / 'assets' / 'english_dictation.ico'

a = Analysis(
    ['app.py'], pathex=[], binaries=[], datas=[(str(icon_file), 'assets')],
    hiddenimports=['pythoncom', 'pywintypes', 'win32com', 'win32com.client'],
    hookspath=[str(Path(SPECPATH) / 'packaging_hooks')], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)

# 某些 Windows 环境会优先加载错误版本的 VC Runtime；显式使用 PySide6 自带版本。
pyside_dir = Path(PySide6.__file__).resolve().parent
runtime_names = {'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
                 'vcruntime140.dll', 'vcruntime140_1.dll'}
filtered = []
for destination, source, kind in a.binaries:
    if Path(destination).name.casefold() not in runtime_names:
        filtered.append((destination, source, kind))
for name in runtime_names:
    source = pyside_dir / name
    if source.exists():
        filtered.append((name, str(source), 'BINARY'))
a.binaries = filtered

# Keep English and Chinese Qt translations; user documents are never bundle data.
def required_language(entry):
    name = entry[0].replace('\\', '/').lower()
    return '/translations/' not in name or name.endswith(('_en.qm', '_zh_cn.qm', '_zh_tw.qm'))

a.binaries = [entry for entry in a.binaries if required_language(entry)]
a.datas = [entry for entry in a.datas if required_language(entry)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='EnglishDictation',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          upx_exclude=[], runtime_tmpdir=None, console=False,
          disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          icon=str(icon_file))
