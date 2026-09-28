# AgriPulse — Food-Inflation Early-Warning System

Forecasts tomato prices 1–4 weeks ahead as ranges (p10 / p50 / p90 + spike probability) and tracks produce vehicles live from farm to mandi, so supply in motion becomes a leading price signal.

**Status:** Version 1 (Live Platform) in progress. Full setup and verification steps are added as each phase lands.

## Quick start (dev, no Docker)

```bash
pip install -e ".[dev]"
cp .env.example .env          # set JWT_SECRET at minimum
alembic upgrade head
python -m agripulse_api.seed --demo
uvicorn agripulse_api.main:app --reload --app-dir services/api
pytest
```
