from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def now_utc() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class Attachment:
    source_id: str
    name: str
    url: str
    content_type: str | None = None
    size: int | None = None


@dataclass
class Course:
    source_id: str
    term: str
    code: str
    section: str
    name: str
    source_url: str


@dataclass
class Notice:
    source_id: str
    course_id: str
    title: str
    author: str | None
    published_at: str | None
    body: str
    source_url: str
    attachments: list[Attachment] = field(default_factory=list)
    updated_at: str | None = None


@dataclass
class Assignment:
    source_id: str
    course_id: str
    title: str
    description: str
    due_at: str | None
    source_url: str
    attachments: list[Attachment] = field(default_factory=list)
    submission_status: dict[str, Any] = field(default_factory=dict)


@dataclass
class Material:
    source_id: str
    course_id: str
    title: str
    source_url: str
    kind: str
    week: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    modified_at: str | None = None


def dump(value: Any) -> dict[str, Any]:
    return asdict(value)

