import logging
import threading
import time
from collections import Counter

import httpx
import pytest
from sqlalchemy import text

from app.db import WorkerSessionLocal
from app.platform import outbox
from app.platform.notifications import InAppAdapter, LogAdapter, WebhookAdapter, default_adapters
from app.platform.outbox import Adapter, backoff_seconds, dispatch_batch, dispatch_pending
from app.platform.workflows import on_event
from tests.workflows.conftest import refund_event


def enqueue_many(n: int, adapter: str = "test", **extra) -> None:
    with WorkerSessionLocal() as db, db.begin():
        for i in range(n):
            outbox.enqueue(db, topic="t", payload={"adapter": adapter, "n": i, **extra})


class Recorder(Adapter):
    name = "test"

    def __init__(self, fail_times: int = 0, delay: float = 0.0):
        self.sent: list[int] = []
        self.fail_times = fail_times
        self.delay = delay
        self.calls = 0
        self.in_transaction: list[bool] = []
        self._lock = threading.Lock()

    def send(self, db, message):
        self.in_transaction.append(db is not None)
        with self._lock:
            self.calls += 1
            if self.calls <= self.fail_times:
                raise RuntimeError(f"downstream unavailable (call {self.calls})")
        time.sleep(self.delay)
        with self._lock:
            self.sent.append(message.id)


def rewind(owner_engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE outbox SET available_at = now() - interval '1 second'"))


def test_dispatch_marks_sent_outside_transaction(outbox_rows):
    enqueue_many(3)
    rec = Recorder()
    report = dispatch_batch(adapters={"test": rec})
    assert (report.claimed, report.sent) == (3, 3)
    assert rec.in_transaction == [False, False, False]
    assert all(r["dispatched_at"] and r["attempts"] == 1 for r in outbox_rows())
    assert dispatch_batch(adapters={"test": rec}).claimed == 0


def test_two_concurrent_dispatchers_never_send_twice(outbox_rows):
    enqueue_many(60)
    rec = Recorder(delay=0.005)
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        for _ in range(20):
            dispatch_batch(adapters={"test": rec}, batch_size=5)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    counts = Counter(rec.sent)
    assert len(counts) == 60 and set(counts.values()) == {1}
    assert all(r["dispatched_at"] is not None and r["attempts"] == 1 for r in outbox_rows())


def test_failure_backs_off_then_retries(outbox_rows, owner_engine):
    enqueue_many(1)
    rec = Recorder(fail_times=2)
    kw = {"adapters": {"test": rec}, "base_backoff_seconds": 30}
    r1 = dispatch_batch(**kw)
    assert (r1.failed, r1.sent) == (1, 0)
    (row,) = outbox_rows()
    assert row["attempts"] == 1 and row["dispatched_at"] is None
    assert row["last_error"] == "RuntimeError: downstream unavailable (call 1)"
    with owner_engine.connect() as conn:
        delay = conn.execute(text("SELECT extract(epoch FROM available_at - now()) FROM outbox"))
        assert 25 < delay.scalar_one() <= 30
    assert dispatch_batch(**kw).claimed == 0  # backing off
    rewind(owner_engine)
    dispatch_batch(**kw)
    with owner_engine.connect() as conn:
        delay = conn.execute(text("SELECT extract(epoch FROM available_at - now()) FROM outbox"))
        assert 55 < delay.scalar_one() <= 60  # exponential
    rewind(owner_engine)
    r3 = dispatch_batch(**kw)
    assert r3.sent == 1
    (row,) = outbox_rows()
    assert row["attempts"] == 3 and row["dispatched_at"] is not None and row["last_error"] is None


def test_backoff_is_exponential_and_capped():
    assert [backoff_seconds(a, 5, 60) for a in range(1, 7)] == [5, 10, 20, 40, 60, 60]


def test_dead_letter_stops_retrying(outbox_rows, owner_engine, caplog):
    enqueue_many(1)
    rec = Recorder(fail_times=100)
    for _ in range(5):
        rewind(owner_engine)
        dispatch_batch(adapters={"test": rec}, max_attempts=3)
    assert rec.calls == 3
    (row,) = outbox_rows()
    assert row["attempts"] == 3 and row["dispatched_at"] is None
    assert "dead-lettered after 3 attempts" in caplog.text


def test_long_errors_are_truncated(outbox_rows):
    class Loud(Adapter):
        def send(self, db, message):
            raise RuntimeError("x" * 10_000)

    enqueue_many(1)
    dispatch_batch(adapters={"test": Loud()})
    assert len(outbox_rows()[0]["last_error"]) == outbox.MAX_ERROR_LENGTH


def test_unknown_adapter_is_a_failure_not_a_crash(outbox_rows):
    enqueue_many(1, adapter="carrier-pigeon")
    report = dispatch_batch(adapters={"test": Recorder()})
    assert report.failed == 1
    assert "no adapter 'carrier-pigeon'" in outbox_rows()[0]["last_error"]


def test_expired_lease_cannot_overwrite_newer_claim(outbox_rows, owner_engine):
    enqueue_many(1)

    class SlowAndReclaimed(Adapter):
        def send(self, db, message):
            # Simulate the lease expiring and another dispatcher claiming the row meanwhile.
            with owner_engine.begin() as conn:
                conn.execute(text("UPDATE outbox SET attempts = attempts + 1"))

    report = dispatch_batch(adapters={"test": SlowAndReclaimed()})
    assert (report.sent, report.lost_lease) == (0, 1)
    (row,) = outbox_rows()
    assert row["dispatched_at"] is None and row["attempts"] == 2


def test_in_app_delivery_is_idempotent_and_scoped(db_as, outbox_rows, owner_engine, worker_db):
    db = db_as("dave")
    with db.begin():
        on_event(db, refund_event("refund.failed", status="failed", business_unit="EU"))
    adapters = default_adapters()
    dispatch_pending(adapters=adapters)
    in_app_id = next(r["id"] for r in outbox_rows() if r["payload"]["adapter"] == "in_app")
    # Redeliver the same message (e.g. crash before the mark): unique outbox_id dedupes.
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE outbox SET dispatched_at = NULL WHERE id = :id"), {"id": in_app_id}
        )
    dispatch_pending(adapters=adapters)
    rows = worker_db.execute(text("SELECT * FROM notifications")).mappings().all()
    assert len(rows) == 1
    assert rows[0]["outbox_id"] == in_app_id
    assert rows[0]["business_unit"] == "EU"
    assert rows[0]["topic"] == "payments-oncall"
    assert rows[0]["recipient_role"] == "finance_admin"


