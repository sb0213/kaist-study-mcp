from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Any
from mcp.server.fastmcp import FastMCP
from . import __version__
from .config import Settings
from .adapters import UnavailableAdapter
from .mock import MockKLMS
from .storage import Store
from .sync import SyncService
from .models import dump
from .portal_boards import PortalBoardAdapter
from .web_adapters import DoorayBrowserAdapter, KLMSBrowserAdapter, LoginController, PortalAcademicBrowserAdapter, PortalPublicAdapter

IMPLEMENTATION_VERSION = __version__

def envelope(data: Any, *, source: str, warnings=None, errors=None, coverage: str = "full"):
    if isinstance(data, dict) and data.get("no_followup_required"):
        data["instruction"] = "포탈 로그인 후 같은 창에서 KLMS와 Dooray를 여세요. 두 서비스의 로그인 화면을 확인하면 세션을 저장합니다."
    return {"source": source, "source_id": None, "source_url": None,
            "implementation_version": IMPLEMENTATION_VERSION,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "last_successful_sync_at": None, "freshness": "live", "data": data,
            "warnings": warnings or [], "coverage": coverage, "errors": errors or []}

def _err(source: str, exc: Exception):
    return envelope(None, source=source, errors=[f"{getattr(exc, 'code', 'error')}: {exc}"], coverage="none")

