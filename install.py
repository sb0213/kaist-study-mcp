"""Install on the recipient's PC without editing an existing MCP client config."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def venv_python(directory: Path, *, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return directory / ("Scripts/python.exe" if windows else "bin/python")


def client_config(python: Path) -> dict:
    return {"mcpServers": {"kaist-study": {
        # Resolving a POSIX venv's Python symlink selects the system interpreter.
        "command": str(python.absolute()),
        "args": ["-m", "kaist_study.cli", "serve"],
    }}}


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, help="Virtual environment path (default: .venv beside install.py)")
    parser.add_argument("--skip-browser-install", action="store_true", help="Skip Chromium download; browser tools will require a separate install")
    options = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required. Install Python, then run this script again.")
    root = Path(__file__).resolve().parent
    directory = (options.venv or root / ".venv").expanduser().resolve()
    if directory == Path(sys.prefix).resolve():
        parser.error("Run this installer with a Python outside the target virtual environment.")
    if directory.exists() and not (directory / "pyvenv.cfg").is_file():
        parser.error("The target exists and is not a virtual environment; choose an empty path with --venv.")
    if not directory.exists():
        venv.EnvBuilder(with_pip=True).create(directory)
    python = venv_python(directory)
    run([str(python), "-m", "pip", "install", f"{root}[browser]"])
    if not options.skip_browser_install:
        run([str(python), "-m", "playwright", "install", "chromium"])
        run([str(python), "-c", "from playwright.sync_api import sync_playwright; "
             "p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()"], timeout=60)
    # Verify the installed package outside the source tree, without any real account.
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="kaist-install-check-") as temporary:
        env.update(KAIST_DATA_DIR=str(Path(temporary) / "data"),
                   KAIST_BROWSER_PROFILE=str(Path(temporary) / "profile"))
        run([str(python), str(root / "scripts" / "diagnose_stdio.py"), "--installed"],
            cwd=temporary, env=env, timeout=120)
    output = root / ".install"
    output.mkdir(exist_ok=True)
    config_path = output / "claude-desktop-config.json"
    config_path.write_text(json.dumps(client_config(python), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nPython executable: {python}")
    print(f"MCP config fragment: {config_path}")
    print("Merge this fragment into your MCP client configuration, or enter the Python path in the MCPB extension settings.")
    print("Existing client configuration was not changed. Restart the client and use login_start with your own KAIST account.")
    if options.skip_browser_install:
        print(f'Chromium installation SKIPPED. Run: "{python}" -m playwright install chromium')


if __name__ == "__main__":
    try:
        main()
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
