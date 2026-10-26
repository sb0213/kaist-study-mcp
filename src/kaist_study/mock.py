from __future__ import annotations

from io import BytesIO

from .adapters import AdapterError
from .models import Assignment, Attachment, Course, Material, Notice


class MockKLMS:
    name = "klms"
    def __init__(self) -> None:
        self.course = Course("klms:2026-spring:cs101:A", "2026-spring", "CS101", "A", "Computer Science", "https://klms.kaist.ac.kr/course/view.php?id=101")
        from pypdf import PdfWriter
        pdf = BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=72, height=72); writer.write(pdf)
        self.pdf = pdf.getvalue()
    def status(self): return {"service": self.name, "status": "connected", "mode": "fixture"}
    def courses(self): return [self.course]
    def notices(self, course_id=None): return [Notice("notice:1", self.course.source_id, "첫 공지", "교수", "2026-03-01T09:00:00+09:00", "읽기 전용 fixture 공지", "https://klms.kaist.ac.kr/mod/courseboard/view.php?id=1", [Attachment("att:notice:1", "notice.pdf", "https://klms.kaist.ac.kr/pluginfile.php/1/notice.pdf", "application/pdf")])]
    def assignments(self, course_id=None): return [Assignment("assign:1", self.course.source_id, "과제 1", "문제 설명", "2026-03-15T23:59:00+09:00", "https://klms.kaist.ac.kr/mod/assign/view.php?id=2", [], {"submitted": False, "graded": False, "raw": "fixture"})]
    def materials(self, course_id=None): return [Material("material:1", self.course.source_id, "Week 1", "https://klms.kaist.ac.kr/mod/resource/view.php?id=3", "resource", "1", [Attachment("att:material:1", "week1.pdf", "https://klms.kaist.ac.kr/pluginfile.php/3/week1.pdf", "application/pdf")])]
    def download(self, url):
        if "pptx" in url: raise AdapterError("fixture", "PPTX fixture not configured")
        return "fixture.pdf", self.pdf, "application/pdf"

