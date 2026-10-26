from __future__ import annotations

import asyncio
import argparse
import os
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REQUIRED_TOOLS = frozenset("""connections_status login_start login_status login_continue
login_finish login_close courses_list notices_search notice_get assignments_list
assignment_get materials_list material_get forums_list material_download materials_search
portal_notices_search portal_boards_list portal_search portal_post_get mail_search mail_get
sync_start sync_status grades_list academic_lectures_list graduation_status""".split())


def validate_result(result, status) -> None:
    if {tool.name for tool in result.tools} != REQUIRED_TOOLS:
        raise RuntimeError("stdio diagnostic failed: unexpected or missing tools")
    if status.isError:
        raise RuntimeError("stdio diagnostic failed: MCP tool error")
    value = status.structuredContent
    if value is None:
        try:
            value = json.loads(status.content[0].text)
        except (IndexError, AttributeError, ValueError) as exc:
            raise RuntimeError("stdio diagnostic failed: invalid status envelope") from exc
    if (not isinstance(value, dict) or value.get("errors")
            or value.get("coverage") != "full" or not isinstance(value.get("data"), list)
            or not value["data"]):
        raise RuntimeError("stdio diagnostic failed: status envelope contains errors or missing data")


async def main(installed: bool = False) -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    if installed:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(root / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kaist_study.cli", "serve", "--mock"],
        env=env,
    )
    async with stdio_client(params, errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print(f"initialized; tools={len(result.tools)}")
            print(", ".join(tool.name for tool in result.tools))
            status = await session.call_tool("connections_status")
            print(f"connections_status_error={status.isError}")
            validate_result(result, status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true", help="Check installed package without adding source paths")
    asyncio.run(main(installed=parser.parse_args().installed))