def test_in_app_refuses_missing_business_unit(outbox_rows):
    enqueue_many(1, adapter="in_app", title="t")
    report = dispatch_batch(adapters={"in_app": InAppAdapter()})
    assert report.failed == 1
    assert "requires a business_unit" in outbox_rows()[0]["last_error"]


def test_log_adapter_writes_structured_line_without_body(caplog):
    enqueue_many(1, adapter="log", title="Hello", body="secret body", channel="c")
    with caplog.at_level(logging.INFO, logger="notifications"):
        dispatch_batch(adapters={"log": LogAdapter()})
    assert '"title": "Hello"' in caplog.text and "secret body" not in caplog.text


SECRET_URL = "https://hooks.slack.test/services/T000/B000/s3cr3t-token"


def test_webhook_posts_slack_compatible_json(caplog, outbox_rows):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    enqueue_many(1, adapter="webhook", title="T", body="B")
    with caplog.at_level(logging.DEBUG):
        dispatch_batch(
            adapters={"webhook": WebhookAdapter(SECRET_URL, transport=httpx.MockTransport(handler))}
        )
    assert seen[0].method == "POST"
    assert seen[0].read() == b'{"text":"*T*\\nB"}'
    assert seen[0].headers["X-Outbox-Message-Id"] == str(outbox_rows()[0]["id"])
    assert outbox_rows()[0]["dispatched_at"] is not None
    assert "s3cr3t" not in caplog.text


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500),
        lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out", request=request)),
    ],
)
def test_webhook_failure_never_records_url(handler, caplog, outbox_rows):
    enqueue_many(1, adapter="webhook", title="T")
    with caplog.at_level(logging.DEBUG):
        report = dispatch_batch(
            adapters={"webhook": WebhookAdapter(SECRET_URL, transport=httpx.MockTransport(handler))}
        )
    assert report.failed == 1
    error = outbox_rows()[0]["last_error"]
    assert error.startswith("WebhookDeliveryError")
    assert "s3cr3t" not in error and "s3cr3t" not in caplog.text
    assert "s3cr3t" not in repr(WebhookAdapter(SECRET_URL))


def test_unconfigured_webhook_and_email_fall_back_to_log(monkeypatch):
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    adapters = default_adapters()
    assert isinstance(adapters["webhook"], LogAdapter)
    assert isinstance(adapters["email"], LogAdapter)
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", SECRET_URL)
    assert isinstance(default_adapters()["webhook"], WebhookAdapter)
