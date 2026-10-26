from pathlib import Path
from unittest.mock import patch
import pytest
from kaist_study.storage import Store
from kaist_study.adapters import AdapterError
from kaist_study.mock import MockKLMS
from kaist_study.sync import SyncService
from bs4 import BeautifulSoup
from kaist_study.web_adapters import PortalPublicAdapter, _due_date, _login_required, _validate_download


def test_filename_is_confined_and_sanitized(tmp_path: Path):
    result = Store(tmp_path).save_bytes("x", "../../secret.pdf", b"data", "https://klms.kaist.ac.kr/x")
    assert Path(result["path"]).parent == tmp_path / "files"
    assert ".." not in Path(result["path"]).name

def test_download_blocks_untrusted_host(tmp_path: Path):
    svc = SyncService(Store(tmp_path), {"klms": MockKLMS()}, ("klms.kaist.ac.kr",))
    try:
        svc.download("klms", "x", "https://example.com/file.pdf", "file.pdf", "course")
    except AdapterError as exc:
        assert exc.code == "blocked_url"
    else:
        raise AssertionError("untrusted URL was not blocked")

def test_service_sso_urls_are_not_reported_as_authenticated():
    assert _login_required("https://klms.kaist.ac.kr/login/ssologin.php", "", "")
    assert _login_required("https://kaist.gov-dooray.com/idp/multi?next=/", "", "")

def test_assignment_due_date_preserves_source_value():
    soup = BeautifulSoup('<div class="activity-dates"><div>Due date: <time datetime="2026-11-10T23:59:00+09:00">10 Nov 2026</time></div></div>', "html.parser")
    due_at, due_raw = _due_date(soup)
    assert due_at == "2026-11-10T23:59:00+09:00"
    assert due_raw == "2026-11-10T23:59:00+09:00"


def test_download_validation_rejects_login_html_for_pdf():
    try:
        _validate_download(b"<!doctype html><html>login</html>", "application/pdf", "lecture.pdf")
    except AdapterError as exc:
        assert exc.code == "not_file"
    else:
        raise AssertionError("login HTML was accepted as a PDF")


def test_download_validation_accepts_pdf_signature():
    _validate_download(b"%PDF-1.7\nbody", "application/pdf", "lecture.pdf")


def test_download_validation_rejects_html_login_form_without_html_tag():
    try:
        _validate_download(b'<form action="/sso"><input type="password"></form>', "text/plain", "download.php")
    except AdapterError as exc:
        assert exc.code == "not_file"
    else:
        raise AssertionError("a password form was accepted as a file")


def test_download_validation_rejects_invalid_zip_mime_with_disguised_name():
    try:
        _validate_download(b"not-a-zip", "application/zip", "download.php")
    except AdapterError as exc:
        assert exc.code == "not_file"
    else:
        raise AssertionError("invalid ZIP content was accepted")


def test_portal_public_search_uses_end_row_pagination():
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            start = calls[-1]["start"]
            if start == 0:
                return [{"id": index} for index in range(20)]
            if start == 21:
                return [{"id": 20}, {"id": 21}]
            return []

    def fake_get(_endpoint, *, params, timeout, follow_redirects):
        calls.append(params)
        return Response()

    with patch("kaist_study.web_adapters.httpx.get", side_effect=fake_get):
        values = PortalPublicAdapter(None).search(display=20)

    assert len(values) == 22
    assert [(call["start"], call["display"]) for call in calls] == [(0, 20), (21, 40)]


def test_portal_public_search_preserves_results_on_page_parse_failure():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": index} for index in range(20)] if len(calls) == 1 else {"data": {}}

    calls = []

    def fake_get(_endpoint, *, params, timeout, follow_redirects):
        calls.append(params)
        return Response()

    adapter = PortalPublicAdapter(None)
    with patch("kaist_study.web_adapters.httpx.get", side_effect=fake_get):
        values = adapter.search(display=20)

    assert len(values) == 20
    assert adapter.last_warnings


@pytest.mark.parametrize("payload", [{"error": "unavailable"}, None, "error", ["error"]])
def test_portal_rejects_unrecognized_response(payload):
    from unittest.mock import Mock
    response = Mock()
    response.json.return_value = payload
    with patch("kaist_study.web_adapters.httpx.get", return_value=response):
        with pytest.raises(AdapterError) as error:
            PortalPublicAdapter(None).search()
    assert error.value.code == "parse_failed"


def test_portal_filtered_empty_partial_result_retains_coverage_warning():
    from unittest.mock import Mock
    response = Mock()
    response.json.return_value = [{"id": n} for n in range(20)]
    with patch("kaist_study.web_adapters.httpx.get", side_effect=[response, TimeoutError()]):
        values = PortalPublicAdapter(None).search("no-match")
    assert values == []
    assert values.warnings


def test_portal_search_cap_and_metadata_are_request_local():
    from unittest.mock import Mock
    response = Mock()
    pages = [[{"id": n * 20 + i} for i in range(20)] for n in range(20)]
    response.json.side_effect = pages + [[]]
    adapter = PortalPublicAdapter(None)
    with patch("kaist_study.web_adapters.httpx.get", return_value=response):
        first = adapter.search()
        second = adapter.search()
    assert len(first) == 400
    assert first.warnings
    assert second == []
    assert not second.warnings
    assert first.warnings is not second.warnings

