FROM python:3.10-slim-bookworm

WORKDIR /app

# Minimal OS deps. Prophet's precompiled prophet_model.bin does not need a full
# CmdStan toolchain at runtime; do not download CmdStan from training workers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
# Prophet 1.1.6 ships an incomplete cmdstan-2.33.1 stub under stan_model/
# (no makefile). cmdstanpy 1.3 validates the makefile and rejects that path,
# so Prophet fails to attach stan_backend. Remove the stub after install; the
# packaged prophet_model.bin is sufficient for fit/predict.
RUN pip install --no-cache-dir --retries 10 --timeout 300 -r /app/requirements.txt \
    && python - <<'PY'
from pathlib import Path
import shutil

bundled = Path("/usr/local/lib/python3.10/site-packages/prophet/stan_model/cmdstan-2.33.1")
if bundled.exists():
    shutil.rmtree(bundled)
    print("REMOVED_INCOMPLETE_PROPHET_BUNDLED_CMDSTAN", bundled)
else:
    print("NO_BUNDLED_CMDSTAN_STUB")

from prophet.models import CmdStanPyBackend

backend = CmdStanPyBackend()
print("PROPHET_BACKEND_OK", backend.get_type())
PY

COPY app /app/app
COPY config /app/config
COPY model_training_engine /app/model_training_engine

RUN test -f /app/model_training_engine/output/all_budget_code_models.pkl \
    || (echo "ERROR: all_budget_code_models.pkl is missing from the backend image." && exit 1)
RUN test -f /app/model_training_engine/output/international_settlement_top12_models.pkl \
    || (echo "ERROR: international_settlement_top12_models.pkl is missing from the backend image." && exit 1)

# Bounded Prophet smoke at build time (synthetic; no production artifacts).
RUN python - <<'PY'
import os
import tempfile
import pandas as pd
from prophet import Prophet

frame = pd.DataFrame({
    "ds": pd.date_range("2023-01-01", periods=24, freq="MS"),
    "y": [10.0 + (i % 3) for i in range(24)],
})
model = Prophet(
    yearly_seasonality=False,
    weekly_seasonality=False,
    daily_seasonality=False,
    growth="linear",
)
if not hasattr(model, "stan_backend") or model.stan_backend is None:
    raise SystemExit(
        "Prophet constructed without stan_backend. "
        "Check that the incomplete bundled cmdstan-2.33.1 stub was removed."
    )
with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    model.fit(frame)
    forecast = model.predict(model.make_future_dataframe(periods=3, freq="MS"))
tail = forecast["yhat"].tail(3)
if not bool(tail.notna().all()) or not bool((tail == tail).all()):
    raise SystemExit("Prophet predictions were not finite")
backend_type = model.stan_backend.get_type() if hasattr(model.stan_backend, "get_type") else type(model.stan_backend).__name__
print("PROPHET_SMOKE_OK", float(tail.iloc[-1]), backend_type)
PY

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
