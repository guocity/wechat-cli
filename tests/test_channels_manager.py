"""Channels manager wrapper tests using an isolated fake signed-page runtime."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wechat_cli.backends.macos import channels_manager as manager
from wechat_cli.core.errors import AppError


@pytest.fixture
def backend(tmp_path, monkeypatch):
    value = SimpleNamespace(directory=tmp_path, channels_lock=threading.Lock())
    monkeypatch.setattr(manager, "load_runtime", lambda directory: {"app": "/fake/WeChat.app"})
    return value


def assert_unlocked(backend):
    assert backend.channels_lock.acquire(blocking=False)
    backend.channels_lock.release()


def install_page(monkeypatch, replies):
    calls, lifecycle = [], []
    responses = iter(replies)

    @contextmanager
    def page(backend, app, *, target):
        lifecycle.append(("enter", backend, app, target))

        def call(command):
            calls.append(command)
            return next(responses)

        try:
            yield call
        finally:
            lifecycle.append(("exit",))

    monkeypatch.setattr(manager, "upload_page", page)
    return calls, lifecycle


@pytest.mark.parametrize(
    ("invoke", "expected"),
    [
        (lambda backend: manager.list_videos(backend), {"action": "list", "limit": 20, "cursor": None}),
        (
            lambda backend: manager.list_videos(backend, limit=3, cursor="next+/=中"),
            {"action": "list", "limit": 3, "cursor": "next+/=中"},
        ),
        (manager.stats, {"action": "stats"}),
        (
            lambda backend: manager.delete_video(backend, "18446744073709551615"),
            {"action": "delete", "videoId": "18446744073709551615"},
        ),
    ],
)
def test_exact_command_forwarding(backend, monkeypatch, invoke, expected):
    result = {"status": "complete", "items": [], "nativeEvidence": "actual-result"}
    calls, lifecycle = install_page(monkeypatch, [result])
    assert invoke(backend) is result
    assert calls == [expected]
    assert lifecycle == [("enter", backend, "/fake/WeChat.app", "manager"), ("exit",)]
    assert_unlocked(backend)


def test_pending_delete_waits_for_actual_result(backend, monkeypatch):
    result = {"status": "failed", "videoId": "123", "deleted": False, "reason": "server-rejected"}
    calls, lifecycle = install_page(
        monkeypatch,
        [
            {"status": "pending", "operationId": "delete-job-1"},
            {"status": "pending", "operationId": "delete-job-1"},
            result,
        ],
    )
    sleeps = []
    monkeypatch.setattr(manager.time, "sleep", sleeps.append)
    assert manager.delete_video(backend, "123") is result
    assert calls == [
        {"action": "delete", "videoId": "123"},
        {"action": "job", "operationId": "delete-job-1"},
        {"action": "job", "operationId": "delete-job-1"},
    ]
    assert sleeps == [0.25, 0.25]
    assert lifecycle[-1] == ("exit",)
    assert_unlocked(backend)


def test_pending_timeout_preserves_uncertain_outcome(backend, monkeypatch):
    calls, lifecycle = install_page(monkeypatch, [{"status": "pending", "operationId": "job-1"}])
    clock = iter([100, 190])
    monkeypatch.setattr(manager.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(manager.time, "sleep", lambda _: pytest.fail("Already expired; do not poll again"))
    with pytest.raises(AppError) as error:
        manager.delete_video(backend, "123")
    assert error.value.code == "channels_operation_interrupted"
    assert error.value.status == 504
    assert calls == [{"action": "delete", "videoId": "123"}]
    assert lifecycle[-1] == ("exit",)
    assert_unlocked(backend)


@pytest.mark.parametrize("stage", ["runtime", "page-enter", "command", "poll", "page-exit"])
def test_failures_release_lock(backend, monkeypatch, stage):
    failure = AppError("test_failure", "Injected backend failure", 503)
    exited = []
    calls = []

    def runtime(directory):
        assert directory == backend.directory
        if stage == "runtime":
            raise failure
        return {"app": "/fake/WeChat.app"}

    @contextmanager
    def page(*args, **kwargs):
        if stage == "page-enter":
            raise failure

        def call(command):
            calls.append(command)
            if stage == "command" or stage == "poll" and command["action"] == "job":
                raise failure
            return {"status": "pending", "operationId": "job-1"} if stage == "poll" else {"status": "ready"}

        try:
            yield call
        finally:
            exited.append(True)
            if stage == "page-exit":
                raise failure

    monkeypatch.setattr(manager, "load_runtime", runtime)
    monkeypatch.setattr(manager, "upload_page", page)
    monkeypatch.setattr(manager.time, "sleep", lambda _: None)
    with pytest.raises(AppError) as error:
        manager.stats(backend)
    assert error.value is failure
    assert bool(exited) == (stage not in {"runtime", "page-enter"})
    assert_unlocked(backend)


def test_concurrent_operation_rejected_without_unlocking_owner(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    result = {"status": "complete", "items": []}
    calls = []

    @contextmanager
    def page(*args, **kwargs):
        def call(command):
            calls.append(command)
            entered.set()
            assert release.wait(3), "Test did not release the first operation"
            return result

        yield call

    monkeypatch.setattr(manager, "upload_page", page)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(manager.list_videos, backend)
        try:
            assert entered.wait(3)
            with pytest.raises(AppError) as error:
                manager.stats(backend)
            assert error.value.code == "native_control_busy"
            assert error.value.status == 409
            assert backend.channels_lock.locked()
            assert calls == [{"action": "list", "limit": 20, "cursor": None}]
        finally:
            release.set()
        assert first.result(timeout=3) is result
    assert_unlocked(backend)
