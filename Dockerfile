FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY ingress_operator/ ./ingress_operator/

RUN pip install uv && \
    UV_NO_SOURCES=1 uv pip install --system --no-cache .

RUN useradd -u 1000 -M -s /sbin/nologin operator
USER operator

CMD ["opnsense-ingress-operator"]
