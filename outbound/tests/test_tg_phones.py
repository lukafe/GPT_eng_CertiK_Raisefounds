"""Telefones dos decisores pelo waterfall do Apollo (offline)."""

import pytest

import db
import tg_phones


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeRpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self


def fake_client(people, url="https://x.supabase.co/functions/v1/apollo-phone-webhook?token=t"):
    calls = []

    class C:
        def rpc(self, name, params=None):
            calls.append((name, params))
            return FakeRpc(people if name == "tg_phone_candidates" else url)

    return C(), calls


PEOPLE = [
    {"id": 1, "apollo_person_id": "a1", "first_name": "Ana", "tg_profile": {"x": 1}},
    {"id": 2, "apollo_person_id": None, "first_name": "Bo", "last_name": "Li", "email": "bo@foo.xyz",
     "domain": "foo.xyz", "tg_profile": None},
    {"id": 3, "apollo_person_id": None, "first_name": "Cy", "email": "cy@bar.io", "domain": "bar.io", "tg_profile": {}},
]


def test_build_details_uses_apollo_id_or_identity():
    assert tg_phones.build_details(PEOPLE) == [
        {"id": "a1"},
        {"first_name": "Bo", "last_name": "Li", "email": "bo@foo.xyz", "domain": "foo.xyz"},
        {"first_name": "Cy", "email": "cy@bar.io", "domain": "bar.io"},
    ]


def test_request_phones_sends_waterfall_with_webhook_and_marks_contacts(monkeypatch):
    client, calls = fake_client(PEOPLE)
    sent, updates, runs = {}, [], []
    monkeypatch.setattr(db, "client", lambda: client)
    monkeypatch.setattr(db, "update_contact", lambda cid, **f: updates.append((cid, f)))
    monkeypatch.setattr(db, "log_run", lambda step, ok, detail="": runs.append((step, ok, detail)))
    monkeypatch.setattr(tg_phones, "headers", lambda: {})

    def http(method, url, **kw):
        sent.update(kw, url=url)
        return FakeResp({"request_id": "r9", "waterfall": {"status": "accepted"},
                         "matches": [{"id": "a1"}, {"id": "b2"}, None]})

    monkeypatch.setattr(tg_phones, "http_call", http)
    monkeypatch.setenv("TG_PHONE_REVEALS_PER_DAY", "5")

    assert tg_phones.request_phones() == {"requested": 3, "matched": 2}
    assert calls[0] == ("tg_phone_candidates", {"p_daily_max": 5})
    assert sent["url"].endswith("/people/bulk_match")
    assert sent["params"] == {"run_waterfall_phone": "true",
                              "webhook_url": "https://x.supabase.co/functions/v1/apollo-phone-webhook?token=t"}
    assert len(sent["json"]["details"]) == 3
    (c1, f1), (c2, f2), (c3, f3) = updates
    assert c1 == 1 and "apollo_person_id" not in f1 and f1["tg_profile"]["x"] == 1
    assert f1["tg_profile"]["phone_request_id"] == "r9" and "phone_none_at" not in f1["tg_profile"]
    assert c2 == 2 and f2["apollo_person_id"] == "b2"          # o webhook acha a pessoa por esse id
    assert c3 == 3 and "phone_none_at" in f3["tg_profile"]     # Apollo não achou: vai para o Finder
    assert runs[0][0] == "phone_reveal" and runs[0][1] is True


def test_nothing_to_do_logs_and_skips_apollo(monkeypatch):
    client, _ = fake_client([])
    runs = []
    monkeypatch.setattr(db, "client", lambda: client)
    monkeypatch.setattr(db, "log_run", lambda step, ok, detail="": runs.append(detail))
    monkeypatch.setattr(tg_phones, "http_call", lambda *a, **k: pytest.fail("não deveria chamar o Apollo"))
    assert tg_phones.request_phones() == {"requested": 0}
    assert "nenhum decisor" in runs[0]


def test_waterfall_not_accepted_raises_and_marks_nothing(monkeypatch):
    client, _ = fake_client(PEOPLE[:1])
    updates = []
    monkeypatch.setattr(db, "client", lambda: client)
    monkeypatch.setattr(db, "update_contact", lambda cid, **f: updates.append(cid))
    monkeypatch.setattr(tg_phones, "headers", lambda: {})
    monkeypatch.setattr(tg_phones, "http_call",
                        lambda *a, **k: FakeResp({"waterfall": {"status": "failed"}, "matches": []}))
    with pytest.raises(RuntimeError):
        tg_phones.request_phones()
    assert updates == []


def test_zero_per_day_turns_it_off(monkeypatch):
    client, calls = fake_client(PEOPLE)
    monkeypatch.setenv("TG_PHONE_REVEALS_PER_DAY", "0")
    monkeypatch.setattr(db, "client", lambda: client)
    assert tg_phones.request_phones() == {"requested": 0}
    assert calls == []
