from unittest.mock import MagicMock, patch

import pytest

from kaist_study.adapters import AdapterError
from kaist_study.config import Settings
from kaist_study.portal_boards import PortalBoardAdapter


def catalog():
    labels = [(1, "통합공지", 21, "공지사항", "ntc"),
              (2, "연구자료실", 24, "연구업무", "res"),
              (3, "행정공지", 23, "행정업무", "adm"),
              (31, "수강/학적/논문", 22, "학사업무", "acd"),
              (28, "장학/복지", 22, "학사업무", "acd"),
              (47, "리더십/인턴/상담", 22, "학사업무", "acd"),
              (1019, "교수학습게시판", 22, "학사업무", "acd"),
              (999, "허용되지않음", 28, "일반게시판", "gnr")]
    return {"data": {"boardAll": [
        {"boardNo": bid, "boardNm": name, "menuNo": mid, "menuNm": menu,
         "menuPath": f"/kaist/portal/board/{path}/{bid}"}
        for bid, name, mid, menu, path in labels]}}


def context():
    value = MagicMock()
    value.storage_state.return_value = {"cookies": [], "origins": []}
    return value


def test_eight_categories_are_discovered_from_the_authorized_catalog(tmp_path):
    adapter = PortalBoardAdapter(Settings(browser_profile=tmp_path))
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context())), \
         patch.object(adapter, "_get_json", return_value=catalog()):
        boards = adapter.boards()
    assert len(boards) == 8
    assert {row["name"] for row in boards} == set(adapter.groups) | set(adapter.specific)


def test_server_search_parameters_and_pagination_are_forwarded(tmp_path):
    adapter = PortalBoardAdapter(Settings(browser_profile=tmp_path))
    ctx = context()
    listing = {"data": [{"boardNo": 31, "pstNo": 123, "pstTtl": "A match", "regDt": "2026-10-01"}],
               "page": {"totalRecord": 50, "totalPage": 5}}
    with patch.object(adapter, "_browser", return_value=(MagicMock(), ctx)), \
         patch.object(adapter, "_get_json", side_effect=[catalog(), listing]) as get:
        result = adapter.search("match", "수강/학적/논문", page=2, limit=10, field="author")
    params = get.call_args.args[2]
    assert params["searchValue"] == "match"
    assert params["searchType"] == "searchRegUser"
    assert params["pageIndex"] == 2
    assert result.pagination["next_page"] == 3
    assert result[0]["source_id"] == "portal:post:31:123"
    ctx.request.post.assert_not_called()


def test_partial_board_failure_keeps_successful_rows(tmp_path):
    adapter = PortalBoardAdapter(Settings(browser_profile=tmp_path))
    def get(_context, path, params):
        if "tabBoard" in path:
            return catalog()
        if params["menuNo"] == 24:
            raise AdapterError("temporary_failure", "Test-only failure")
        bid = {21: 1, 22: 31, 23: 3}[params["menuNo"]]
        return {"data": [{"boardNo": bid, "pstNo": bid, "pstTtl": "Test"}],
                "page": {"totalRecord": 1, "totalPage": 1}}
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context())), \
         patch.object(adapter, "_get_json", side_effect=get):
        result = adapter.search()
    assert len(result) == 3
    assert len(result.warnings) == 1
    assert "temporary_failure" in result.warnings[0]


def test_image_post_retains_images_and_attachments(tmp_path):
    adapter = PortalBoardAdapter(Settings(browser_profile=tmp_path))
    post = {"boardNo": 31, "pstNo": 123, "pstTtl": "Poster", "pstCn": '<img src="/poster.png">',
            "boardAtchList": [{"atchFileNo": 8, "orgnlFileNm": "poster.pdf"}]}
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context())), \
         patch.object(adapter, "_get_json", side_effect=[catalog(), post]):
        result = adapter.post("portal:post:31:123")
    assert result["body_status"] == "image_only"
    assert result["images"] == ["https://portal.kaist.ac.kr/poster.png"]
    assert result["attachments"][0]["name"] == "poster.pdf"
    assert result["warnings"]


def test_unknown_categories_and_write_routes_are_blocked_before_request():
    adapter = PortalBoardAdapter(Settings())
    with patch.object(adapter, "_browser") as browser:
        with pytest.raises(AdapterError):
            adapter.search(board="개인신청업무")
    browser.assert_not_called()
    ctx = context()
    with pytest.raises(AdapterError):
        adapter._get_json(ctx, "/wz/api/mypage/bookmark/save")
    ctx.request.get.assert_not_called()


def test_post_outside_authorized_categories_is_not_requested(tmp_path):
    adapter = PortalBoardAdapter(Settings(browser_profile=tmp_path))
    with patch.object(adapter, "_browser", return_value=(MagicMock(), context())), \
         patch.object(adapter, "_get_json", return_value=catalog()) as get:
        with pytest.raises(AdapterError) as error:
            adapter.post("portal:post:999:123")
    assert error.value.code == "permission_denied"
    assert get.call_count == 1
