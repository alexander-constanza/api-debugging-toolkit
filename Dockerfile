FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# Two workers with four threads each: a single sync worker means one
# blocking request (e.g. /simulate/timeout?seconds=10) stalls the whole
# service, health checks included. See RUNBOOK section 7.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", \
     "--timeout", "30", "--access-logfile", "-", "app.main:create_app()"]
