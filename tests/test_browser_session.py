import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from kaist_study.adapters import AdapterError
from kaist_study.browser_session import close_browser, launch_profile, restore_session, save_session


def test_session_cookies_and_origin_state_survive_a_new_context(tmp_path):
    state = {
        "cookies": [{"name": "session", "value": "test-only", "domain": "example.test",
                     "path": "/", "expires": -1}],
        "origins": [{"origin": "https://example.test", "localStorage": [
            {"name": "key", "value": "test-only"}]}],
    }
    original = Mock()
    original.storage_state.return_value = state
    save_session(original, tmp_path)
    restored = Mock()
    restore_session(restored, tmp_path)
    restored.add_cookies.assert_called_once_with(state["cookies"])
    script = restored.add_init_script.call_args.args[0]
    assert "location.origin" in script
    assert "https://example.test" in script
    assert json.loads((tmp_path / ".mcp-session.json").read_text()) == state


def test_corrupt_session_is_reported_without_disclosing_its_content(tmp_path):
    (tmp_path / ".mcp-session.json").write_text("private-invalid-state")
    with pytest.raises(AdapterError) as error:
        restore_session(Mock(), tmp_path)
    assert error.value.code == "session_restore_failed"
    assert "private-invalid-state" not in str(error.value)


def test_missing_session_does_not_replace_browser_state(tmp_path):
    context = Mock()
    restore_session(context, tmp_path)
    context.add_cookies.assert_not_called()


def test_profile_is_locked_until_context_close(tmp_path):
    pw = Mock()
    first = launch_profile(pw, tmp_path, lock_timeout=0)
    with pytest.raises(AdapterError) as error:
        launch_profile(pw, tmp_path, lock_timeout=0)
    assert error.value.code == "browser_busy"
    first.close()
    first.close()
    second = launch_profile(pw, tmp_path, lock_timeout=0)
    second.close()


def test_launch_failure_releases_the_profile_lock(tmp_path):
    pw = Mock()
    pw.chromium.launch_persistent_context.side_effect = [RuntimeError("test"), Mock()]
    with pytest.raises(RuntimeError):
        launch_profile(pw, tmp_path, lock_timeout=0)
    launch_profile(pw, tmp_path, lock_timeout=0).close()


def test_profile_lock_also_blocks_another_process(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    holder = launch_profile(Mock(), tmp_path, lock_timeout=0)
    code = '''
from pathlib import Path
from unittest.mock import Mock
import sys
from kaist_study.browser_session import launch_profile
from kaist_study.adapters import AdapterError
try:
    launch_profile(Mock(), Path(sys.argv[1]), lock_timeout=0).close()
except AdapterError as exc:
    assert exc.code == 'browser_busy'
    sys.exit(0)
sys.exit(1)
'''
    try:
        result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    finally:
        holder.close()


def test_cleanup_stops_the_driver_even_if_context_close_fails():
    context, pw = Mock(), Mock()
    context.close.side_effect = RuntimeError("test")
    with pytest.raises(RuntimeError):
        close_browser(pw, context)
    pw.stop.assert_called_once()


def test_portal_initial_navigation_failure_releases_the_profile(tmp_path):
    from kaist_study.config import Settings
    from kaist_study.web_adapters import PortalLoginBrowser
    pw, raw, page = MagicMock(), MagicMock(), MagicMock()
    pw.chromium.launch_persistent_context.return_value = raw
    raw.pages = [page]
    page.goto.side_effect = RuntimeError("navigation failed")
    settings = Settings(data_dir=tmp_path / "data", browser_profile=tmp_path / "profile")
    with patch("playwright.sync_api.sync_playwright") as start:
        start.return_value.start.return_value = pw
        with pytest.raises(RuntimeError):
            PortalLoginBrowser(settings).open()
    raw.close.assert_called_once()
    pw.stop.assert_called_once()
    launch_profile(pw, settings.browser_profile, lock_timeout=0).close()


def test_service_cli_input_failure_releases_the_profile(tmp_path):
    from kaist_study.config import Settings
    from kaist_study.web_adapters import KLMSBrowserAdapter
    pw, raw, page = MagicMock(), MagicMock(), MagicMock()
    pw.chromium.launch_persistent_context.return_value = raw
    raw.pages = [page]
    settings = Settings(browser_profile=tmp_path)
    with patch("playwright.sync_api.sync_playwright") as start, \
         patch("builtins.input", side_effect=EOFError):
        start.return_value.start.return_value = pw
        with pytest.raises(EOFError):
            KLMSBrowserAdapter(settings).login()
    raw.close.assert_called_once()
    pw.stop.assert_called_once()
    launch_profile(pw, tmp_path, lock_timeout=0).close()


@pytest.mark.parametrize("failure", ["save", "closed"])
def test_login_worker_failure_closes_browser_and_releases_the_profile(tmp_path, failure):
    from types import SimpleNamespace
    from kaist_study.config import Settings
    from kaist_study.web_adapters import LoginController
    pw, raw, page = MagicMock(), MagicMock(), MagicMock()
    pw.chromium.launch_persistent_context.return_value = raw
    raw.pages = [page] if failure == "save" else []
    raw.cookies.return_value = []
    page.url = "https://portal.kaist.ac.kr/"
    page.title.return_value = "Portal"
    page.content.return_value = ""
    locked = launch_profile(pw, tmp_path, lock_timeout=0)
    controller = LoginController(Settings(browser_profile=tmp_path))
    def immediate(target, **kwargs):
        return SimpleNamespace(start=target)
    with patch("kaist_study.web_adapters.PortalLoginBrowser.open", return_value=(pw, locked, page)), \
         patch("kaist_study.web_adapters.threading.Thread", side_effect=immediate), \
         patch.object(controller, "_service_page_states", return_value={"klms": "authenticated", "dooray": "authenticated"}), \
         patch("kaist_study.web_adapters.save_session", side_effect=OSError("disk failure")):
        result = controller.start()
    assert controller.status(result["job_id"])["status"] == "failed"
    assert controller._jobs[result["job_id"]]["context"] is None
    pw.stop.assert_called_once()
    launch_profile(pw, tmp_path, lock_timeout=0).close()
