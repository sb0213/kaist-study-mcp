import json
from pathlib import Path
import shutil
from unittest.mock import patch
import zipfile
import uuid
from types import SimpleNamespace

import pytest

import install
from scripts.build_mcpb import build_bundle
from scripts.diagnose_stdio import REQUIRED_TOOLS, validate_result


@pytest.fixture
def release_root(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in ("src", "mcpb", "scripts", "tests"):
        shutil.copytree(root / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "install.py", "README.md", ".gitignore", ".env.example"):
        shutil.copyfile(root / name, tmp_path / name)
    return tmp_path


def test_release_contains_runtime_and_installer_but_no_private_data(release_root):
    marker = uuid.uuid4().hex
    for name in (".env", "debug.log", ".mcp-session.json", ".data/mail.json", ".venv/password.txt", "src/kaist_study/cookies.json"):
        path = release_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(marker, encoding="utf-8")
    output = build_bundle(release_root)
    for path in (output, *output.parent.glob("*-source.zip")):
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            assert all(marker.encode() not in archive.read(name) for name in archive.namelist())
            assert any(name.endswith("src/kaist_study/server.py") for name in archive.namelist())
            assert any(name.endswith("install.py") for name in archive.namelist())
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["server"]["mcp_config"]["command"] == "${user_config.python_path}"
        assert manifest["server"]["mcp_config"]["args"] == ["${__dirname}/server/entry.py"]
        assert "env" not in manifest["server"]["mcp_config"]
        assert "server/entry.py" in archive.namelist()
    assert len((output.parent / "SHA256SUMS.txt").read_text().splitlines()) == 2


def test_release_refuses_version_mismatch(release_root):
    manifest = release_root / "mcpb/manifest.json"
    value = json.loads(manifest.read_text())
    value["version"] = "9.9.9"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="versions must match"):
        build_bundle(release_root)


def test_installer_config_is_local_and_does_not_inherit_author_data(tmp_path):
    python = tmp_path / "my installation" / "python"
    config = install.client_config(python)["mcpServers"]["kaist-study"]
    assert Path(config["command"]).is_absolute()
    assert config["args"] == ["-m", "kaist_study.cli", "serve"]
    assert "env" not in config


def test_installer_keeps_virtual_environment_interpreter_symlink(tmp_path):
    system = tmp_path / "system-python"
    system.write_text("synthetic interpreter")
    python = tmp_path / "environment/bin/python"
    python.parent.mkdir(parents=True)
    try:
        python.symlink_to(system)
    except OSError:
        pytest.skip("This OS account cannot create symlinks")
    config = install.client_config(python)["mcpServers"]["kaist-study"]
    assert config["command"] == str(python.absolute())
    assert config["command"] != str(python.resolve())


def test_installer_never_dereferences_the_python_executable(tmp_path):
    python = tmp_path / "environment/bin/python"
    with patch.object(Path, "resolve", side_effect=AssertionError("would select system Python")):
        config = install.client_config(python)["mcpServers"]["kaist-study"]
    assert config["command"] == str(python.absolute())


def test_installer_refuses_existing_non_environment(tmp_path):
    existing = tmp_path / "personal-files"
    existing.mkdir()
    original = existing / "important.txt"
    original.write_text("keep")
    with patch.object(install, "run") as run, pytest.raises(SystemExit):
        install.main(["--venv", str(existing)])
    run.assert_not_called()
    assert original.read_text() == "keep"


def test_installer_python_paths_for_both_platforms(tmp_path):
    assert install.venv_python(tmp_path, windows=True) == tmp_path / "Scripts/python.exe"
    assert install.venv_python(tmp_path, windows=False) == tmp_path / "bin/python"


@pytest.mark.parametrize("envelope", [
    {"data": None, "errors": ["temporary_failure"], "coverage": "none"},
    {"data": [], "errors": [], "coverage": "full"},
    {"data": [{}], "errors": [], "coverage": "partial"},
])
def test_diagnostic_rejects_internal_failures_despite_mcp_success(envelope):
    result = SimpleNamespace(tools=[SimpleNamespace(name=name) for name in REQUIRED_TOOLS])
    status = SimpleNamespace(isError=False, structuredContent=envelope)
    with pytest.raises(RuntimeError, match="status envelope"):
        validate_result(result, status)


def test_diagnostic_checks_required_tools_and_accepts_valid_text_envelope():
    status = SimpleNamespace(isError=False, structuredContent=None, content=[SimpleNamespace(
        text=json.dumps({"data": [{"service": "klms"}], "errors": [], "coverage": "full"}))])
    with pytest.raises(RuntimeError, match="missing tools"):
        validate_result(SimpleNamespace(tools=[]), status)
    validate_result(SimpleNamespace(tools=[SimpleNamespace(name=name) for name in REQUIRED_TOOLS]), status)
