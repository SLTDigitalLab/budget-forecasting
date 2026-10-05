FROM python:3.10-slim-bookworm

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY config /app/config
COPY model_training_engine /app/model_training_engine

RUN test -f /app/model_training_engine/output/all_budget_code_models.pkl \
    || (echo "ERROR: all_budget_code_models.pkl is missing from the backend image." && exit 1)
RUN test -f /app/model_training_engine/output/international_settlement_top12_models.pkl \
    || (echo "ERROR: international_settlement_top12_models.pkl is missing from the backend image." && exit 1)

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
