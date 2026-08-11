FROM python:3.12-slim

WORKDIR /opt/loro
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 loro \
    && mkdir -p /var/lib/loro \
    && chown -R loro:loro /var/lib/loro
USER 10001

VOLUME ["/var/lib/loro"]
EXPOSE 8788
ENTRYPOINT ["loro", "audit", "collect"]
CMD ["--host", "0.0.0.0", "--port", "8788", "--path", "/var/lib/loro/audit.sqlite3"]
