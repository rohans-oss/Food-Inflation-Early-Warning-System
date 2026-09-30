"""V3-3 rule 23: per-module data status, visible to Policy and Admin."""
from tests.test_tracking import seed_forecasts


def test_each_module_reports_its_own_status(client, as_role, db):
    seed_forecasts(db)  # synthetic display-model forecasts for every mandi
    r = client.get("/module-status", headers=as_role("policy"))
    assert r.status_code == 200
    by = {m["module"]: m for m in r.json()}
    assert set(by) == {"Price forecast", "Mandi graph", "Satellite crop signal", "In-transit supply feature",
                       "Optimizer, shared + return loads", "Scenario simulator", "Real spike backtest"}
    assert by["Real spike backtest"]["status"] == "not_yet_evaluable"  # seed_forecasts writes no real prices
    assert by["Price forecast"]["status"] == "synthetic" and by["Price forecast"]["per_mandi"]["synthetic"] > 0
    assert by["Optimizer, shared + return loads"]["status"] == "not_yet_evaluable"
    assert by["Scenario simulator"]["counterfactual"] is True and by["Scenario simulator"]["status"] == "synthetic"
    assert by["Mandi graph"]["status"] == "not_yet_evaluable"  # no graph build in the test database
    assert all(m["evidence"] and m["doc"].startswith("docs/") for m in r.json())
    assert client.get("/module-status", headers=as_role("admin")).status_code == 200
    assert client.get("/module-status", headers=as_role("farmer")).status_code == 403
