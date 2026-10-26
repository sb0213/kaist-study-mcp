from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from kaist_study.adapters import AdapterError
from kaist_study.config import Settings
from kaist_study.web_adapters import (
    DoorayBrowserAdapter, KLMSBrowserAdapter, LoginController,
    PortalAcademicBrowserAdapter, _due_date,
)


def test_korean_due_date_uses_the_value_cell_without_splitting_the_clock():
    soup = BeautifulSoup('''<div class="submissionstatustable"><table><tr>
        <th>마감 일시</th><td>2026년 10월 29일(목요일) 오후 1:00</td>
        </tr></table></div>''', "html.parser")
    due, raw = _due_date(soup)
    assert due == "2026-10-29T13:00:00+09:00"
    assert raw == "2026년 10월 29일(목요일) 오후 1:00"


def test_course_links_exclude_global_navigation_and_support_courses():
    soup = BeautifulSoup('''<a href="/course/view.php?id=1">Common course</a>
        <div class="course_li_fullname"><a href="/course/view.php?id=2">Current</a></div>
        <div class="course_fullname"><a href="/course/view.php?id=3">Support</a></div>''', "html.parser")
    assert KLMSBrowserAdapter(None)._course_links(soup, "https://klms.kaist.ac.kr/") == [
        ("Current", "https://klms.kaist.ac.kr/course/view.php?id=2")]


def test_notices_follow_course_articles_and_paginate_without_global_boards():
    root = "https://klms.kaist.ac.kr"
    course = root + "/course/view.php?id=2"
    board = root + "/mod/courseboard/view.php?id=20"
    second = board + "&page=1"
    first_article = root + "/mod/courseboard/article.php?id=20&bwid=1"
    second_article = root + "/mod/courseboard/article.php?id=20&bwid=2"
    pages = {
        course: '''<a href="/mod/courseboard/view.php?id=1">공지사항</a>
          <div class="course-content"><a href="/mod/courseboard/view.php?id=20">Notice 게시판</a>
          <a href="/mod/courseboard/view.php?id=30">Q&amp;A</a></div>''',
        board: f'''<table class="courseboard_table"><tr><td><a href="{first_article}">First</a></td></tr></table>
          <div class="pagination"><a href="{second}">2</a></div>''',
        second: f'<table class="courseboard_table"><tr><td><a href="{second_article}">Second</a></td></tr></table>',
        first_article: '<div class="courseboard_view">First body</div>',
        second_article: '<div class="courseboard_view">Second body</div>',
    }
    adapter = KLMSBrowserAdapter(None)
    page = SimpleNamespace(url=course)
    def get(_page, url):
        page.url = url
        return BeautifulSoup(pages[url], "html.parser")
    with patch.object(adapter, "_page", return_value=(MagicMock(), MagicMock(), page)), \
         patch.object(adapter, "_get", side_effect=get):
        notices = adapter.notices("klms:course:2")
    assert [notice.title for notice in notices] == ["First", "Second"]
    assert [notice.body for notice in notices] == ["First body", "Second body"]


def test_mail_search_uses_server_search_and_preserves_pagination(tmp_path):
    adapter = DoorayBrowserAdapter(Settings(browser_profile=tmp_path))
    context = MagicMock()
    response = context.request.post.return_value
    response.url = "https://kaist.gov-dooray.com/v2/wapi/mails/search"
    response.status = 200
    response.json.return_value = {"header": {"isSuccessful": True}, "result": {
        "totalCount": 21, "contents": [{"id": "123", "subject": "Test",
          "users": {"from": {"emailUser": {"name": "Sender"}}},
          "mailSummary": {"previewText": "Preview", "flags": {"read": False}}}]}}
    context.storage_state.return_value = {"cookies": [], "origins": []}
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context)):
        result = adapter.mail_search("Sender", limit=1, page=2)
    call = context.request.post.call_args
    assert call.args[0].endswith("/mails/search")
    assert call.kwargs["data"]["all"] == ["Sender"]
    assert call.kwargs["data"]["page"] == 2
    assert result[0]["source_id"] == "dooray:mail:123"
    assert result.pagination["next_page"] == 3


def test_mail_detail_only_downloads_original_mime_without_marking_read(tmp_path):
    adapter = DoorayBrowserAdapter(Settings(browser_profile=tmp_path))
    adapter._mail_index["dooray:mail:123"] = "123"
    context = MagicMock()
    response = context.request.get.return_value
    response.url = "https://kaist.gov-dooray.com/v2/wapi/mails/123?type=raw"
    response.status = 200
    response.headers = {"content-type": "message/rfc822;charset=UTF-8"}
    response.body.return_value = b"Subject: Test\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nTest body"
    context.storage_state.return_value = {"cookies": [], "origins": []}
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context)):
        result = adapter.mail_get("dooray:mail:123")
    assert context.request.get.call_args.kwargs["params"] == {"type": "raw"}
    context.request.post.assert_not_called()
    assert result["body"] == "Test body"
    assert result["read_only"] is True


