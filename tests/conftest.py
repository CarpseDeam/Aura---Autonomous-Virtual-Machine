import pytest
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication


# Ensure project root is importable so 'src.aura...' works when running pytest from repo root
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(scope="session", autouse=True)
def qcore_app():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    yield app
