FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/market

COPY requirements.txt ./requirements.txt
COPY app/requirements.txt ./app/requirements.txt
COPY api/requirements.txt ./api/requirements.txt
RUN pip install -r requirements.txt -r app/requirements.txt -r api/requirements.txt

COPY . .

RUN useradd --create-home --uid 10001 market && chown -R market /srv/market
USER market

EXPOSE 8501 8000
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
