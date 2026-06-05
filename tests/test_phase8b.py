# tests/test_phase8b.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from signals.registry import SIGNAL_REGISTRY, get_signal_ids, populate_registry_to_db
from database import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()

def test_registry_has_32_signals():
    assert len(SIGNAL_REGISTRY) == 32

def test_registry_signal_ids_unique():
    ids = [s["id"] for s in SIGNAL_REGISTRY]
    assert len(ids) == len(set(ids)), "Signal IDs must be unique"

def test_registry_all_have_required_fields():
    required = {"id", "name", "category", "update_freq", "source"}
    for sig in SIGNAL_REGISTRY:
        missing = required - sig.keys()
        assert not missing, f"Signal {sig.get('id')} missing fields: {missing}"

def test_registry_categories():
    cats = {s["category"] for s in SIGNAL_REGISTRY}
    assert cats == {"TECHNICAL", "ON_CHAIN", "SENTIMENT", "DERIVATIVES", "MACRO"}

def test_registry_technical_count():
    tech = [s for s in SIGNAL_REGISTRY if s["category"] == "TECHNICAL"]
    assert len(tech) == 10

def test_registry_onchain_count():
    oc = [s for s in SIGNAL_REGISTRY if s["category"] == "ON_CHAIN"]
    assert len(oc) == 7

def test_registry_sentiment_count():
    sent = [s for s in SIGNAL_REGISTRY if s["category"] == "SENTIMENT"]
    assert len(sent) == 6

def test_registry_derivatives_count():
    der = [s for s in SIGNAL_REGISTRY if s["category"] == "DERIVATIVES"]
    assert len(der) == 4

def test_registry_macro_count():
    macro = [s for s in SIGNAL_REGISTRY if s["category"] == "MACRO"]
    assert len(macro) == 5

def test_get_signal_ids_returns_32_strings():
    ids = get_signal_ids()
    assert len(ids) == 32
    assert all(isinstance(i, str) for i in ids)

def test_populate_registry_to_db_inserts_rows(db):
    populate_registry_to_db(db)
    count = db.conn.execute("SELECT COUNT(*) FROM signal_registry").fetchone()[0]
    assert count == 32

def test_registry_enabled_by_default():
    for sig in SIGNAL_REGISTRY:
        assert sig.get("enabled", True) is True
