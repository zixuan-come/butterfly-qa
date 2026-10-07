"""Polling and background writes must share the task store's lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest

from qa_agent.storage import ArtifactStore
from qa_agent.web.run_tasks import WorkflowRunTask, WorkflowRunTaskStore


@pytest.mark.parametrize("reader", ["load", "latest"])
def test_polling_serializes_with_atomic_task_updates(tmp_path, monkeypatch, reader):
    store = WorkflowRunTaskStore(tmp_path)
    now = datetime.now(timezone.utc)
    task = WorkflowRunTask(
        run_id="run-test", project_id="demo", status="running", stage="test",
        current_step="waiting", message="running", started_at=now, updated_at=now,
    )
    store.save(task)
    read_started, release, write_started = Event(), Event(), Event()
    original_read = ArtifactStore._read_json

    def blocked_read(path):
        with path.open(encoding="utf-8"):
            read_started.set()
            assert release.wait(5)
            return original_read(path)

    monkeypatch.setattr(ArtifactStore, "_read_json", staticmethod(blocked_read))

    def write():
        write_started.set()
        return store.save(task.model_copy(update={"status": "succeeded"}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(store.load, "demo", "run-test") if reader == "load" else pool.submit(store.latest, "demo")
        try:
            assert read_started.wait(5)
            acquired = store._lock.acquire(blocking=False)
            if acquired:
                store._lock.release()
            assert not acquired
            writing = pool.submit(write)
            assert write_started.wait(5)
            assert not writing.done()
        finally:
            release.set()
        assert reading.result(timeout=5).status == "running"
        assert writing.result(timeout=5).status == "succeeded"
    assert store.load("demo", "run-test").status == "succeeded"
