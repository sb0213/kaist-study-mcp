from pathlib import Path

from kaist_study.mock import MockKLMS
from kaist_study.storage import Store
from kaist_study.sync import SyncService


def test_sync_is_idempotent_and_preserves_versions(tmp_path: Path):
    store = Store(tmp_path)
    svc = SyncService(store, {"klms": MockKLMS()}, ("klms.kaist.ac.kr",))
    first = svc.sync(); second = svc.sync()
    assert first["counts"] == second["counts"] == {"course": 1, "notice": 1, "assignment": 1, "material": 1}
    result1 = svc.download("klms", "att:1", "https://klms.kaist.ac.kr/a.pdf", "same.pdf", "course")
    assert result1["extraction"] == "complete"
    result2 = svc.download("klms", "att:1", "https://klms.kaist.ac.kr/a.pdf", "same.pdf", "course")
    assert result1["version"] == result2["version"] == 1
    assert result2["deduplicated"] is True


class FakeDooray:
    def mail_search(self, limit=20):
        return [{"source_id": "dooray:mail:1", "subject": "공지", "preview": "내용", "source_url": "https://kaist.gov-dooray.com/mail/1"}]


def test_dooray_sync_does_not_call_courses(tmp_path: Path):
    store = Store(tmp_path)
    result = SyncService(store, {"dooray": FakeDooray()}).sync("dooray")
    assert result["counts"] == {"mail": 1}
    assert store.list_records("mail")[0]["source_id"] == "dooray:mail:1"


def test_changed_attachment_keeps_both_versions(tmp_path: Path):
    store = Store(tmp_path)
    first = store.save_bytes("file:1", "notes.pdf", b"original", "https://klms.kaist.ac.kr/notes.pdf")
    second = store.save_bytes("file:1", "notes.pdf", b"revised", "https://klms.kaist.ac.kr/notes.pdf")
    assert first["version"] == 1
    assert second["version"] == 2
    assert first["sha256"] != second["sha256"]
    assert Path(first["path"]).read_bytes() == b"original"
    assert Path(second["path"]).read_bytes() == b"revised"

