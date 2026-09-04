"""Fixtures des tests memory.

REGLE ABSOLUE : aucun test ne doit toucher la base reelle
(`server/memory/neron_memory.db`). C'est la memoire persistante de
l'utilisateur — une ecriture accidentelle y serait irreversible. Toutes les
fixtures d'ici construisent une base neuve dans un repertoire temporaire.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory.oblivia.manager import ObliviaMemoryManager
from memory.oblivia.schemas import MemoryRecord
from memory.oblivia.sqlite_adapter import SQLiteMemoryAdapter

REAL_DB = Path(__file__).resolve().parents[1] / "neron_memory.db"


@pytest.fixture
def db_path(tmp_path) -> Path:
    """Chemin d'une base jetable, garanti distinct de la base reelle."""
    path = tmp_path / "test_memory.db"
    assert path != REAL_DB
    return path


@pytest.fixture
def adapter(db_path) -> SQLiteMemoryAdapter:
    return SQLiteMemoryAdapter(db_path)


@pytest.fixture
def manager(tmp_path, db_path) -> ObliviaMemoryManager:
    return ObliviaMemoryManager(
        sqlite_path=str(db_path),
        obsidian_path=str(tmp_path / "obsidian"),
    )


@pytest.fixture
def record() -> MemoryRecord:
    return MemoryRecord(content="Je m'appelle Eleazar.", source="test")


def test_fixtures_never_point_at_the_real_database(db_path):
    """Garde-fou de la garde-fou : si cette assertion tombe, tout le reste
    ecrit dans la memoire de l'utilisateur."""
    assert db_path != REAL_DB
    assert "tmp" in str(db_path) or "pytest" in str(db_path)
