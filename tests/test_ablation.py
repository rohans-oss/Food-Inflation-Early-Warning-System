"""V2-5 ablation: each variant sees only its groups' columns; real-so-far data reports 'not enough' honestly."""
from datetime import date, timedelta

import pandas as pd

from agripulse_api.models import Mandi, Price
from agripulse_ml.ablation import PREFIX, VARIANTS, columns_for, run_real
from agripulse_ml.features.inputs import Inputs
from agripulse_ml.features.store import build_table


def test_each_variant_sees_only_its_groups():
    tb = build_table(Inputs.from_synthetic(seed=3, start=date(2024, 1, 1), end=date(2024, 12, 31), n_mandis=2),
                     "prices+weather+graph+transit")
    for name, groups in VARIANTS.items():
        cols = columns_for(tb, groups)
        for g, prefixes in PREFIX.items():
            has = any(c.startswith(prefixes) for c in cols)
            present = any(c.startswith(prefixes) for c in tb.feature_columns)
            assert has == (g in groups and present), (name, g)
        assert not any(c.startswith("target") or c == "spike" for c in cols)
    base = columns_for(tb, ["weather"])
    assert set(columns_for(tb, [])) < set(base) < set(columns_for(tb, ["weather", "graph", "transit"]))


def test_real_ablation_without_real_prices(db):
    out = run_real(db)
    assert out["status"] == "not_enough_real_data" and out["real_price_rows"] == 0


def test_real_ablation_with_a_few_days_of_real_prices(db):
    """What the live system has today (real prices since 2026-09-25): the harness cannot form a fold."""
    m = db.query(Mandi).first()
    for k in range(6):
        db.add(Price(mandi_id=m.id, commodity="Tomato", date=date.today() - timedelta(days=k), modal_price=1500 + k,
                     source="agmarknet"))
    db.commit()
    out = run_real(db)
    assert out["status"] == "not_enough_real_data" and out["max_real_price_days"] == 6
    assert out["mandis_with_real_prices"] == 1 and out["reason"]


def test_admin_v2_results_card(client, as_role):
    r = client.get("/admin/v2-results", headers=as_role("admin"))
    assert r.status_code == 200
    rows = r.json()
    assert {x["phase"] for x in rows} >= {"V2-0", "V2-2", "V2-3", "V2-4", "V2-5"}
    assert all(x["provenance_label"] and x["doc"].startswith("docs/") for x in rows)
    assert any(x["data_provenance"] == "real" for x in rows) and any(x["data_provenance"] == "synthetic" for x in rows)
    assert client.get("/admin/v2-results", headers=as_role("policy")).status_code == 403
