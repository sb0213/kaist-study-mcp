"""Keep the dedicated browser's session across short-lived MCP calls."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .adapters import AdapterError

_profile_locks: dict[str, Any] = {}
_locks_guard = threading.Lock()


def close_browser(pw: Any, context: Any) -> None:
    try:
        if context is not None:
            context.close()
    finally:
        if pw is not None:
            pw.stop()


def _unlock_file(stream: Any) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _lock_file(profile: Path, deadline: float) -> Any:
    profile.mkdir(parents=True, exist_ok=True)
    stream = (profile / ".mcp-browser.lock").open("a+b")
    if stream.seek(0, os.SEEK_END) == 0:
        stream.write(b"0")
        stream.flush()
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return stream
            except OSError:
                if time.monotonic() >= deadline:
                    raise AdapterError("browser_busy", "별도 프로세스가 전용 브라우저를 사용 중입니다.") from None
                time.sleep(0.05)
    except Exception:
        stream.close()
        raise


class _LockedContext:
    def __init__(self, context: Any, lock: Any, stream: Any) -> None:
        self._context = context
        self._lock = lock
        self._closed = False
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._context.close()
        finally:
            try:
                _unlock_file(self._stream)
            finally:
                self._lock.release()


def launch_profile(pw: Any, profile: Path, *, restore: bool = True,
                   lock_timeout: float = 45, **options: Any) -> Any:
    """Serialize calls sharing a persistent Chromium profile until close()."""
    key = os.path.normcase(str(profile.resolve()))
    deadline = time.monotonic() + lock_timeout
    with _locks_guard:
        lock = _profile_locks.setdefault(key, threading.Lock())
    if not lock.acquire(timeout=lock_timeout):
        raise AdapterError("browser_busy", "다른 조회 또는 로그인이 같은 전용 브라우저를 사용 중입니다.")
    context = None
    stream = None
    try:
        stream = _lock_file(profile, deadline)
        context = pw.chromium.launch_persistent_context(str(profile), **options)
        if restore:
            restore_session(context, profile)
        return _LockedContext(context, lock, stream)
    except Exception:
        try:
            if context is not None:
                context.close()
        finally:
            try:
                if stream is not None:
                    _unlock_file(stream)
            finally:
                lock.release()
        raise


def save_session(context: Any, profile: Path) -> None:
    """Save session cookies too; Chromium does not retain them across launches."""
    state = context.storage_state()
    profile.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mcp-session-", dir=profile)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        os.replace(temporary, profile / ".mcp-session.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_session(context: Any, profile: Path) -> None:
    path = profile / ".mcp-session.json"
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        cookies = state["cookies"]
        origins = state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise ValueError("invalid browser state")
        context.add_cookies(cookies)
        # Persistent profiles normally retain localStorage, but explicitly
        # restore the state captured with the same authenticated cookies.
        if origins:
            context.add_init_script("""
            (() => {
                const origins = %s;
                const entry = origins.find(item => item.origin === location.origin);
                if (entry) {
                    for (const item of entry.localStorage || []) {
                        localStorage.setItem(item.name, item.value);
                    }
                }
            })();
            """ % json.dumps(origins))
    except Exception as exc:
        raise AdapterError(
            "session_restore_failed",
            "저장된 로그인 세션을 복원하지 못했습니다. login_start로 다시 로그인하세요.",
        ) from exc
