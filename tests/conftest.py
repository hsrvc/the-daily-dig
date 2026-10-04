import sys
from pathlib import Path
import pytest

# make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

@pytest.fixture
def tmp_db(tmp_path):
    """Return a connected, schema-initialized SQLite DB in a temp directory."""
    from db import connect, init_schema
    db_file = tmp_path / "test.db"
    conn = connect(db_file)
    init_schema(conn)
    return conn, db_file
