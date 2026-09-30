"""V3-2 scenario simulator (Policy + Admin). Every response carries the COUNTERFACTUAL label (rule 22)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mandi, ScenarioRun, User
from ..rbac import require

router = APIRouter(prefix="/scenarios", tags=["V3-2 scenarios"])
policy = require("policy:read")


@router.get("")
def scenario_types(db: Session = Depends(get_db), _=Depends(policy)):
    """What can be simulated, with parameters, defaults and every assumption's source."""
    from agripulse_ml.scenarios import assumptions as B, label, scenario_config

    cfg = scenario_config()
    districts = sorted(set(db.scalars(select(Mandi.district).where(Mandi.lat.is_not(None)))))
    return {
        "label": label(),
        "scenarios": [
            {"id": "rainfall_failure", "title": "Rainfall failure in a region",
             "params": {"districts": districts, "deficit": "fraction of normal rain lost, 0..1",
                        "start": "date", "end": "date"},
             "assumption_chain": "rain deficit -> yield loss (ky) -> fewer local arrivals -> price (demand elasticity)",
             "model_channel": "rain inputs of the display model (last 30 days only)"},
            {"id": "export_ban", "title": "Export ban on tomato",
             "params": {"start": "date", "share": f"optional regional export share 0..{cfg['export_ban']['max_user_share']} "
                                                  "(default: national exports / production)"},
             "default_share": round(B.export_share(cfg), 5),
             "assumption_chain": "exports stay home -> more domestic supply -> price (demand elasticity)",
             "model_channel": "recent arrivals input of the display model (last 7 days)"},
        ],
        "assumptions": cfg,
        "doc": "docs/scenario-assumptions.md",
    }


class RunIn(BaseModel):
    scenario: str
    params: dict = {}


@router.post("/run", status_code=201)
def run_scenario(body: RunIn, db: Session = Depends(get_db), user: User = Depends(policy)):
    from agripulse_ml.scenarios.run import run, run_out

    return run_out(run(db, user, body.scenario, body.params))


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), _=Depends(policy)):
    from agripulse_ml.scenarios.run import run_out

    return [run_out(r, full=False) for r in db.scalars(select(ScenarioRun).order_by(ScenarioRun.id.desc()).limit(20))]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), _=Depends(policy)):
    from agripulse_ml.scenarios.run import run_out

    r = db.get(ScenarioRun, run_id)
    if r is None:
        raise HTTPException(404, "Not found")
    return run_out(r)
