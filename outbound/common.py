"""Shared infrastructure: env loading, logging to log.txt, HTTP calls with retry."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# Caminho explícito: o .env é achado mesmo quando o cron chama de outro diretório.
load_dotenv(Path(__file__).resolve().parent / ".env")

LOG_FILE = Path(__file__).resolve().parent / "log.txt"

RETRIES = 3
BACKOFF_BASE = 2  # seconds: 2, 4, 8


class MissingCredential(Exception):
    """Raised when a required env var for the current step is not set."""


def env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and (value is None or value.strip() == ""):
        raise MissingCredential(
            f"Variável de ambiente {name} não definida. "
            f"Preencha o .env antes de rodar esta etapa."
        )
    return value


def log(step: str, result: str) -> None:
    """Append a timestamped line to log.txt and echo to stdout."""
    line = f"{datetime.now(timezone.utc).isoformat()} [{step}] {result}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_call(method: str, url: str, step: str = "http", **kwargs) -> requests.Response:
    """HTTP request with 3 retries and exponential backoff (2s, 4s, 8s).

    Retries on connection errors, timeouts, and 5xx/429 responses.
    Raises the last error if all attempts fail. Does NOT raise on 4xx —
    callers inspect the response (except 429, which is retried).
    """
    kwargs.setdefault("timeout", 30)
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 500 or resp.status_code == 429:
                last_error = requests.HTTPError(
                    f"HTTP {resp.status_code} em {url}", response=resp
                )
            else:
                return resp
        except requests.RequestException as e:
            last_error = e
        if attempt < RETRIES:
            wait = BACKOFF_BASE ** attempt
            log(step, f"tentativa {attempt}/{RETRIES} falhou ({last_error}); retry em {wait}s")
            time.sleep(wait)
    log(step, f"todas as {RETRIES} tentativas falharam para {url}: {last_error}")
    raise last_error