def create_server(settings: Settings | None = None, *, mock: bool = False) -> FastMCP:
    settings = settings or Settings(); settings.prepare()
    adapters = {"klms": MockKLMS() if mock else KLMSBrowserAdapter(settings),
                "portal": PortalPublicAdapter(settings),
                "academic": PortalAcademicBrowserAdapter(settings),
                "dooray": UnavailableAdapter("dooray", "mock 모드에서는 Dooray를 조회하지 않습니다.") if mock else DoorayBrowserAdapter(settings)}
    store = Store(settings.data_dir)
    sync = SyncService(store, adapters, settings.allowed_download_hosts)
    login_controller = LoginController(settings)
    portal_boards = PortalBoardAdapter(settings)
    mcp = FastMCP("kaist-study", host=settings.http_host, port=settings.http_port)

    @mcp.tool()
    async def connections_status() -> dict[str, Any]:
        try:
            active_login = login_controller.active_status()
            if active_login:
                values = [
                    {"service": "klms", "status": "login_in_progress", "mode": "playwright",
                     "job_id": active_login["job_id"], "reason": active_login["message"]},
                    {"service": "portal", "status": "login_in_progress", "mode": "playwright",
                     "job_id": active_login["job_id"], "reason": active_login["message"]},
                    {"service": "dooray", "status": "login_in_progress", "mode": "playwright",
                     "job_id": active_login["job_id"], "reason": active_login["message"]},
                ]
                return envelope(values, source="local")
            values = await asyncio.to_thread(lambda: [
                ({"service": "academic", "status": "unavailable", "mode": "fixture"}
                 if mock and name == "academic" else adapter.status())
                for name, adapter in adapters.items()])
            return envelope(values, source="local")
        except Exception as exc: return _err("local", exc)

    @mcp.tool()
    def login_start() -> dict[str, Any]:
        try:
            result = login_controller.start()
            result["flow"] = "portal_only"
            result["automatic_service_tabs"] = False
            result["no_followup_required"] = True
            return envelope(result, source="local")
        except Exception as exc: return _err("local", exc)

    @mcp.tool()
    def login_status(job_id: str) -> dict[str, Any]:
        return envelope(login_controller.status(job_id), source="local")

    @mcp.tool()
    def login_continue(job_id: str) -> dict[str, Any]:
        return envelope(login_controller.continue_login(job_id), source="local")

    @mcp.tool()
    def login_finish(job_id: str) -> dict[str, Any]:
        return envelope(login_controller.finish(job_id, close_browser=False), source="local")

    @mcp.tool()
    def login_close(job_id: str) -> dict[str, Any]:
        return envelope(login_controller.finish(job_id, close_browser=True), source="local")

    @mcp.tool()
    async def courses_list(refresh: bool = True) -> dict[str, Any]:
        if not refresh: return envelope(store.list_records("course"), source="klms")
        try:
            values = await asyncio.to_thread(adapters["klms"].courses)
            for v in values: store.upsert("course", v.source_id, dump(v))
            return envelope([dump(v) for v in values], source="klms")
        except Exception as exc: return _err("klms", exc)

    @mcp.tool()
    async def notices_search(course_id: str | None = None, refresh: bool = True) -> dict[str, Any]:
        if not refresh: return envelope(store.list_records("notice", course_id), source="klms")
        try:
            values = await asyncio.to_thread(adapters["klms"].notices, course_id)
            for v in values: store.upsert("notice", v.source_id, dump(v), v.course_id)
            warnings = getattr(values, "warnings", [])
            return envelope([dump(v) for v in values], source="klms", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc: return _err("klms", exc)

    @mcp.tool()
    def notice_get(source_id: str): return envelope(store.get_record("notice", source_id), source="klms")

    @mcp.tool()
    async def assignments_list(course_id: str | None = None, refresh: bool = True, active_only: bool = True):
        """List current unsubmitted assignments; active_only=false includes past assignments."""
        try:
            if refresh:
                values = await asyncio.to_thread(adapters["klms"].assignments, course_id)
                records = [dump(v) for v in values]
                for record in records:
                    store.upsert("assignment", record["source_id"], record, record["course_id"])
            else:
                records = store.list_records("assignment", course_id)
            warnings = list(getattr(values, "warnings", [])) if refresh else []
            if active_only:
                active = []
                for record in records:
                    if record.get("submission_status", {}).get("submitted"):
                        continue
                    if record.get("due_at"):
                        due = datetime.fromisoformat(record["due_at"])
                        if due < datetime.now().astimezone():
                            continue
                    else:
                        warnings.append("마감일을 확인하지 못한 과제를 진행 목록에 포함했습니다.")
                    active.append(record)
                records = active
            return envelope(records, source="klms", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc: return _err("klms", exc)

    @mcp.tool()
    def assignment_get(source_id: str): return envelope(store.get_record("assignment", source_id), source="klms")

    @mcp.tool()
    async def materials_list(course_id: str | None = None, refresh: bool = True):
        if not refresh: return envelope(store.list_records("material", course_id), source="klms")
        try:
            values = await asyncio.to_thread(adapters["klms"].materials, course_id)
            for v in values: store.upsert("material", v.source_id, dump(v), v.course_id)
            warnings = getattr(values, "warnings", [])
            return envelope([dump(v) for v in values], source="klms", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc: return _err("klms", exc)

    @mcp.tool()
    def material_get(source_id: str): return envelope(store.get_record("material", source_id), source="klms")

    @mcp.tool()
    async def forums_list(course_id: str | None = None):
        """Read KLMS forum activity links; never submit or edit a discussion."""
        if mock:
            return envelope([], source="klms", warnings=["모의 모드는 포럼을 제공하지 않습니다."])
        try:
            values = await asyncio.to_thread(adapters["klms"].forums, course_id)
            warnings = getattr(values, "warnings", [])
            return envelope(values, source="klms", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc:
            return _err("klms", exc)

    @mcp.tool()
    async def material_download(source_id: str, url: str, name: str, course_id: str):
        try: return envelope(await asyncio.to_thread(sync.download, "klms", source_id, url, name, course_id), source="klms")
        except Exception as exc: return _err("klms", exc)

    @mcp.tool()
    def materials_search(query: str, limit: int = 20): return envelope(store.search(query, limit), source="local")

    @mcp.tool()
    async def portal_notices_search(query: str = "", code: str = "0801"):
        try:
            values = await asyncio.to_thread(adapters["portal"].search, query, code)
            warnings = getattr(values, "warnings", [])
            return envelope(values, source="portal", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc: return _err("portal", exc)

    @mcp.tool()
    async def portal_boards_list():
        """List the eight authorized portal board categories, separate from academic operations."""
        try:
            return envelope(await asyncio.to_thread(portal_boards.boards), source="portal")
        except Exception as exc: return _err("portal", exc)

    @mcp.tool()
    async def portal_search(query: str = "", board: str = "전체", page: int = 1,
                            limit: int = 20, field: str = "title_content"):
        """Search portal boards by title/content/author. page/limit apply to each category."""
        try:
            values = await asyncio.to_thread(portal_boards.search, query, board, page, limit, field)
            result = envelope(values, source="portal", warnings=values.warnings,
                              coverage="partial" if values.warnings else "full")
            result["pagination"] = values.pagination
            return result
        except Exception as exc: return _err("portal", exc)

    @mcp.tool()
    async def portal_post_get(source_id: str):
        """Read a portal board post without posting, bookmarking or subscribing."""
        try:
            value = await asyncio.to_thread(portal_boards.post, source_id)
            warnings = value.get("warnings", [])
            return envelope(value, source="portal", warnings=warnings,
                            coverage="partial" if warnings else "full")
        except Exception as exc: return _err("portal", exc)

    @mcp.tool()
    async def mail_search(query: str = "", limit: int = 20, page: int = 0):
        """Search the inbox on the server; page starts at 0, newest mail first."""
        try:
            values = await asyncio.to_thread(adapters["dooray"].mail_search, query, limit, page)
            result = envelope(values, source="dooray")
            result["pagination"] = getattr(values, "pagination", {})
            return result
        except Exception as exc: return _err("dooray", exc)

    @mcp.tool()
    async def mail_get(source_id: str):
        try: return envelope(await asyncio.to_thread(adapters["dooray"].mail_get, source_id), source="dooray")
        except Exception as exc: return _err("dooray", exc)

    @mcp.tool()
    def sync_start(service: str = "klms"): return envelope({"job_id": sync.start(service), "status": "queued"}, source=service)
    @mcp.tool()
    def sync_status(job_id: str): return envelope(store.job_status(job_id), source="local")
    @mcp.tool()
    def grades_list(term: str | None = None, course_id: str | None = None):
        try:
            values = adapters["academic"].grades(term, course_id)
            return envelope(values, source="academic")
        except Exception as exc:
            return _err("academic", exc)

    @mcp.tool()
    def academic_lectures_list():
        """Read only an explicitly verified academic lecture screen."""
        try:
            return envelope(adapters["academic"].lectures(), source="academic")
        except Exception as exc:
            return _err("academic", exc)

    @mcp.tool()
    def graduation_status():
        """Read only an explicitly verified graduation screen."""
        try:
            return envelope(adapters["academic"].graduation(), source="academic")
        except Exception as exc:
            return _err("academic", exc)

    return mcp
