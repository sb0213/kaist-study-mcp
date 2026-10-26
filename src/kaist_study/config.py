from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv(
        "KAIST_DATA_DIR", user_data_dir("kaist-study", "KAIST")
    )))
    timezone: str = os.getenv("KAIST_TIMEZONE", "Asia/Seoul")
    sync_interval_minutes: int = int(os.getenv("KAIST_SYNC_INTERVAL_MINUTES", "60"))
    max_concurrency: int = int(os.getenv("KAIST_MAX_CONCURRENCY", "2"))
    max_download_bytes: int = int(os.getenv("KAIST_MAX_DOWNLOAD_BYTES", str(50 * 1024 * 1024)))
    http_host: str = os.getenv("KAIST_HTTP_HOST", "127.0.0.1")
    http_port: int = int(os.getenv("KAIST_HTTP_PORT", "8765"))
    enable_http: bool = os.getenv("KAIST_ENABLE_HTTP", "false").lower() == "true"
    verified_grades_url: str = field(default_factory=lambda: os.getenv("KAIST_VERIFIED_GRADES_URL", ""))
    verified_lectures_url: str = field(default_factory=lambda: os.getenv("KAIST_VERIFIED_LECTURES_URL", ""))
    verified_graduation_url: str = field(default_factory=lambda: os.getenv("KAIST_VERIFIED_GRADUATION_URL", ""))
    browser_profile: Path = field(default_factory=lambda: Path(os.getenv(
        "KAIST_BROWSER_PROFILE", str(Path(user_data_dir("kaist-study", "KAIST")) / "browser-profile")
    )))
    allowed_download_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip().lower() for h in os.getenv(
            "KAIST_ALLOWED_DOWNLOAD_HOSTS",
            "klms.kaist.ac.kr,portal.kaist.ac.kr,mail.kaist.ac.kr,kaist.gov-dooray.com",
        ).split(",") if h.strip()
    ))

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "files").mkdir(exist_ok=True)
        self.browser_profile.parent.mkdir(parents=True, exist_ok=True)

