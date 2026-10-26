from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .models import Assignment, Course, Material, Notice


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message); self.code = code


class AcademicAdapter(Protocol):
    name: str
    def status(self) -> dict[str, Any]: ...
    def courses(self) -> list[Course]: ...
    def notices(self, course_id: str | None = None) -> list[Notice]: ...
    def assignments(self, course_id: str | None = None) -> list[Assignment]: ...
    def materials(self, course_id: str | None = None) -> list[Material]: ...
    def download(self, url: str) -> tuple[str, bytes, str]: ...


@dataclass
class UnavailableAdapter:
    name: str
    reason: str
    def status(self) -> dict[str, Any]:
        return {"service": self.name, "status": "unavailable", "reason": self.reason}
    def _fail(self) -> None: raise AdapterError("unsupported", self.reason)
    def courses(self) -> list[Course]: self._fail()
    def notices(self, course_id: str | None = None) -> list[Notice]: self._fail()
    def assignments(self, course_id: str | None = None) -> list[Assignment]: self._fail()
    def materials(self, course_id: str | None = None) -> list[Material]: self._fail()
    def download(self, url: str) -> tuple[str, bytes, str]: self._fail()

