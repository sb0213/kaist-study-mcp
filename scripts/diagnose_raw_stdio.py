from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "kaist_study.cli",
        "serve",
        "--mock",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    message = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "diagnostic", "version": "1.0"},
        },
    }
    assert process.stdin is not None
    process.stdin.write((json.dumps(message) + "\n").encode())
    await process.stdin.drain()
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        response = await asyncio.wait_for(process.stdout.readline(), timeout=5)
    except TimeoutError:
        response = b"<no response within 5 seconds>"
    if process.returncode is None:
        process.terminate()
        await process.wait()
    stderr = await process.stderr.read()
    print("stdout:", response.decode(errors="replace").strip())
    print("stderr:", stderr.decode(errors="replace").strip())
    print("returncode:", process.returncode)


if __name__ == "__main__":
    asyncio.run(main())

