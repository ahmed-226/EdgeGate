FROM python:3.12-alpine

WORKDIR /app

COPY edgegate/ ./edgegate/
COPY config.json ./

# Security hygiene: a proxy at the network edge never runs as root.
RUN adduser -D edgegate \
    && chown -R edgegate:edgegate /app
USER edgegate

EXPOSE 8080

CMD ["python", "-m", "edgegate", "config.json"]
