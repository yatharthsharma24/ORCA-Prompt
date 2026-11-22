from __future__ import annotations

from pathlib import Path

import pytest

from orca.config import ROOT, Settings
from orca.data import generate
from orca.data.store import MarineStore
from orca.evidence import EvidenceLedger
from orca.knowledge import KnowledgeBase
from orca.tools import ToolContext


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory) -> Path:
    return generate.build(tmp_path_factory.mktemp("db") / "orca_test.sqlite")


@pytest.fixture
def settings(demo_db, tmp_path) -> Settings:
    return Settings(
        data_db=demo_db,
        runs_db=tmp_path / "runs.sqlite",
        knowledge_dir=ROOT / "data" / "knowledge",
        enable_obis=False,
        llm_critic=False,
        _env_file=None,
    )


@pytest.fixture
def store(settings) -> MarineStore:
    return MarineStore(settings.data_db, row_limit=settings.sql_row_limit)


@pytest.fixture
def kb(settings) -> KnowledgeBase:
    return KnowledgeBase(settings.knowledge_dir)


@pytest.fixture
def ctx(store, kb, settings) -> ToolContext:
    return ToolContext(store=store, ledger=EvidenceLedger(), kb=kb, settings=settings)
