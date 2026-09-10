"""Testes da infra comum (fase 0): log, retry e leitura de env.

Os testes das seções do spec (Supabase, DefiLlama, Hunter, Apollo) entram nas
fases correspondentes; cada um gravará uma linha em `runs` quando o db existir.
"""

import pytest
import requests

import common
from common import MissingCredential, env, http_call, log


def test_log_writes_timestamped_line(tmp_path, monkeypatch):
    logfile = tmp_path / "log.txt"
    monkeypatch.setattr(common, "LOG_FILE", logfile)
    log("teste", "ok")
    content = logfile.read_text()
    assert "[teste] ok" in content
    assert content.startswith("20")  # timestamp ISO


def test_env_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE_VAR", raising=False)
    with pytest.raises(MissingCredential):
        env("NOPE_VAR")
    assert env("NOPE_VAR", required=False) is None


def test_http_call_retries_on_500(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "LOG_FILE", tmp_path / "log.txt")
    monkeypatch.setattr(common, "BACKOFF_BASE", 0)  # sem espera no teste
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        resp = requests.Response()
        resp.status_code = 500
        resp.url = url
        return resp

    monkeypatch.setattr(requests, "request", fake_request)
    with pytest.raises(requests.HTTPError):
        http_call("GET", "https://example.com/x", step="teste")
    assert len(calls) == 3


def test_http_call_returns_on_success(monkeypatch):
    def fake_request(method, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(requests, "request", fake_request)
    resp = http_call("GET", "https://example.com/x", step="teste")
    assert resp.status_code == 200
