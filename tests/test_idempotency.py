import hashlib
import json
import time

import pytest

from wechat_cli.core.errors import AppError
from wechat_cli.core.idempotency import Idempotency


def test_receipts_survive_restart(tmp_path):
    attempts = []

    def send():
        attempts.append(True)
        return {"acceptedByClient": True}

    store = Idempotency(tmp_path)
    store.once("send", "key", {"text": "private"}, send)
    store.close()
    store = Idempotency(tmp_path)
    assert store.once("send", "key", {"text": "private"}, send) == {"acceptedByClient": True}
    assert attempts == [True]
    store.close()
    assert b"private" not in (tmp_path / "requests.sqlite3").read_bytes()
    assert (tmp_path / "requests.sqlite3").stat().st_mode & 0o777 == 0o600


def test_crash_intent_prevents_resending(tmp_path):
    store = Idempotency(tmp_path)
    payload = {"text": "hello"}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store.connection.execute(
        "INSERT INTO requests VALUES (?,?,?,?,NULL,NULL)", ("send", "key", digest, time.time())
    )
    store.connection.commit()
    store.close()
    store = Idempotency(tmp_path)
    with pytest.raises(AppError) as caught:
        store.once("send", "key", payload, lambda: pytest.fail("must never send"))
    assert caught.value.code == "native_send_outcome_unknown"
    store.close()
