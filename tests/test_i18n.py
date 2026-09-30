"""V3-3: every alert template and screen string exists in English, Kannada and Hindi, with the same placeholders;
per-string review status; the native-speaker export / import round trip."""
import csv
import json

import pytest

from agripulse_api import i18n_tools as T

LANGS = ("en", "kn", "hi")


@pytest.mark.parametrize("name", ["alerts", "ui"])
def test_every_string_exists_in_every_language_with_the_same_placeholders(name):
    data = T.load(name)
    for lang in LANGS:
        assert set(data[lang]) == set(data["en"]), (name, lang, set(data["en"]) ^ set(data[lang]))
        for key, en in data["en"].items():
            cur = data[lang][key]
            pairs = [(en[f], cur[f]) for f in ("title", "body")] if isinstance(en, dict) else [(en, cur)]
            for e, c in pairs:
                assert c.strip(), (name, lang, key)
                assert T.placeholders(c) == T.placeholders(e), (name, lang, key)


@pytest.mark.parametrize("name", ["alerts", "ui"])
def test_every_translation_has_a_review_status(name):
    data = T.load(name)
    for lang in ("kn", "hi"):
        review = data["_review"][lang]
        assert set(review) == set(data["en"])
        assert {v["status"] for v in review.values()} <= {"machine", "reviewed"}
        for v in review.values():
            if v["status"] == "reviewed":
                assert v.get("by") and v.get("on")


def test_alerts_render_in_hindi(client, as_role, db):
    from agripulse_api.alerts import render

    title, body = render("vehicle_arrived", "hi", vehicle="KA-01", mandi="कोलार", time="10:00")
    assert "KA-01" in body and "पहुँचा" in title
    r = client.patch("/auth/me", json={"preferred_lang": "hi"}, headers=as_role("farmer"))
    assert r.status_code == 200 and r.json()["preferred_lang"] == "hi"
    assert client.patch("/auth/me", json={"preferred_lang": "fr"}, headers=as_role("farmer")).status_code == 422


def test_review_round_trip(tmp_path, monkeypatch):
    files = {}
    for name, p in T.FILES.items():
        files[name] = tmp_path / p.name
        files[name].write_text(p.read_text("utf-8"), "utf-8")
    monkeypatch.setattr(T, "FILES", files)
    out = tmp_path / "review_kn.csv"
    n = T.export("kn", out)
    assert n == 2 * len(T.load("alerts")["en"]) + len(T.load("ui")["en"])
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    for r in rows:
        if r["file"] == "alerts" and r["key"] == "vehicle_arrived" and r["field"] == "title":
            r["corrected"] = "ವಾಹನ {mandi} ತಲುಪಿತು (ಪರಿಶೀಲಿತ)"
        if r["file"] == "ui" and r["key"] == "mandi":
            r["approve"] = "yes"
    bad = [dict(r) for r in rows]
    for r in bad:
        if r["file"] == "ui" and r["key"] == "eta":
            r["corrected"] = "ETA {oops}"  # placeholder that English doesn't have
    for path, data in ((tmp_path / "bad.csv", bad), (tmp_path / "good.csv", rows)):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(data)
    before = files["alerts"].read_text("utf-8")
    r = T.import_("kn", tmp_path / "bad.csv", "Test Reviewer")
    assert r["applied"] is False and "placeholders" in r["errors"][0]
    assert files["alerts"].read_text("utf-8") == before  # nothing half-applied
    r = T.import_("kn", tmp_path / "good.csv", "Test Reviewer")
    assert r == {"applied": True, "corrected": 1, "approved": 1}
    alerts, ui = json.loads(files["alerts"].read_text("utf-8")), json.loads(files["ui"].read_text("utf-8"))
    assert alerts["kn"]["vehicle_arrived"]["title"].endswith("(ಪರಿಶೀಲಿತ)")
    assert alerts["_review"]["kn"]["vehicle_arrived"]["status"] == "reviewed"
    assert ui["_review"]["kn"]["mandi"] == {"status": "reviewed", "by": "Test Reviewer", "on": ui["_review"]["kn"]["mandi"]["on"]}
    assert ui["_review"]["kn"]["eta"]["status"] == "machine"
    st = T.status()
    assert st["kn"]["ui"]["reviewed"] == 1 and st["kn"]["alerts"]["reviewed"] == 1


def test_admin_sees_review_status(client, as_role):
    r = client.get("/admin/i18n-status", headers=as_role("admin"))
    assert r.status_code == 200 and set(r.json()) == {"kn", "hi"}
    assert client.get("/admin/i18n-status", headers=as_role("policy")).status_code == 403
