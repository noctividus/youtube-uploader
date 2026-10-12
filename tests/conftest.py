import os
import gc

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vmix_youtube_uploader.database import Database


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "queue.sqlite3")
    yield db
    db.close()


@pytest.fixture(scope="session")
def app():
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    yield application
    application.closeAllWindows()
    application.processEvents()
    for widget in application.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    application.processEvents()
