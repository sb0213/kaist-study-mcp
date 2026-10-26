from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from email import policy
from email.message import Message
from email.parser import BytesParser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .adapters import AdapterError
from .browser_session import close_browser, launch_profile, save_session
from .config import Settings
from .models import Assignment, Attachment, Course, Material, Notice


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _text_from_string(value: str | None) -> str:
    return " ".join((value or "").split())


def _absolute(base: str, href: str | None) -> str:
    return urljoin(base, href or "")


def _safe_url(url: str) -> str:
    """Remove query/fragment values before returning a URL to an MCP client."""
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()


def _validate_download(data: bytes, content_type: str, name: str) -> None:
    """Reject login/error documents masquerading as downloaded study files."""
    lowered_type = content_type.lower().split(";", 1)[0].strip()
    lowered_name = name.lower()
    sample = data[:1024].lstrip().lower()
    if not data:
        raise AdapterError("not_file", "다운로드 응답이 비어 있습니다.")
    if lowered_type in ("text/html", "application/xhtml+xml"):
        raise AdapterError("not_file", "HTML 응답을 파일로 저장하지 않았습니다.")
    if b"<html" in sample or b"<!doctype html" in sample or b"<form" in sample and b"password" in sample:
        raise AdapterError("not_file", "로그인 또는 오류 HTML을 파일로 저장하지 않았습니다.")
    if lowered_name.endswith(".pdf") or lowered_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise AdapterError("not_file", "PDF 시그니처가 없는 응답입니다.")
    elif lowered_name.endswith((".pptx", ".docx", ".xlsx", ".zip")) or lowered_type in {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        if not data.startswith(b"PK\x03\x04"):
            raise AdapterError("not_file", "Office/ZIP 파일 시그니처가 없는 응답입니다.")
    elif lowered_name.endswith(".ppt") or lowered_type in {
        "application/vnd.ms-powerpoint",
        "application/msword",
    }:
        if not data.startswith(b"\xd0\xcf\x11\xe0"):
            raise AdapterError("not_file", "구형 Office 파일 시그니처가 없는 응답입니다.")


def _parse_datetime(value: str) -> str | None:
    value = " ".join(value.replace("Z", "+00:00").split())
    korean = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?\s*(오전|오후)?\s*(\d{1,2}):(\d{2})", value)
    if korean:
        year, month, day, period, hour, minute = korean.groups()
        hours = int(hour)
        if period:
            hours = hours % 12 + (12 if period == "오후" else 0)
        try:
            return datetime(int(year), int(month), int(day), hours, int(minute),
                            tzinfo=timezone(timedelta(hours=9))).isoformat()
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
        for fmt in (
            "%A, %d %B %Y, %I:%M %p", "%d %B %Y, %I:%M %p",
            "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=9)))
    return parsed.astimezone(timezone(timedelta(hours=9))).isoformat()


def _due_date(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    labels = ("due", "due date", "마감", "마감일", "제출 기한", "제출기간")
    for node in soup.select("time[datetime]"):
        parent_text = _text(node.parent)
        if any(label in parent_text.lower() for label in labels):
            raw = node.get("datetime") or _text(node)
            return _parse_datetime(raw), raw
    for row in soup.select(".submissionstatustable tr, .activity-dates tr, tr"):
        cells = row.select("th, td")
        if len(cells) >= 2 and any(label in _text(cells[0]).lower() for label in labels):
            raw = _text(cells[1])
            return _parse_datetime(raw), raw
    for node in soup.select(".activity-dates div, p, li, dd"):
        text = _text(node)
        lowered = text.lower()
        label = next((item for item in labels if item in lowered), None)
        if not label:
            continue
        raw = text[lowered.index(label) + len(label):].lstrip(" :")
        return _parse_datetime(raw), raw
    return None, None


def _login_required(url: str, title: str, html: str) -> bool:
    # KAIST services do not all use the same SSO URL.  In particular,
    # KLMS lands on /login/ssologin.php and Dooray lands on /idp/multi.
    # Neither page is guaranteed to contain a password form, so URL-only
    # detection is required before parsing the page as authenticated content.
    if any(x in url.lower() for x in (
        "/login", "/auth/signin", "/auth/login", "/auth/sso",
        "/sso", "/idp/", "ssologin",
    )):
        return True
    title_login = any(x in title.lower() for x in ("login", "sign in", "로그인"))
    password_form = bool(re.search(r'type\s*=\s*["\']password["\']', html, re.I))
    return title_login and password_form


def _links(soup: BeautifulSoup, base: str, patterns: tuple[str, ...]) -> list[tuple[str, str]]:
    result = []
    for a in soup.select("a[href]"):
        href = _absolute(base, a.get("href"))
        if any(p in href.lower() for p in patterns):
            result.append((_text(a) or href.rsplit("/", 1)[-1], href))
    return list(dict.fromkeys(result))


class BrowserAdapter:
    """Read-only Playwright adapter using a dedicated persistent browser profile."""

    name = "browser"
    home = ""
    allowed_hosts: tuple[str, ...] = ()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def profile_path(self) -> Path:
        # One dedicated profile is intentional: KAIST SSO can hand off from
        # the portal to KLMS/Dooray. It is never the user's normal browser.
        return self.settings.browser_profile

    def _browser(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterError("missing_dependency", "Playwright가 설치되지 않았습니다: pip install -e '.[browser]'") from exc
        pw = sync_playwright().start()
        try:
            context = launch_profile(
                pw, self.profile_path, headless=True,
                accept_downloads=True, downloads_path=str(self.settings.data_dir / "downloads"),
            )
        except Exception:
            pw.stop()
            raise
        return pw, context

    def _page(self):
        pw, context = self._browser()
        page = context.pages[0] if context.pages else context.new_page()
        return pw, context, page

    def _get(self, page: Any, url: str) -> BeautifulSoup:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            raise AdapterError("temporary_failure", f"{self.name} 접속 실패: {exc}") from exc
        title = page.title()
        html = page.content()
        if _login_required(page.url, title, html):
            raise AdapterError("login_required", f"{self.name} 전용 브라우저 프로필에서 먼저 SSO 로그인이 필요합니다.")
        if response and response.status >= 400:
            raise AdapterError("http_error", f"{self.name} HTTP {response.status}")
        return BeautifulSoup(html, "html.parser")

    def status(self) -> dict[str, Any]:
        if not self.profile_path.exists():
            return {"service": self.name, "status": "login_required", "mode": "playwright", "profile": str(self.profile_path)}
        pw = context = None
        try:
            pw, context, page = self._page()
            self._get(page, self.home)
            return {"service": self.name, "status": "connected", "mode": "playwright", "url": page.url}
        except AdapterError as exc:
            return {"service": self.name, "status": exc.code, "mode": "playwright", "reason": str(exc)}
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "already in use" in lowered or "user data directory" in lowered:
                return {"service": self.name, "status": "login_in_progress", "mode": "playwright",
                        "reason": "로그인 브라우저가 아직 열려 있습니다. 로그인 완료 후 login_finish를 호출하세요."}
            return {"service": self.name, "status": "error", "mode": "playwright", "reason": message}
        finally:
            close_browser(pw, context)

    def login(self) -> None:
        """Open a headed browser. The user completes SSO/OTP themselves."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterError("missing_dependency", "Playwright가 설치되지 않았습니다.") from exc
        pw = context = None
        try:
            pw = sync_playwright().start()
            context = launch_profile(pw, self.profile_path, headless=False)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.home, wait_until="domcontentloaded", timeout=45000)
            print(f"브라우저에서 {self.name} SSO 로그인을 완료한 뒤 이 터미널에서 Enter를 누르세요.")
            input()
            save_session(context, self.profile_path)
        finally:
            close_browser(pw, context)

    def download(self, url: str) -> tuple[str, bytes, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise AdapterError("blocked_url", "허용된 HTTPS 도메인이 아닙니다.")
        pw = context = None
        try:
            pw, context, page = self._page()
            response = context.request.get(url, timeout=60000)
            if _login_required(str(response.url), "", ""):
                raise AdapterError("login_required", "다운로드 세션이 만료되어 다시 로그인해야 합니다.")
            if response.status == 401:
                raise AdapterError("login_required", "다운로드 세션에 로그인이 필요합니다.")
            if response.status == 403:
                raise AdapterError("permission_denied", "이 첨부 파일을 다운로드할 권한이 없습니다.")
            if response.status >= 400:
                raise AdapterError("download_failed", f"HTTP {response.status}")
            data = response.body()
            if len(data) > self.settings.max_download_bytes:
                raise AdapterError("too_large", "다운로드 크기 제한을 초과했습니다.")
            content_type = response.headers.get("content-type", "application/octet-stream")
            if b"<html" in data[:512].lower() and "pdf" not in content_type:
                raise AdapterError("not_file", "로그인/오류 HTML을 파일로 저장하지 않았습니다.")
            disposition = Message()
            disposition["content-disposition"] = response.headers.get("content-disposition", "")
            name = disposition.get_filename() or Path(unquote(urlparse(str(response.url)).path)).name or "download.bin"
            _validate_download(data, content_type, name)
            return name, data, content_type
        finally:
            close_browser(pw, context)


class KLMSBrowserAdapter(BrowserAdapter):
    name = "klms"
    home = "https://klms.kaist.ac.kr/"
    allowed_hosts = ("klms.kaist.ac.kr",)

    def _course_links(self, soup: BeautifulSoup, base: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        # The current-semester list excludes navigation links, common courses,
        # CELT support courses and historical course cards.
        for anchor in soup.select(".course_li_fullname a[href]"):
            href = _absolute(base, anchor.get("href"))
            if "/course/view.php" not in href.lower():
                continue
            if not re.search(r"[?&]id=\d+", href):
                continue
            result.append((_text(anchor) or href, href))
        return list(dict.fromkeys(result))

    def courses(self) -> list[Course]:
        pw = context = None
        try:
            pw, context, page = self._page(); soup = self._get(page, self.home)
            result = []
            year = _text(soup.select_one('select[name="year"] option[selected]'))
            semester = _text(soup.select_one('select[name="semester"] option[selected]'))
            term = " ".join(part for part in (year, semester) if part)
            for label, url in self._course_links(soup, page.url):
                cid = re.search(r"[?&]id=(\d+)", url)
                if not cid: continue
                source_id = f"klms:course:{cid.group(1)}"
                result.append(Course(source_id, term, "", "", label, url))
            if not result:
                raise AdapterError("parse_failed", "KLMS에서 과목 링크를 찾지 못했습니다. 페이지 구조가 계정별로 다를 수 있습니다.")
            return list({x.source_id: x for x in result}.values())
        finally:
            close_browser(pw, context)

    def _course_page(self, page: Any, course_id: str) -> tuple[BeautifulSoup, str]:
        numeric = re.search(r"(\d+)$", course_id)
        if not numeric: raise AdapterError("invalid_id", "KLMS 과목 ID가 올바르지 않습니다.")
        url = f"https://klms.kaist.ac.kr/course/view.php?id={numeric.group(1)}"
        return self._get(page, url), url

    def _course_content(self, soup: BeautifulSoup) -> Any:
        main = soup.select_one(".course-content")
        if main is None:
            raise AdapterError("parse_failed", "검증된 KLMS 강의 활동 영역을 찾지 못했습니다.")
        return main

    def _all_courses(self, method: str) -> list[Any]:
        result = PortalSearchResults()
        successes = 0
        first_error: Exception | None = None
        for course in self.courses():
            try:
                result.extend(getattr(self, method)(course.source_id))
                successes += 1
            except Exception as exc:
                first_error = first_error or exc
                result.warnings.append(f"{course.source_id}: {getattr(exc, 'code', 'temporary_failure')}: 일부 과목 조회 실패")
        if not successes and first_error:
            raise first_error
        return result

    def _activity_index(self, context: Any, base: str, module: str) -> tuple[BeautifulSoup, str]:
        """Verified whole-course indices include activities from every week."""
        if module not in ("assign", "forum", "resource", "coursefile"):
            raise AdapterError("permission_denied", "허용된 KLMS 목록 모듈이 아닙니다.")
        course = parse_qs(urlparse(base).query)["id"][0]
        url = f"https://klms.kaist.ac.kr/mod/{module}/index.php?id={course}"
        response = context.request.get(url, timeout=30000)
        if _login_required(str(response.url), "", "") or response.status == 401:
            raise AdapterError("login_required", "KLMS 활동 목록 조회에 로그인이 필요합니다.")
        if response.status == 403:
            raise AdapterError("permission_denied", "KLMS 활동 목록을 읽을 권한이 없습니다.")
        if response.status >= 400:
            raise AdapterError("temporary_failure", f"KLMS 목록 조회 HTTP {response.status}")
        soup = BeautifulSoup(response.text(), "html.parser")
        main = soup.select_one("#region-main")
        if main is None:
            raise AdapterError("parse_failed", "검증된 KLMS 목록 영역이 없습니다.")
        tables = main.select("table.generaltable")
        if not tables:
            if re.search(r"이 강좌에는 .*없습니다|there are no .* in this course", _text(main), re.I):
                return BeautifulSoup("", "html.parser"), url
            raise AdapterError("parse_failed", "KLMS 활동 목록의 표 형식이 변경됐습니다.")
        return BeautifulSoup("".join(str(table) for table in tables), "html.parser"), url

    def _pagination_urls(self, soup: BeautifulSoup, base: str) -> list[str]:
        result = []
        for anchor in soup.select("nav a[href], .pagination a[href], .paging a[href], a.page-number[href]"):
            href = _absolute(base, anchor.get("href"))
            if re.search(r"(?:[?&]page=|/page/)", href, re.I):
                result.append(href)
        return list(dict.fromkeys(result))

    def _paged_soups(self, page: Any, first: BeautifulSoup, base: str) -> list[tuple[BeautifulSoup, str]]:
        result = [(first, base)]
        queue = self._pagination_urls(first, base)[:20]
        seen = {base}
        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if len(seen) > 100:
                raise AdapterError("partial_failure", "KLMS 게시판 페이지 조회 상한에 도달했습니다.")
            parsed, initial = urlparse(url), urlparse(base)
            if parsed.scheme != "https" or parsed.netloc != initial.netloc or parsed.path != initial.path:
                raise AdapterError("blocked_url", "KLMS 게시판 범위 밖의 페이지 링크입니다.")
            if parse_qs(parsed.query).get("id") != parse_qs(initial.query).get("id"):
                raise AdapterError("blocked_url", "다른 KLMS 게시판으로의 페이지 이동입니다.")
            detail = self._get(page, url)
            result.append((detail, url))
            queue.extend(x for x in self._pagination_urls(detail, url) if x not in seen)
        return result

    def notices(self, course_id: str | None = None) -> list[Notice]:
        if not course_id:
            return self._all_courses("notices")
        pw = context = None
        try:
            pw, context, page = self._page(); soup, base = self._course_page(page, course_id)
            boards = [(title, url) for title, url in _links(
                self._course_content(soup), base, ("/mod/courseboard/view.php",)
            ) if "notice" in title.lower() or "공지" in title]
            rows = []
            for _, board_url in boards:
                board = self._get(page, board_url)
                for listed, listed_base in self._paged_soups(page, board, board_url):
                    table = listed.select_one(".courseboard_table")
                    if table is None:
                        raise AdapterError("parse_failed", "KLMS 공지 게시판 목록을 찾지 못했습니다.")
                    rows.extend(_links(table, listed_base, ("/mod/courseboard/article.php",)))
            rows = list(dict.fromkeys(rows))
            result = []
            for title, url in rows:
                detail = self._get(page, url); main = detail.select_one(".courseboard_view")
                if main is None:
                    raise AdapterError("parse_failed", "KLMS 공지 본문을 찾지 못했습니다.")
                attachments = [Attachment(f"{url}#file:{i}", name, href) for i, (name, href) in enumerate(_links(detail, page.url, ("pluginfile.php", ".pdf", ".ppt", ".pptx", ".doc", ".hwp")))]
                result.append(Notice(f"klms:notice:{hashlib.sha1(url.encode()).hexdigest()[:16]}", course_id, title, None, None, _text(main), url, attachments))
            return list({x.source_id: x for x in result}.values())
        finally:
            close_browser(pw, context)

    def assignments(self, course_id: str | None = None) -> list[Assignment]:
        if not course_id:
            return self._all_courses("assignments")
        pw = context = None
        try:
            pw, context, page = self._page(); soup, base = self._course_page(page, course_id)
            index, index_url = self._activity_index(context, base, "assign")
            rows = _links(index, index_url, ("/mod/assign/view.php",))
            result = []
            for title, url in list(dict.fromkeys(rows)):
                detail = self._get(page, url); main = detail.select_one("#region-main, main") or detail
                attachments = [Attachment(f"{url}#file:{i}", name, href) for i, (name, href) in enumerate(_links(detail, page.url, ("pluginfile.php", ".pdf", ".ppt", ".pptx", ".doc", ".hwp")))]
                due_at, due_raw = _due_date(detail)
                status: dict[str, Any] = {"raw_page": ""}
                for row in detail.select(".submissionstatustable tr"):
                    cells = row.select("th, td")
                    if len(cells) >= 2 and _text(cells[0]).lower() in ("제출 상태", "제출상태", "submission status"):
                        status["raw_page"] = _text(cells[1])
                if due_raw:
                    status["due_raw"] = due_raw
                status["submitted"] = status["raw_page"].lower() in (
                    "제출 완료", "제출됨", "submitted for grading", "submitted", "graded"
                )
                result.append(Assignment(f"klms:assignment:{hashlib.sha1(url.encode()).hexdigest()[:16]}", course_id, title, _text(main), due_at, url, attachments, status))
            return list({x.source_id: x for x in result}.values())
        finally:
            close_browser(pw, context)

    def _material_sections(self, context: Any, soup: BeautifulSoup, base: str):
        """Follow only week numbers exposed by the verified KLMS week selector."""
        yield soup, base
        course = parse_qs(urlparse(base).query)["id"][0]
        loaded = {node.get("id") for node in self._course_content(soup).select("[id^=section-]")}
        weeks = set()
        for anchor in soup.select(".week-slider a[href]"):
            match = re.fullmatch(r"javascript:M\.course\.format\.dayselect\((\d+),(\d+),0\)", anchor["href"])
            if match and match.group(2) == course:
                weeks.add(int(match.group(1)))
        if len(weeks) > 100:
            raise AdapterError("parse_failed", "KLMS 주차 목록이 조회 한도를 초과했습니다.")
        for week in sorted(weeks):
            if f"section-{week}" in loaded:
                continue
            url = f"{base}&section={week}"
            response = context.request.get(url, timeout=30000)
            html = response.text()
            if response.status == 401 or _login_required(str(response.url), "", html):
                raise AdapterError("login_required", "KLMS 주차 자료 조회에 로그인이 필요합니다.")
            if response.status == 403:
                raise AdapterError("permission_denied", "KLMS 주차 자료 접근 권한이 없습니다.")
            if response.status >= 400:
                raise AdapterError("temporary_failure", f"KLMS 주차 자료 HTTP {response.status}")
            section = BeautifulSoup(html, "html.parser")
            self._course_content(section)
            yield section, url

    def materials(self, course_id: str | None = None) -> list[Material]:
        if not course_id:
            return self._all_courses("materials")
        pw = context = None
        try:
            pw, context, page = self._page(); soup, base = self._course_page(page, course_id)
            patterns = ("/mod/resource/view.php", "/mod/folder/view.php", "/mod/page/view.php",
                        "pluginfile.php", "/mod/coursefile/view.php")
            rows = []
            for section, section_url in self._material_sections(context, soup, base):
                rows.extend(_links(self._course_content(section), section_url, patterns))
            for module in ("coursefile", "resource"):
                index, index_url = self._activity_index(context, base, module)
                rows.extend(_links(index, index_url, patterns))
            result = []
            for title, url in list(dict.fromkeys(rows)):
                direct = "pluginfile.php" in url or "/mod/resource/" in url or "/mod/coursefile/view.php" in url
                detail = None if direct else self._get(page, url)
                attachments = [Attachment(f"{url}#file:0", title, url)] if direct else [Attachment(
                    f"{url}#file:{i}", name, href
                ) for i, (name, href) in enumerate(_links(
                    detail, page.url,
                    ("pluginfile.php", ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".hwp", ".zip")
                ))]
                kind = "resource" if direct else "folder" if "/folder/" in url else "content"
                result.append(Material(
                    f"klms:material:{hashlib.sha1(url.encode()).hexdigest()[:16]}",
                    course_id, title, url, kind, None, attachments,
                ))
            return list({x.source_id: x for x in result}.values())
        finally:
            close_browser(pw, context)

    def forums(self, course_id: str | None = None) -> list[dict[str, Any]]:
        if not course_id:
            return self._all_courses("forums")
        pw = context = None
        try:
            pw, context, page = self._page()
            soup, base = self._course_page(page, course_id)
            index, index_url = self._activity_index(context, base, "forum")
            rows = _links(index, index_url, ("/mod/forum/view.php",))
            by_url: dict[str, str] = {}
            for title, url in rows:
                by_url.setdefault(url, title)
            return [{"source_id": "klms:forum:" + hashlib.sha1(url.encode()).hexdigest()[:16],
                     "course_id": course_id, "title": title, "source_url": url}
                    for url, title in by_url.items()]
        finally:
            close_browser(pw, context)


class DoorayBrowserAdapter(BrowserAdapter):
    name = "dooray"
    home = "https://kaist.gov-dooray.com/"
    allowed_hosts = ("mail.kaist.ac.kr", "kaist.gov-dooray.com")

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._mail_index: dict[str, str] = {}

    def _get(self, page: Any, url: str) -> BeautifulSoup:
        try:
            response = page.goto(url, wait_until="commit", timeout=15000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            raise AdapterError("temporary_failure", f"Dooray 로딩 시간 초과 또는 접속 실패: {exc}") from exc
        title = page.title()
        html = page.content()
        if _login_required(page.url, title, html):
            raise AdapterError("login_required", "Dooray SSO 로그인이 필요합니다.")
        if response and response.status >= 400:
            raise AdapterError("http_error", f"Dooray HTTP {response.status}")
        return BeautifulSoup(html, "html.parser")

    def _mail_rows(self, page: Any) -> list[dict[str, str]]:
        """Extract visible mail rows from Dooray's server/SPA-rendered DOM."""
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        selectors = (
            "[role='row']", "tr", "li", "[data-testid*='mail']",
            "[data-testid*='message']", "[class*='mail']", "[class*='message']",
        )
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 200)
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    text = _text_from_string(item.inner_text(timeout=1000))
                    anchors = item.locator("a[href]")
                    if anchors.count() == 0:
                        continue
                    href = anchors.first.get_attribute("href", timeout=500) or ""
                except Exception:
                    continue
                if len(text) < 8:
                    continue
                key = (text, href)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"text": text, "href": _absolute(page.url, href) if href else page.url})
        return rows

    def _mail_response(self, response: Any) -> dict[str, Any]:
        if _login_required(str(response.url), "", "") or response.status == 401:
            raise AdapterError("login_required", "Dooray 로그인이 필요합니다.")
        if response.status == 403:
            raise AdapterError("permission_denied", "Dooray 메일 조회 권한이 없습니다.")
        if response.status >= 400:
            raise AdapterError("temporary_failure", f"Dooray HTTP {response.status}")
        try:
            payload = response.json()
            result = payload["result"]
            successful = payload["header"]["isSuccessful"]
            if successful is not True or not isinstance(result, dict):
                raise ValueError("unsuccessful mail response")
            return result
        except (ValueError, KeyError, TypeError) as exc:
            raise AdapterError("parse_failed", "Dooray 메일 API 응답 형식이 변경됐습니다.") from exc

    def mail_search(self, query: str = "", limit: int = 20, page: int = 0) -> list[dict[str, Any]]:
        """Use the list/search requests observed in the authenticated mail UI."""
        if page < 0:
            raise AdapterError("invalid_page", "페이지는 0 이상이어야 합니다.")
        pw = context = None
        try:
            pw, context = self._browser()
            size = max(1, min(limit, 100))
            endpoint = self.home.rstrip("/") + "/v2/wapi/mails"
            if query:
                # This POST is a search only, not the mail read/update endpoint.
                response = context.request.post(endpoint + "/search", params={"preview": "true"},
                    data={"folderNames": ["inbox"], "all": [query], "page": page,
                          "order": "-createdAt", "highlight": True, "size": size}, timeout=30000)
            else:
                response = context.request.get(endpoint, params={"folderName": "inbox",
                    "preview": "true", "size": size, "page": page,
                    "order": "-createdAt", "view": "timeline"}, timeout=30000)
            payload = self._mail_response(response)
            rows = payload.get("contents")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise AdapterError("parse_failed", "Dooray 메일 목록 형식이 변경됐습니다.")
            result = PortalSearchResults()
            total = payload.get("totalCount")
            has_more = len(rows) == size and (not isinstance(total, int) or (page + 1) * size < total)
            result.pagination = {"page": page, "size": size, "total_count": total,
                                 "next_page": page + 1 if has_more else None}
            for row in rows:
                mid = str(row.get("id", ""))
                if not mid.isdecimal():
                    raise AdapterError("parse_failed", "Dooray 메일 ID를 찾지 못했습니다.")
                source_id = f"dooray:mail:{mid}"
                self._mail_index[source_id] = mid
                sender = row.get("users", {}).get("from", {}).get("emailUser", {})
                result.append({"source_id": source_id, "subject": row.get("subject", ""),
                    "sender": sender.get("name", ""), "received_at": row.get("createdAt"),
                    "source_url": self.home.rstrip("/") + f"/mail/popup/mails/{mid}",
                    "preview": row.get("mailSummary", {}).get("previewText", ""),
                    "read": row.get("mailSummary", {}).get("flags", {}).get("read")})
            save_session(context, self.profile_path)
            return result
        finally:
            close_browser(pw, context)

    def _unsupported_mail_get(self, source_id: str) -> dict[str, Any]:
        raise AdapterError("unsupported", "Dooray 상세 메일 선택자는 계정별 검증이 필요합니다. 먼저 mail_search 결과의 원문 링크를 확인하세요.")


    def mail_get(self, source_id: str) -> dict[str, Any]:
        mid = self._mail_index.get(source_id)
        if not mid:
            raise AdapterError("not_found", "mail_search 결과에 있는 source_id만 상세 조회할 수 있습니다.")
        pw = context = None
        try:
            pw, context = self._browser()
            # The ordinary JSON detail GET marks a mail as read. The original
            # MIME download was verified to leave both read/opened flags intact.
            response = context.request.get(self.home.rstrip("/") + f"/v2/wapi/mails/{mid}",
                                           params={"type": "raw"}, timeout=30000)
            if _login_required(str(response.url), "", "") or response.status == 401:
                raise AdapterError("login_required", "Dooray 로그인이 필요합니다.")
            if response.status >= 400:
                raise AdapterError("download_failed", f"Dooray HTTP {response.status}")
            if not response.headers.get("content-type", "").lower().startswith("message/rfc822"):
                raise AdapterError("parse_failed", "검증된 Dooray 메일 원문 형식이 아닙니다.")
            data = response.body()
            if len(data) > self.settings.max_download_bytes:
                raise AdapterError("too_large", "메일 원문 크기 제한을 초과했습니다.")
            message = BytesParser(policy=policy.default).parsebytes(data)
            body = message.get_body(preferencelist=("plain", "html"))
            content = body.get_content() if body else ""
            if body and body.get_content_type() == "text/html":
                content = _text(BeautifulSoup(content, "html.parser"))
            attachments = [{"name": part.get_filename(), "content_type": part.get_content_type()}
                           for part in message.iter_attachments()]
            save_session(context, self.profile_path)
            return {"source_id": source_id,
                    "source_url": self.home.rstrip("/") + f"/mail/popup/mails/{mid}",
                    "subject": str(message.get("Subject", "")), "body": content,
                    "attachments": attachments, "read_only": True}
        finally:
            close_browser(pw, context)


class PortalAcademicBrowserAdapter(BrowserAdapter):
    """Only explicitly verified grade, lecture and graduation screens."""

    name = "academic"
    home = "https://portal.kaist.ac.kr/"
    allowed_hosts = ("portal.kaist.ac.kr",)

    def status(self) -> dict[str, Any]:
        scopes = {"grades": self.settings.verified_grades_url,
                  "lectures": self.settings.verified_lectures_url,
                  "graduation": self.settings.verified_graduation_url}
        return {"service": self.name, "status": "configured" if any(scopes.values()) else "unverified",
                "allowed_scopes": list(scopes), "configured_scopes": [k for k, v in scopes.items() if v]}

    def _table_records(self, page: Any, source_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(page.content(), "html.parser")
        records: list[dict[str, Any]] = []
        for table in soup.select("table"):
            headers = [_text(cell) for cell in table.select("thead th")]
            for row in table.select("tbody tr") or table.select("tr"):
                cells = [_text(cell) for cell in row.select("th, td")]
                if not cells or not any(cells):
                    continue
                data = dict(zip(headers, cells)) if headers and len(headers) == len(cells) else {"columns": cells}
                data["source_url"] = _safe_url(source_url)
                data["fetched_at"] = datetime.now().astimezone().isoformat()
                records.append(data)
        return records

    def _read_menu(self, keywords: tuple[str, ...]) -> list[dict[str, Any]]:
        # Only explicitly verified destinations are permitted. Never infer
        # an academic destination from generic labels such as 신청/행정.
        scope = keywords[0] if len(keywords) == 1 else ""
        if scope not in ("grades", "lectures", "graduation"):
            raise AdapterError("permission_denied", "학사는 성적·강의·졸업만 접근할 수 있습니다.")
        target = {"grades": self.settings.verified_grades_url,
                  "lectures": self.settings.verified_lectures_url,
                  "graduation": self.settings.verified_graduation_url}[scope]
        parsed = urlparse(target)
        if not target:
            raise AdapterError("unsupported", "이 학사 화면은 아직 URL과 읽기 전용 동작이 검증되지 않았습니다.")
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts or parsed.path in ("", "/"):
            raise AdapterError("blocked_url", "검증된 학사 성적/강의/졸업 URL만 허용됩니다.")
        pw = context = None
        try:
            pw, context, page = self._page()
            blocked: list[str] = []
            def navigation_guard(route: Any) -> None:
                request = route.request
                url = request.url.split("#", 1)[0]
                parsed_request = urlparse(url)
                static = (request.resource_type in ("script", "stylesheet", "image", "font", "media")
                          and parsed_request.hostname == parsed.hostname)
                document = request.is_navigation_request() and url == target.split("#", 1)[0]
                is_login = (request.is_navigation_request() and parsed_request.hostname == "sso.kaist.ac.kr"
                            and parsed_request.path.startswith("/auth/kaist/"))
                if not ((request.method in ("GET", "HEAD") and (static or document))
                        or (is_login and request.method in ("GET", "POST"))):
                    blocked.append(url)
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", navigation_guard)
            try:
                self._get(page, target)
                if blocked:
                    raise AdapterError("permission_denied", "미검증 학사 데이터 요청을 차단했습니다.")
            except AdapterError as exc:
                if exc.code == "login_required":
                    raise
                if blocked:
                    raise AdapterError("permission_denied", "허용된 성적/강의/졸업 화면 밖으로의 이동을 차단했습니다.") from None
                raise
            records = self._table_records(page, target)
            if not records:
                raise AdapterError("parse_failed", "학사 메뉴는 열렸지만 표 형식의 데이터를 찾지 못했습니다.")
            return records
        finally:
            close_browser(pw, context)

    def grades(self, term: str | None = None, course_id: str | None = None) -> list[dict[str, Any]]:
        records = self._read_menu(("grades",))
        if term:
            records = [x for x in records if term.lower() in str(x).lower()]
        if course_id:
            records = [x for x in records if course_id.lower() in str(x).lower()]
        return records

    def administrative(self) -> list[dict[str, Any]]:
        raise AdapterError("permission_denied", "성적·강의·졸업 이외의 학사 접근은 금지되어 있습니다.")

    def lectures(self) -> list[dict[str, Any]]:
        return self._read_menu(("lectures",))

    def graduation(self) -> list[dict[str, Any]]:
        return self._read_menu(("graduation",))


class PortalLoginBrowser:
    name = "portal"
    home = "https://portal.kaist.ac.kr/"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterError("missing_dependency", "Playwright가 설치되지 않았습니다.") from exc
        self.settings.prepare()
        pw = sync_playwright().start()
        context = None
        try:
            try:
                context = launch_profile(pw, self.settings.browser_profile,
                                         headless=False, accept_downloads=True)
            except AdapterError as exc:
                if exc.code != "session_restore_failed":
                    raise
                context = launch_profile(pw, self.settings.browser_profile, restore=False,
                                         headless=False, accept_downloads=True)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.home, wait_until="domcontentloaded", timeout=45000)
            return pw, context, page
        except BaseException:
            close_browser(pw, context)
            raise

    def open_service_tabs(self, context: Any) -> None:
        # Open these only after portal SSO is complete. Otherwise Dooray/KLMS
        # can capture an unauthenticated redirect in the shared profile.
        for target in ("https://kaist.gov-dooray.com/", "https://klms.kaist.ac.kr/"):
            try:
                tab = context.new_page()
                # Do not wait for a Dooray SPA to become network-idle.  Open
                # both tabs quickly and let the login loop inspect them.
                tab.goto(target, wait_until="commit", timeout=15000)
            except Exception:
                continue

    def login(self) -> None:
        pw, context, page = self.open()
        try:
            print("포탈에서 SSO 로그인 후 Dooray(이메일)와 KLMS를 각각 한 번씩 열어 세션을 확인하세요.")
            print("로그인이 끝나면 이 터미널에서 Enter를 누르세요.")
            input()
            save_session(context, self.settings.browser_profile)
        finally:
            close_browser(pw, context)


class LoginController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self) -> dict[str, Any]:
        with self._lock:
            active = next((j for j in self._jobs.values() if j["status"] in ("starting", "portal_open", "portal_authenticated", "opening_services", "services_open", "waiting_for_services")), None)
            if active:
                return {k: active[k] for k in ("job_id", "status", "message")}
            job_id = str(uuid.uuid4())
            job = {"job_id": job_id, "status": "starting", "message": "포탈 로그인 브라우저를 여는 중입니다.", "pw": None, "context": None, "command": None}
            job["force_close"] = False
            self._jobs[job_id] = job

        def run() -> None:
            pw = context = None
            try:
                browser = PortalLoginBrowser(self.settings)
                pw, context, portal_page = browser.open()
                initially_login_required = _login_required(
                    portal_page.url, portal_page.title(), portal_page.content()
                )
                initial_cookies = {
                    (cookie["name"], cookie.get("domain"), cookie.get("path")): cookie.get("value")
                    for cookie in context.cookies()
                }
                with self._lock:
                    job.update(status="portal_open", message="포탈에서 로그인한 뒤 같은 창에서 Dooray와 KLMS를 직접 여세요.", pw=pw, context=context)
                services_opened = False
                while True:
                    if not context.pages:
                        raise AdapterError("login_cancelled", "로그인 브라우저를 닫았습니다.")
                    with self._lock:
                        command = job.get("command")
                        job["command"] = None
                        finish_requested = job["status"] == "finish_requested"
                        force_close = bool(job.get("force_close"))
                    if finish_requested:
                        states = self._service_page_states(context)
                        if force_close or all(value == "authenticated" for value in states.values()):
                            break
                        with self._lock:
                            job.update(
                                status="waiting_for_services",
                                message=(
                                    "서비스 로그인 확인이 끝나지 않았습니다. "
                                    f"현재 상태: KLMS={states['klms']}, Dooray={states['dooray']}. "
                                    "두 서비스의 실제 메인 화면을 연 뒤 login_finish를 다시 호출하세요."
                                ),
                            )
                        threading.Event().wait(0.5)
                        continue
                    with self._lock:
                        if job["status"] == "cancelled":
                            break
                    authenticated = False
                    try:
                        authenticated = not _login_required(
                            portal_page.url, portal_page.title(), portal_page.content()
                        )
                        current_cookies = {
                            (cookie["name"], cookie.get("domain"), cookie.get("path")): cookie.get("value")
                            for cookie in context.cookies()
                        }
                        cookies_changed = current_cookies != initial_cookies
                        authenticated = authenticated or cookies_changed
                        for page in context.pages:
                            if "portal.kaist.ac.kr" in page.url and not _login_required(
                                page.url, page.title(), page.content()
                            ):
                                authenticated = True
                    except Exception:
                        pass
                    if authenticated and not initially_login_required and not services_opened:
                        with self._lock:
                            job.update(status="portal_authenticated", message="포탈 로그인 세션을 확인했습니다. 같은 브라우저의 포탈 화면에서 Dooray와 KLMS를 직접 여세요.")
                    if authenticated and initially_login_required and not services_opened:
                        with self._lock:
                            job.update(status="portal_authenticated", message="포탈 로그인 완료를 확인했습니다. 같은 브라우저의 포탈 화면에서 Dooray와 KLMS를 직접 여세요.")
                    states = self._service_page_states(context)
                    with self._lock:
                        job["service_states"] = states
                    if any(value != "not_open" for value in states.values()):
                        services_opened = True
                        if all(value == "authenticated" for value in states.values()):
                            break
                    if command == "open_services":
                        browser.open_service_tabs(context)
                        services_opened = True
                        with self._lock:
                            job.update(status="services_open", message="포탈 로그인 확인 완료. Dooray와 KLMS 탭을 자동으로 열었습니다. 확인 후 login_finish를 호출하세요.")
                    threading.Event().wait(0.5)
                save_session(context, self.settings.browser_profile)
                with self._lock:
                    job.update(status="completed", message="로그인 세션을 저장했습니다.")
            except Exception as exc:
                with self._lock:
                    message = str(exc) if isinstance(exc, AdapterError) else f"로그인 처리 실패 ({type(exc).__name__})"
                    job.update(status="failed", message=message)
            finally:
                try:
                    close_browser(pw, context)
                except Exception as exc:
                    with self._lock:
                        job.update(status="failed", message=f"로그인 브라우저 정리 실패 ({type(exc).__name__})")
                finally:
                    with self._lock:
                        job.update(pw=None, context=None)

        threading.Thread(target=run, name="kaist-login", daemon=True).start()
        return {"job_id": job_id, "status": "starting", "message": "브라우저가 곧 열립니다."}

    def continue_login(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job: return {"job_id": job_id, "status": "not_found"}
            if job["status"] not in ("portal_open", "portal_authenticated", "services_open", "waiting_for_services"):
                return {"job_id": job_id, "status": job["status"], "message": "먼저 login_start를 실행하고 포탈 탭을 준비하세요."}
            job["command"] = "open_services"
            return {"job_id": job_id, "status": "opening_services", "message": "포탈 세션을 유지한 채 Dooray와 KLMS 탭을 여는 중입니다."}

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job: return {"job_id": job_id, "status": "not_found"}
            result = {k: job[k] for k in ("job_id", "status", "message")}
            # Playwright objects belong to the login worker's thread.
            # Status tools must read its snapshot rather than call page.title().
            result["service_states"] = dict(job.get("service_states", {}))
            return result

    @staticmethod
    def _service_page_states(context: Any) -> dict[str, str]:
        """Check open service tabs without navigating to them."""
        states = {"klms": "not_open", "dooray": "not_open"}
        for page in context.pages:
            host = urlparse(page.url or "").hostname
            service = "klms" if host == "klms.kaist.ac.kr" else "dooray" if (
                host in ("kaist.gov-dooray.com", "mail.kaist.ac.kr")
            ) else None
            if not service:
                continue
            try:
                authenticated = not _login_required(page.url, page.title(), page.content())
                if authenticated:
                    selector = (".course_li_fullname, .course-content" if service == "klms"
                                else '[data-testid="MailContentHeaderSearch_SearchKeyword"]')
                    authenticated = page.locator(selector).count() > 0
            except Exception:
                authenticated = False
            if authenticated:
                states[service] = "authenticated"
            elif states[service] != "authenticated":
                states[service] = "login_required"
        return states

    def active_status(self) -> dict[str, Any] | None:
        with self._lock:
            job = next((j for j in self._jobs.values() if j["status"] in (
                "starting", "portal_open", "portal_authenticated", "opening_services", "services_open", "waiting_for_services"
            )), None)
            if not job:
                return None
            return {k: job[k] for k in ("job_id", "status", "message")}

    def finish(self, job_id: str, close_browser: bool = False) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job: return {"job_id": job_id, "status": "not_found"}
            if job["status"] in ("completed", "failed", "cancelled"):
                return {k: job[k] for k in ("job_id", "status", "message")}
            job["force_close"] = close_browser
            job["status"] = "finish_requested"
            return {"job_id": job_id, "status": "finish_requested", "message": "Dooray와 KLMS 탭을 확인한 뒤 브라우저를 닫습니다."}


class PortalSearchResults(list[Any]):
    """Keep coverage metadata with its request, including concurrent searches."""

    def __init__(self) -> None:
        super().__init__()
        self.warnings: list[str] = []
        self.pagination: dict[str, Any] = {}


class PortalPublicAdapter:
    name = "portal"
    endpoint = "https://www.kaist.ac.kr/_module/api/json.php"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_warnings: list[str] = []
    def status(self) -> dict[str, Any]: return {"service": self.name, "status": "available", "mode": "public_api"}
    def _legacy_search(self, query: str = "", code: str = "0801", display: int = 20) -> list[dict[str, Any]]:
        params = {"site_dvs_cd": "kr", "code": code, "start": 0, "display": display}
        try:
            response = httpx.get(self.endpoint, params=params, timeout=30, follow_redirects=True)
            response.raise_for_status(); payload = response.json()
        except Exception as exc:
            raise AdapterError("temporary_failure", f"포탈 공개 API 조회 실패: {exc}") from exc
        values = payload if isinstance(payload, list) else payload.get("data", payload.get("list", [])) if isinstance(payload, dict) else []
        if not isinstance(values, list): values = []
        if query: values = [x for x in values if query.lower() in json.dumps(x, ensure_ascii=False).lower()]
        return values

    def search(self, query: str = "", code: str = "0801", display: int = 20) -> list[dict[str, Any]]:
        page_size = max(1, min(display, 100))
        collected = PortalSearchResults()
        seen: set[str] = set()
        self.last_warnings = collected.warnings
        try:
            for page_number in range(20):
                params = {
                    "site_dvs_cd": "kr",
                    "code": code,
                    "start": 0 if page_number == 0 else page_number * page_size + 1,
                    "display": (page_number + 1) * page_size,
                }
                response = httpx.get(self.endpoint, params=params, timeout=30, follow_redirects=True)
                response.raise_for_status()
                payload = response.json()
                values = payload if isinstance(payload, list) else (
                    payload.get("data", payload.get("list"))
                    if isinstance(payload, dict) else None
                )
                if not isinstance(values, list) or any(not isinstance(x, dict) for x in values):
                    raise AdapterError("parse_failed", "포탈 공개 공지 API 응답 목록 형식이 아닙니다.")
                if not values:
                    break
                added = 0
                for value in values:
                    key = json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not query or query.lower() in json.dumps(value, ensure_ascii=False).lower():
                        collected.append(value)
                    added += 1
                if added == 0:
                    collected.warnings.append("포탈 API가 기존 페이지를 반복 반환하여 조회를 중단했습니다.")
                    break
                if len(values) < page_size:
                    break
            else:
                collected.warnings.append("포탈 공지 조회 상한에 도달하여 이전 공지는 포함되지 않을 수 있습니다.")
            return collected
        except AdapterError as exc:
            if seen:
                collected.warnings.append(f"포탈 공지 일부 페이지 응답 오류: {exc}")
                return collected
            raise
        except Exception as exc:
            if seen:
                collected.warnings.append(f"포탈 공지 일부 페이지 조회 실패: {exc}")
                return collected
            raise AdapterError("temporary_failure", f"포탈 공개 API 조회 실패: {exc}") from exc