@pytest.mark.parametrize("scope", ["administration", "application", "payment", "register"])
def test_forbidden_academic_scopes_never_launch_a_browser(scope):
    adapter = PortalAcademicBrowserAdapter(Settings())
    with patch.object(adapter, "_page") as page:
        with pytest.raises(AdapterError) as error:
            adapter._read_menu((scope,))
    assert error.value.code == "permission_denied"
    page.assert_not_called()


@pytest.mark.parametrize("method", ["grades", "lectures", "graduation"])
def test_allowed_but_unverified_academic_screens_never_navigate(method):
    adapter = PortalAcademicBrowserAdapter(Settings(
        verified_grades_url="", verified_lectures_url="", verified_graduation_url=""))
    with patch.object(adapter, "_page") as page:
        with pytest.raises(AdapterError) as error:
            getattr(adapter, method)()
    assert error.value.code == "unsupported"
    page.assert_not_called()


def test_login_status_reads_a_worker_snapshot_without_browser_calls():
    controller = LoginController(Settings())
    context = MagicMock()
    controller._jobs["test"] = {"job_id": "test", "status": "portal_authenticated",
        "message": "Test", "context": context, "service_states": {"klms": "authenticated"}}
    assert controller.status("test")["service_states"] == {"klms": "authenticated"}
    assert controller.active_status()["job_id"] == "test"
    assert context.mock_calls == []


def test_academic_xhr_writes_are_blocked_before_network_access():
    adapter = PortalAcademicBrowserAdapter(Settings(verified_grades_url="https://portal.kaist.ac.kr/grades/verified"))
    page = MagicMock()
    route = MagicMock()
    route.request.url = "https://portal.kaist.ac.kr/private/payment"
    route.request.resource_type = "xhr"
    route.request.method = "POST"
    route.request.is_navigation_request.return_value = False
    def get(_page, _url):
        handler = page.route.call_args.args[1]
        handler(route)
        return BeautifulSoup("<table><tr><td>Test</td></tr></table>", "html.parser")
    with patch.object(adapter, "_page", return_value=(MagicMock(), MagicMock(), page)), \
         patch.object(adapter, "_get", side_effect=get):
        with pytest.raises(AdapterError) as error:
            adapter.grades()
    assert error.value.code == "permission_denied"
    route.abort.assert_called_once()
    route.continue_.assert_not_called()


def test_partial_course_failure_keeps_successful_results():
    adapter = KLMSBrowserAdapter(None)
    courses = [SimpleNamespace(source_id="first"), SimpleNamespace(source_id="second")]
    with patch.object(adapter, "courses", return_value=courses), \
         patch.object(adapter, "notices", side_effect=[["a notice"], AdapterError("parse_failed", "Test")]):
        result = adapter._all_courses("notices")
    assert result == ["a notice"]
    assert "second" in result.warnings[0]


def test_coursefile_index_reads_all_weeks_in_one_request():
    adapter = KLMSBrowserAdapter(None)
    context = MagicMock()
    response = context.request.get.return_value
    response.url = "https://klms.kaist.ac.kr/mod/coursefile/index.php?id=2"
    response.status = 200
    response.text.return_value = '''<div id="region-main"><table class="generaltable mod_index">
      <tr><td>Week 1</td><td><a href="view.php?id=11">File 1</a></td></tr>
      <tr><td>Week 8</td><td><a href="view.php?id=18">File 8</a></td></tr></table></div>'''
    soup, url = adapter._activity_index(context, "https://klms.kaist.ac.kr/course/view.php?id=2", "coursefile")
    assert len(soup.select("a")) == 2
    assert url.endswith("coursefile/index.php?id=2")
    context.request.get.assert_called_once()


def test_unknown_klms_index_module_is_not_accessed():
    context = MagicMock()
    with pytest.raises(AdapterError):
        KLMSBrowserAdapter(None)._activity_index(context, "https://klms.kaist.ac.kr/course/view.php?id=2", "quiz")
    context.request.get.assert_not_called()


def test_material_sections_include_other_week_folders_and_reject_foreign_selectors():
    adapter = KLMSBrowserAdapter(None)
    soup = BeautifulSoup('''<div class="course-content"><li id="section-1"></li></div>
      <div class="week-slider">
      <a href="javascript:M.course.format.dayselect(1,2,0)">1</a>
      <a href="javascript:M.course.format.dayselect(8,2,0)">8</a>
      <a href="javascript:M.course.format.dayselect(9,999,0)">foreign</a></div>''', "html.parser")
    context = MagicMock()
    response = context.request.get.return_value
    response.status = 200
    response.url = "https://klms.kaist.ac.kr/course/view.php?id=2&section=8"
    response.text.return_value = '''<div class="course-content"><li id="section-8">
      <a href="/mod/folder/view.php?id=88">Older folder</a>
      <a href="/mod/page/view.php?id=89">Older page</a></li></div>'''
    sections = list(adapter._material_sections(context, soup, "https://klms.kaist.ac.kr/course/view.php?id=2"))
    assert len(sections) == 2
    assert len(sections[1][0].select("a")) == 2
    context.request.get.assert_called_once_with(response.url, timeout=30000)
