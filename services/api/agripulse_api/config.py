"""Runtime settings. Everything secret comes from the environment / .env (rule 6)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---
    database_url: str = "sqlite:///./agripulse.db"
    redis_url: str = ""  # empty -> in-process pub/sub (fine for dev / tests)
    jwt_secret: str = "change-me-in-.env"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    jwt_refresh_days: int = 14
    cors_origins: str = "http://localhost:3000,http://localhost:8000,https://localhost"  # https://localhost = Android driver app (Capacitor)
    public_base_url: str = "http://localhost:3000"

    # --- data sources ---
    # data.gov.in "Current Daily Price of Various Commodities from Various Markets (Mandi)".
    # Resource id checked against live responses (see docs/data-sources.md).
    data_gov_api_key: str = ""
    agmarknet_resource_id: str = "9ef84268-d588-465a-a308-a864a43d0070"
    agmarknet_base_url: str = "https://api.data.gov.in/resource"
    agmarknet_states: str = "Karnataka,Tamil Nadu,Andhra Pradesh,Telangana,Maharashtra,Kerala"
    agmarknet_commodity: str = "Tomato"
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    nasa_power_url: str = "https://power.larc.nasa.gov/api/temporal/daily/point"

    # --- routing ---
    osrm_url: str = ""  # e.g. http://osrm:5000 ; empty -> haversine fallback (flagged in API output)
    # V3-3 (backlog 3): OpenStreetMap place search for mandi-location CANDIDATES (an admin still confirms each one).
    # The public server's policy requires an identifying User-Agent: set NOMINATIM_CONTACT (email or URL) or it refuses.
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_contact: str = ""
    fallback_road_factor: float = 1.3  # straight-line km -> road km when OSRM is unavailable
    fallback_speed_kmph: float = 40.0

    # --- tracking ---
    share_link_hours: int = 24
    unexpected_stop_minutes: int = 30
    stop_speed_kmph: float = 3.0
    pickup_radius_m: float = 300.0
    delay_alert_minutes: int = 45

    # --- forecasting / recommender ---
    spike_threshold_pct: float = 30.0  # price rise above this within 2 weeks = spike
    spike_alert_probability: float = 0.5
    # recommender cost parameters live in config/recommender.toml
    model_dir: str = "ml/artifacts"

    # --- alerts ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "alerts@agripulse.local"
    sms_webhook_url: str = ""  # optional adapter; empty -> SMS disabled

    # --- jobs ---
    enable_scheduler: bool = False

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def state_list(self) -> list[str]:
        return [s.strip() for s in self.agmarknet_states.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
