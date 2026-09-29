from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mandi

router = APIRouter(prefix="/mandis", tags=["mandis"])


def mandi_out(m: Mandi) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "district": m.district,
        "state": m.state,
        "lat": m.lat,
        "lon": m.lon,
        "coords_verified": m.coords_verified,
        "geofence_radius_m": m.geofence_radius_m,
    }


@router.get("")
def list_mandis(state: str | None = None, db: Session = Depends(get_db)):
    """Public: mandi names and locations are public information (needed on the sign-up form)."""
    q = select(Mandi).order_by(Mandi.state, Mandi.name)
    if state:
        q = q.where(Mandi.state == state)
    return [mandi_out(m) for m in db.scalars(q)]
