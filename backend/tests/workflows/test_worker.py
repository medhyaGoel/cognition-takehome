import logging
import os
import signal
import threading

from app import worker as worker_mod
from app.worker import Worker, _RefundsExecution


def test_one_failing_job_does_not_stop_the_others(caplog):
    calls = []

    def boom():
        calls.append("boom")
        raise RuntimeError("kaput")

    w = Worker([("boom", boom), ("ok", lambda: calls.append("ok"))], poll_interval=0)
    w.run_once()
    w.run_once()
    assert calls == ["boom", "ok", "boom", "ok"]
    assert "worker job boom failed; continuing" in caplog.text


def test_sigterm_stops_the_loop_gracefully():
    ticks = []
    w = Worker([("tick", lambda: ticks.append(1))], poll_interval=0.01)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        w.install_signal_handlers()
        threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()
        w.run()  # returns once SIGTERM is handled
    finally:
        signal.signal(signal.SIGTERM, previous)
        signal.signal(signal.SIGINT, signal.default_int_handler)
    assert w.stop_event.is_set() and ticks


def test_default_jobs_include_refunds_execution():
    names = [name for name, _ in Worker().jobs]
    assert names == ["outbox_dispatch", "workflow_schedules", "refunds_execution"]


def test_refunds_execution_calls_execute_pending(monkeypatch):
    import app.apps.refunds.execution as execution

    calls = []
    monkeypatch.setattr(execution, "execute_pending", lambda: calls.append(1) or 0)
    _RefundsExecution()()
    assert calls == [1]


def test_missing_refunds_execution_is_logged_once(monkeypatch, caplog):
    def missing(name):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(worker_mod.importlib, "import_module", missing)
    job = _RefundsExecution()
    with caplog.at_level(logging.WARNING, logger="worker"):
        job()
        job()
    assert caplog.text.count("refunds execution not available yet") == 1
