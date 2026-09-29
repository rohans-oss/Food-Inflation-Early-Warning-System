"""Migrations go up and down cleanly, the models match the migrated schema, and 0004 backfills provenance."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from agripulse_api.db import Base

ROOT = Path(__file__).resolve().parents[1]


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "services/api/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_downgrade_and_provenance_backfill(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = _cfg(url)
    command.upgrade(cfg, "0003")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO mandis (id,name,district,state,coords_verified,geofence_radius_m,aliases) "
                       "VALUES (1,'X','d','s',0,500,'[]')"))
        for h, synth in ((1, 1), (2, 0)):
            c.execute(text("INSERT INTO forecasts (mandi_id,commodity,issue_date,target_date,horizon_weeks,p10,p50,p90,"
                           "spike_prob,model_name,model_version,trained_on_synthetic,created_at) VALUES "
                           f"(1,'Tomato','2026-09-01','2026-09-08',{h},1,2,3,0.1,'m','',{synth},'2026-09-01')"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        rows = dict(c.execute(text("SELECT horizon_weeks, data_provenance FROM forecasts")).all())
        assert rows == {1: "synthetic", 2: "real"}
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
        assert diff == [], f"models and migrations disagree: {diff}"
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng.dispose()
