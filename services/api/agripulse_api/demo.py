"""Telling demo accounts / demo companies apart from real ones in the same database (persistent public demo).

Real users sign up through /auth/register; demo accounts are seeded (@demo.agripulse), FPO-added members
(@members.agripulse.local) and not-yet-signed-up driver invites (@invite.agripulse.local) are not demo per se but are
treated like their organisation. The demo autopilot only ever acts for DEMO fleets and only weighs at mandis that have no
real manager, so a real farmer, transporter or mandi manager always does their own step."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Organization, User

DEMO_DOMAIN = "@demo.agripulse"


def is_demo_user(u: User | None) -> bool:
    return bool(u and u.email.endswith(DEMO_DOMAIN))


def is_demo_org(org: Organization | None) -> bool:
    return bool(org and org.name.endswith("(demo)"))


def real_managers(db: Session, mandi_id: int) -> list[User]:
    """Active, real (signed-up) mandi managers / traders of this mandi."""
    return [u for u in db.scalars(select(User).where(User.role == "trader", User.mandi_id == mandi_id,
                                                      User.is_active.is_(True))) if not is_demo_user(u)]
