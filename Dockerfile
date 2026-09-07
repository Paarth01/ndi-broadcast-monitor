# For Linux deployment only -- see docker-compose.yml for why this
# isn't the primary way to run this project. Requires cyndilib's
# native NDI SDK dependency to be satisfiable inside the image, and
# --network host (or network_mode: host in compose) for NDI mDNS
# discovery to actually find sources on the LAN.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
