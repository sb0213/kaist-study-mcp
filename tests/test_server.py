from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from kaist_study.config import Settings
from kaist_study.models import Assignment, Attachment, Material
from kaist_study.server import create_server


@pytest.mark.asyncio
async def test_material_tool_serializes_nested_attachments_before_storage(tmp_path):
    klms = Mock()
    klms.materials.return_value = [Material("m", "c", "Material", "https://klms.kaist.ac.kr/m",
        "resource", attachments=[Attachment("a", "test.pdf", "https://klms.kaist.ac.kr/a.pdf")])]
    with patch("kaist_study.server.KLMSBrowserAdapter", return_value=klms):
        app = create_server(Settings(data_dir=tmp_path / "data", browser_profile=tmp_path / "profile"))
        result = await app._tool_manager.get_tool("materials_list").fn(course_id="c")
    assert result["errors"] == []
    assert result["data"][0]["attachments"][0]["name"] == "test.pdf"


@pytest.mark.asyncio
async def test_active_assignments_exclude_past_and_submitted_tasks(tmp_path):
    now = datetime.now(timezone.utc)
    klms = Mock()
    def task(identifier, due, submitted):
        return Assignment(identifier, "c", identifier, "", due.isoformat(),
                          "https://klms.kaist.ac.kr/task", submission_status={"submitted": submitted})
    klms.assignments.return_value = [task("active", now + timedelta(days=1), False),
        task("past", now - timedelta(days=1), False), task("submitted", now + timedelta(days=1), True)]
    with patch("kaist_study.server.KLMSBrowserAdapter", return_value=klms):
        app = create_server(Settings(data_dir=tmp_path / "data", browser_profile=tmp_path / "profile"))
        result = await app._tool_manager.get_tool("assignments_list").fn()
    assert result["errors"] == []
    assert [record["source_id"] for record in result["data"]] == ["active"]


@pytest.mark.asyncio
async def test_mock_connection_diagnostics_do_not_launch_real_browsers(tmp_path):
    with patch("kaist_study.server.PortalAcademicBrowserAdapter") as academic:
        app = create_server(Settings(data_dir=tmp_path / "data", browser_profile=tmp_path / "profile"), mock=True)
        result = await app._tool_manager.get_tool("connections_status").fn()
    assert result["errors"] == []
    academic.return_value.status.assert_not_called()
    assert app._tool_manager.get_tool("administrative_status") is None
