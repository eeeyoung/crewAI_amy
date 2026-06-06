import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure src/ is on the import path
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def temp_db():
    """Create a temporary fact_store.db for isolated tests."""
    import amail.fact_store as fs

    old_path = fs.DB_PATH
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "fact_store.db")
        fs.DB_PATH = db_path
        fs.init_db()
        yield db_path
        fs.DB_PATH = old_path


@pytest.fixture
def sample_facts():
    return [
        {"project": "ARCO", "topic": "concrete pour", "detail": "Level 2 slab pour scheduled March 15"},
        {"project": "Econolodge", "topic": "RFI", "detail": "RFI-042 regarding window flashing detail"},
        {"project": "ARCO", "topic": "change order", "detail": "Client approved change order #3 for $12,400"},
    ]
