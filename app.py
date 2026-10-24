from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path


def application_directory() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def resolve_storage_root() -> Path:
    candidates = [application_directory()]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "EnglishDictation")
    for candidate in candidates:
        try:
            (candidate / "data").mkdir(parents=True, exist_ok=True)
            (candidate / "logs").mkdir(parents=True, exist_ok=True)
            probe = candidate / "data" / ".write_access_test.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    raise OSError("程序目录和用户数据目录均不可写")


def configure_logging(root: Path) -> None:
    logging.basicConfig(handlers=[RotatingFileHandler(root / "logs" / "app.log", maxBytes=1024*1024,
                                                     backupCount=3, encoding="utf-8")], level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--verify-release":
        # Isolated packaged-app QA: this branch never opens normal learning data.
        from src.services.release_check import run
        return run(Path(sys.argv[2]).resolve())
    root = resolve_storage_root()
    configure_logging(root)
    try:
        from PySide6.QtGui import QColor, QIcon, QPalette
        from PySide6.QtCore import QLockFile, QProcess
        from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory
        from src.database.repository import Repository
        from src.ui.main_window import MainWindow
        from src.services.backup_restore import recover_interrupted_restore, restore_library

        app = QApplication(sys.argv)
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        icon_path = resource_root / "assets" / "english_dictation.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
        instance_lock = QLockFile(str(root / "data" / "application.lock"))
        if not instance_lock.tryLock(100):
            QMessageBox.information(None, "程序已经运行", "已有一个窗口正在使用学习数据，请切换到原窗口。")
            return 0
        app.setApplicationName("英文文章智能默写")
        app.setStyle(QStyleFactory.create("Fusion"))
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#f5f7fb"))
        palette.setColor(QPalette.Base, QColor("#ffffff"))
        palette.setColor(QPalette.Highlight, QColor("#2e7dcc"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
        database_path = root / "data" / "english_dictation.db"
        if recover_interrupted_restore(database_path):
            logging.warning("已回退上次中断的恢复，原学习数据已恢复")
        repository = Repository(database_path)
        window = MainWindow(repository)
        window.show()
        logging.info("主窗口启动完成")
        restart = False
        try:
            result = app.exec()
            prepared = window.pending_library_restore
            if prepared is not None:
                try:
                    safety = restore_library(repository, prepared)
                    logging.info("恢复学习备份完成，恢复前备份：%s", safety)
                    QMessageBox.information(None, "恢复完成", f"学习备份已恢复，软件即将重新启动。\n恢复前的数据已保存在：\n{safety}")
                    restart = True
                except Exception as exc:
                    logging.exception("恢复学习备份失败")
                    QMessageBox.critical(None, "恢复失败", f"{exc}\n\n已停止恢复。请重新打开软件；恢复前备份保存在 backups/recovery。")
                finally:
                    prepared.close()
        finally:
            instance_lock.unlock()
        if restart:
            arguments = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve())]
            started, _ = QProcess.startDetached(sys.executable, arguments, str(application_directory()))
            if not started:
                QMessageBox.information(None, "请重新打开软件", "数据已恢复，请手动重新打开软件。")
        return result
    except Exception as exc:
        logging.exception("程序启动失败")
        try:
            QMessageBox.critical(None, "启动失败", f"程序无法启动：\n{exc}\n\n详情见 logs/app.log")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
