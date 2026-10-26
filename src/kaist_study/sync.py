from __future__ import annotations

import asyncio
import uuid
from threading import Thread
from urllib.parse import urlparse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .adapters import AcademicAdapter, AdapterError
from .extract import extract
from .models import dump
from .storage import Store


class SyncService:
    def __init__(self, store: Store, adapters: dict[str, AcademicAdapter], allowed_hosts: tuple[str, ...] = ()) -> None:
        self.store, self.adapters, self.allowed_hosts = store, adapters, set(allowed_hosts)

    def start(self, service: str = "klms") -> str:
        job_id = str(uuid.uuid4())
        self.store.job(job_id, service, "queued")
        def run() -> None:
            result = self.sync(service, job_id=job_id)
            if result.get("status") == "failed":
                self.store.job(job_id, service, "failed", result.get("error"))
        Thread(target=run, daemon=True).start()
        return job_id

    def sync(self, service: str = "klms", job_id: str | None = None) -> dict[str, Any]:
        job_id = job_id or str(uuid.uuid4()); self.store.job(job_id, service, "running")
        try:
            adapter = self.adapters[service]
            if service == "dooray" and hasattr(adapter, "mail_search"):
                mails = adapter.mail_search(limit=100)
                for mail in mails:
                    self.store.upsert("mail", mail["source_id"], mail)
                counts = {"mail": len(mails)}
                self.store.job(job_id, service, "done", str(counts))
                return {"job_id": job_id, "counts": counts}
            counts: dict[str, int] = {}
            courses = adapter.courses()
            counts["course"] = len(courses)
            for value in courses:
                self.store.upsert("course", value.source_id, dump(value))
            for typ, fn in (("notice", adapter.notices), ("assignment", adapter.assignments),
                            ("material", adapter.materials)):
                values = []
                for course in courses:
                    values.extend(fn(course.source_id))
                counts[typ] = len(values)
                for value in values:
                    self.store.upsert(typ, value.source_id, dump(value), getattr(value, "course_id", None))
            self.store.job(job_id, service, "done", str(counts)); return {"job_id": job_id, "counts": counts}
        except Exception as exc:
            self.store.job(job_id, service, "failed", str(exc)); return {"job_id": job_id, "status": "failed", "error": str(exc)}

    def download(self, service: str, source_id: str, url: str, name: str, course_id: str) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (self.allowed_hosts and parsed.hostname not in self.allowed_hosts):
            raise AdapterError("blocked_url", "HTTPS URL이 아니거나 허용된 학교 다운로드 호스트가 아닙니다.")
        adapter = self.adapters[service]
        try:
            actual_name, data, content_type = adapter.download(url)
            result = self.store.save_bytes(source_id, actual_name or name, data, url)
            try:
                for location, content in extract(result["path"]):
                    self.store.index(f"{source_id}:{result['version']}:{location}", source_id, course_id, location, content, url)
                result["extraction"] = "complete"
            except Exception as exc:
                result["extraction"] = "failed"; result["extraction_error"] = str(exc)
            result["content_type"] = content_type; return result
        except AdapterError:
            raise

