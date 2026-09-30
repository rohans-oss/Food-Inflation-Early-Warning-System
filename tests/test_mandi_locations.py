"""V3-3 (backlog 3): confirm mandi locations; OpenStreetMap candidates are suggestions only; every change audited."""
import httpx
import respx
from sqlalchemy import select

from agripulse_api.config import get_settings
from agripulse_api.models import AuditLog, Mandi

# the documented Nominatim jsonv2 shape: lat / lon are STRINGS
NOMINATIM = [{"place_id": 111, "licence": "ODbL", "osm_type": "way", "osm_id": 222, "lat": "13.1402", "lon": "78.1350",
              "category": "amenity", "type": "marketplace", "place_rank": 30, "importance": 0.1, "addresstype": "amenity",
              "name": "Kolar APMC Yard", "display_name": "Kolar APMC Yard, Kolar, Karnataka, India",
              "boundingbox": ["13.13", "13.15", "78.13", "78.14"]}]


def _kolar(db):
    return db.scalar(select(Mandi).where(Mandi.name == "Kolar APMC"))


def test_candidates_need_a_contact_for_the_public_server(client, as_role, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "nominatim_contact", "")
    r = client.get(f"/admin/mandis/{_kolar(db).id}/osm-candidates", headers=as_role("admin"))
    assert r.status_code == 409 and "NOMINATIM_CONTACT" in r.json()["detail"]


@respx.mock
def test_candidates_are_parsed_and_identified(client, as_role, db, monkeypatch):
    from agripulse_api.routers import admin as A

    A._osm_cache.clear()
    monkeypatch.setattr(get_settings(), "nominatim_contact", "ops@example.org")
    route = respx.get("https://nominatim.openstreetmap.org/search").mock(return_value=httpx.Response(200, json=NOMINATIM))
    m = _kolar(db)
    r = client.get(f"/admin/mandis/{m.id}/osm-candidates", headers=as_role("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    [c] = body["candidates"]  # the same place from 3 queries is listed once
    assert c["lat"] == 13.1402 and c["lon"] == 78.135 and c["osm_url"] == "https://www.openstreetmap.org/way/222"
    assert "OpenStreetMap" in body["attribution"] and "Suggestions only" in body["note"]
    req = route.calls[0].request
    assert "ops@example.org" in req.headers["user-agent"] and req.url.params["format"] == "jsonv2"
    assert req.url.params["countrycodes"] == "in"
    assert db.get(Mandi, m.id).coords_verified is False  # suggesting never verifies anything
    assert client.get(f"/admin/mandis/{m.id}/osm-candidates", headers=as_role("policy")).status_code == 403


def test_confirming_and_moving_a_location_is_audited(client, as_role, db):
    m = _kolar(db)
    r = client.patch(f"/admin/mandis/{m.id}", json={"lat": 13.1402, "lon": 78.135, "coords_verified": True,
                                                     "geofence_radius_m": 400}, headers=as_role("admin"))
    assert r.status_code == 200 and r.json()["coords_verified"] is True
    # moving the point again without saying "verified" makes it unverified
    r = client.patch(f"/admin/mandis/{m.id}", json={"lat": 13.15, "lon": 78.14}, headers=as_role("admin"))
    assert r.json()["coords_verified"] is False
    rows = db.scalars(select(AuditLog).where(AuditLog.entity == "mandi", AuditLog.entity_id == m.id)
                      .order_by(AuditLog.id)).all()
    assert [x.to_state for x in rows] == ["verified", "unverified"]
    assert rows[0].details["after"]["lat"] == 13.1402 and rows[0].details["after"]["geofence_radius_m"] == 400
    locs = client.get("/admin/mandis/locations", headers=as_role("admin")).json()
    assert any(x["id"] == m.id and x["lat"] == 13.15 for x in locs)
