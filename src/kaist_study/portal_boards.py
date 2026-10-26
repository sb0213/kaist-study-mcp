"""Read-only requests verified against the authenticated portal board UI."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .adapters import AdapterError
from .browser_session import close_browser, save_session
from .web_adapters import BrowserAdapter, PortalSearchResults, _login_required, _text


def _key(value: str) -> str:
    return re.sub(r"[\s/]+", "", value)


class PortalBoardAdapter(BrowserAdapter):
    name = "portal_boards"
    home = "https://portal.kaist.ac.kr/"
    allowed_hosts = ("portal.kaist.ac.kr",)
    groups = {"공지사항": "공지사항", "학사": "학사업무", "연구": "연구업무", "행정": "행정업무"}
    specific = ("수강/학적/논문", "장학/복지", "리더십/인턴/상담", "교수학습게시판")
    search_types = {"title": "searchTtl", "content": "searchCn",
                    "title_content": "searchTtlCn", "author": "searchRegUser"}

    def _get_json(self, context: Any, path: str, params: dict[str, Any] | None = None) -> Any:
        # No write, bookmark, subscription, comment or academic-operation API
        # can be called through this adapter, even with malformed tool input.
        if not re.fullmatch(r"/wz/api/(?:widget/tabBoard/divide|board/(?:recents|\d+)(?:/\d+)?)", path):
            raise AdapterError("permission_denied", "허용된 포탈 게시판 읽기 API가 아닙니다.")
        try:
            response = context.request.get(self.home.rstrip("/") + path,
                                           params=params or {}, timeout=30000)
            if _login_required(str(response.url), "", "") or response.status == 401:
                raise AdapterError("login_required", "포탈 게시판 조회에 로그인이 필요합니다.")
            if response.status == 403:
                raise AdapterError("permission_denied", "이 포탈 게시판을 읽을 권한이 없습니다.")
            if response.status >= 400:
                raise AdapterError("temporary_failure", f"포탈 게시판 HTTP {response.status}")
            return response.json()
        except AdapterError:
            raise
        except ValueError as exc:
            raise AdapterError("parse_failed", "포탈 게시판 JSON 응답 형식이 아닙니다.") from exc
        except Exception as exc:
            raise AdapterError("temporary_failure", "포탈 게시판 요청에 실패했습니다.") from exc

    def _catalog(self, context: Any) -> list[dict[str, Any]]:
        payload = self._get_json(context, "/wz/api/widget/tabBoard/divide", {"type": "NOR"})
        try:
            rows = payload["data"]["boardAll"]
            if not isinstance(rows, list):
                raise ValueError("invalid catalog")
            allowed = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid catalog entry")
                if row.get("menuNm") not in self.groups.values():
                    continue
                path = row.get("menuPath", "")
                parsed = urlparse(path)
                if parsed.scheme or parsed.netloc or not re.match(r"^/kaist/portal/board/(ntc|acd|res|adm)/", parsed.path):
                    raise ValueError("invalid board path")
                if not str(row.get("boardNo", "")).isdecimal() or not str(row.get("menuNo", "")).isdecimal():
                    raise ValueError("invalid board identifier")
                allowed.append(row)
            if not allowed:
                raise ValueError("empty authorized catalog")
            return allowed
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError("parse_failed", "포탈의 허용된 게시판 목록을 확인하지 못했습니다.") from exc

    def boards(self) -> list[dict[str, Any]]:
        pw = context = None
        try:
            pw, context = self._browser()
            catalog = self._catalog(context)
            result = []
            for label, menu_name in self.groups.items():
                matching = [row for row in catalog if row["menuNm"] == menu_name]
                if matching:
                    result.append({"name": label, "board_count": len(matching)})
            for label in self.specific:
                if any(_key(str(row["boardNm"])) == _key(label) for row in catalog):
                    result.append({"name": label, "board_count": 1})
            return result
        finally:
            close_browser(pw, context)

    def search(self, query: str = "", board: str = "전체", page: int = 1,
               limit: int = 20, field: str = "title_content") -> PortalSearchResults:
        if page < 1 or not 1 <= limit <= 100 or field not in self.search_types:
            raise AdapterError("invalid_query", "페이지는 1 이상, 개수는 1~100이며 지원 검색 필드만 허용됩니다.")
        known = {_key(name) for name in (*self.groups, *self.specific, "전체")}
        if _key(board) not in known:
            raise AdapterError("permission_denied", "사용자가 허용한 8개 포탈 게시판 범위 밖입니다.")
        pw = context = None
        try:
            pw, context = self._browser()
            catalog = self._catalog(context)
            groups = list(self.groups) if board == "전체" else [board]
            result = PortalSearchResults()
            pages = []
            seen: set[str] = set()
            successes = 0
            first_error: AdapterError | None = None
            for label in groups:
                try:
                    group = next((name for name in self.groups if _key(name) == _key(label)), None)
                    if group:
                        rows = [row for row in catalog if row["menuNm"] == self.groups[group]]
                        if not rows:
                            raise AdapterError("unsupported", "허용된 게시판 분류를 찾지 못했습니다.")
                        path = "/wz/api/board/recents"
                        params = {"menuNo": rows[0]["menuNo"]}
                    else:
                        rows = [row for row in catalog if _key(str(row["boardNm"])) == _key(label)]
                        if len(rows) != 1:
                            raise AdapterError("unsupported", "허용된 게시판을 유일하게 식별하지 못했습니다.")
                        path = "/wz/api/board/" + str(rows[0]["boardNo"])
                        params = {}
                    params.update({"pageIndex": page, "recordCountPerPage": limit,
                                   "searchType": self.search_types[field], "searchValue": query,
                                   "searchAll": "true"})
                    payload = self._get_json(context, path, params)
                    data, pagination = payload.get("data"), payload.get("page")
                    if not isinstance(data, list) or not isinstance(pagination, dict):
                        raise AdapterError("parse_failed", "포탈 게시판 목록 또는 페이지 정보가 없습니다.")
                    paths = {str(row["boardNo"]): row["menuPath"] for row in rows}
                    for post in data:
                        if not isinstance(post, dict):
                            raise AdapterError("parse_failed", "포탈 게시물 형식이 변경됐습니다.")
                        bid, pid = str(post.get("boardNo", "")), str(post.get("pstNo", ""))
                        if bid not in paths or not pid.isdecimal():
                            raise AdapterError("permission_denied", "응답에 허용된 게시판 밖의 게시물이 있습니다.")
                        sid = f"portal:post:{bid}:{pid}"
                        if sid in seen: continue
                        seen.add(sid)
                        result.append({"source_id": sid, "board": post.get("boardNm", label),
                            "title": _text(BeautifulSoup(post.get("pstTtl", ""), "html.parser")),
                            "author": post.get("pstWrtrNm"), "published_at": post.get("regDt"),
                            "updated_at": post.get("chgDt"), "attachment_count": post.get("atchFileCnt", 0),
                            "source_url": self.home.rstrip("/") + paths[bid] + "#" + pid})
                    pages.append({"board": label, "total_count": pagination.get("totalRecord"),
                                  "next_page": page + 1 if page < int(pagination.get("totalPage", page)) else None})
                    successes += 1
                except AdapterError as exc:
                    first_error = first_error or exc
                    result.warnings.append(f"{label}: {exc.code}: {exc}")
            if not successes and first_error:
                raise first_error
            result.sort(key=lambda post: str(post.get("published_at", "")), reverse=True)
            result.pagination = {"page": page, "page_size_per_board": limit, "boards": pages,
                                 "next_page": page + 1 if any(p["next_page"] for p in pages) else None}
            save_session(context, self.profile_path)
            return result
        finally:
            close_browser(pw, context)

    def post(self, source_id: str) -> dict[str, Any]:
        match = re.fullmatch(r"portal:post:(\d+):(\d+)", source_id)
        if not match:
            raise AdapterError("invalid_id", "포탈 검색 결과의 게시물 ID가 필요합니다.")
        bid, pid = match.groups()
        pw = context = None
        try:
            pw, context = self._browser()
            catalog = self._catalog(context)
            board = next((row for row in catalog if str(row["boardNo"]) == bid), None)
            if board is None:
                raise AdapterError("permission_denied", "허용된 포탈 게시판 밖의 게시물입니다.")
            post = self._get_json(context, "/wz/api/board/recents/" + pid,
                                  {"boardNo": bid, "menuNo": board["menuNo"]})
            if not isinstance(post, dict) or str(post.get("boardNo")) != bid or str(post.get("pstNo")) != pid:
                raise AdapterError("parse_failed", "포탈 게시물 본문을 확인하지 못했습니다.")
            if not isinstance(post.get("pstCn"), str):
                raise AdapterError("permission_denied", "이 게시물의 본문을 읽을 권한이 없습니다.")
            soup = BeautifulSoup(post["pstCn"], "html.parser")
            def content_url(value: str) -> str | None:
                url = urljoin(self.home, value)
                return url if urlparse(url).scheme in ("http", "https") else None
            images = [url for node in soup.select("img[src]")
                      if (url := content_url(str(node.get("src", ""))))]
            links = [{"title": _text(node), "url": url} for node in soup.select("a[href]")
                     if (url := content_url(str(node.get("href", ""))))]
            attachments = []
            for item in post.get("boardAtchList", []):
                fid = str(item.get("atchFileNo", ""))
                if fid.isdecimal():
                    attachments.append({"name": item.get("orgnlFileNm") or item.get("fileNm") or f"attachment-{fid}",
                        "source_url": self.home.rstrip("/") + f"/wz/api/board/{bid}/{pid}/download/{fid}"})
            body = _text(soup)
            warnings = []
            if not body and images:
                warnings.append("이미지로 된 본문입니다. 이미지 링크를 제공하며 OCR은 수행하지 않았습니다.")
            elif not body:
                warnings.append("포탈이 텍스트 본문을 반환하지 않았습니다. 원문과 첨부를 확인하세요.")
            return {"source_id": source_id, "board": board["boardNm"],
                    "title": post.get("pstTtl", ""),
                    "body": body, "body_status": "text" if body else "image_only" if images else "empty",
                    "images": images, "links": links, "attachments": attachments, "warnings": warnings,
                    "author": post.get("pstWrtrNm"), "published_at": post.get("regDt"),
                    "updated_at": post.get("chgDt"),
                    "attachment_count": post.get("atchFileCnt", 0),
                    "source_url": self.home.rstrip("/") + board["menuPath"] + "#" + pid}
        finally:
            close_browser(pw, context)
