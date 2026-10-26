"""Portable stdio entry point; install dependencies with install.py first."""

from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))


if __name__ == "__main__":
    from kaist_study.cli import app

    app(args=["serve", *sys.argv[1:]])

