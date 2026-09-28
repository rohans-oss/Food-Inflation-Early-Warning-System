"""Load cleaned daily series from the database into tidy DataFrames."""
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from agripulse_api.models import Arrival, Mandi, Price, Weather


def load_prices(db: Session, commodity: str = "Tomato", synthetic: bool = False) -> pd.DataFrame:
    """One modal price per mandi per day (median across variety/grade), outliers excluded.
    synthetic=False -> real rows only; True -> synthetic rows only. Never both."""
    rows = db.execute(
        select(Price.mandi_id, Price.date, Price.modal_price).where(
            Price.commodity == commodity,
            Price.is_outlier.is_(False),
            Price.modal_price > 0,
            (Price.source == "synthetic") if synthetic else (Price.source != "synthetic"),
        )
    ).all()
    df = pd.DataFrame(rows, columns=["mandi_id", "date", "price"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.groupby(["mandi_id", "date"], as_index=False)["price"].median()


def load_weather(db: Session, synthetic: bool = False) -> pd.DataFrame:
    """Observed weather per mandi-day; NASA POWER preferred for history, Open-Meteo fills recent days."""
    rows = db.execute(
        select(Weather.mandi_id, Weather.date, Weather.source, Weather.precip_mm, Weather.tmax_c, Weather.is_forecast).where(
            (Weather.source == "synthetic") if synthetic else (Weather.source != "synthetic")
        )
    ).all()
    df = pd.DataFrame(rows, columns=["mandi_id", "date", "source", "precip_mm", "tmax_c", "is_forecast"])
    if df.empty:
        return pd.DataFrame(columns=["mandi_id", "date", "precip_mm", "tmax_c"])
    df = df[~df["is_forecast"].astype(bool)]
    df["date"] = pd.to_datetime(df["date"])
    df["rank"] = df["source"].map({"nasa_power": 0, "open_meteo": 1}).fillna(2)
    df = df.sort_values("rank").drop_duplicates(["mandi_id", "date"])
    return df[["mandi_id", "date", "precip_mm", "tmax_c"]]


def load_arrivals(db: Session, commodity: str = "Tomato", synthetic: bool = False) -> pd.DataFrame:
    rows = db.execute(
        select(Arrival.mandi_id, Arrival.date, Arrival.tonnes).where(
            Arrival.commodity == commodity,
            (Arrival.source == "synthetic") if synthetic else (Arrival.source != "synthetic"),
        )
    ).all()
    df = pd.DataFrame(rows, columns=["mandi_id", "date", "tonnes"])
    if df.empty:
        return pd.DataFrame(columns=["mandi_id", "date", "tonnes"])
    df["date"] = pd.to_datetime(df["date"])
    return df.groupby(["mandi_id", "date"], as_index=False)["tonnes"].sum()


def load_mandis(db: Session) -> pd.DataFrame:
    rows = db.execute(select(Mandi.id, Mandi.name, Mandi.district, Mandi.state)).all()
    return pd.DataFrame(rows, columns=["mandi_id", "name", "district", "state"])
