"""
Import an honors roster (CSV) into a roster session.

Each row is matched to an existing student by R number, then TTU email, then
exact normalized name. Someone on the list who never filled the form gets a
stub student (no recording, so they are announced with the plain voice). Rows
that cannot be used are reported, never silently dropped. Import is additive:
it adds and updates entries and never removes any.
"""

import csv
import io
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SessionEntry, Student


class RosterImportError(Exception):
    pass


def _norm_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value.strip().lower())


COLUMN_ALIASES = {
    "r_number": {"r number", "rnumber", "r#", "rnum"},
    "email": {"email", "ttu email", "e mail", "email address"},
    "name": {"name", "full name", "student name", "student"},
    "major": {"major"},
    "honors": {"honors", "honors level", "honor", "distinction", "level"},
}


def map_columns(headers: list[str]) -> dict[str, str]:
    """Canonical column name -> the header actually used in the file."""
    columns: dict[str, str] = {}
    for header in headers:
        if header is None:
            continue
        key = _norm_header(header)
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in aliases and canonical not in columns:
                columns[canonical] = header
    return columns


def normalize_honors(value: str) -> str | None:
    text = re.sub(r"\s+", " ", (value or "").strip().lower())
    if text.startswith("with "):
        text = text[len("with "):]
    if text in ("honors", "honor"):
        return "honors"
    if text in ("highest honors", "highest honor", "highest"):
        return "highest_honors"
    return None


def normalize_r_number(value: str) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _read_rows(csv_bytes: bytes) -> tuple[list[str], list[dict]]:
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RosterImportError("The file must be a UTF-8 CSV (in Excel, save as CSV UTF-8)")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise RosterImportError("The file is empty")
    return list(reader.fieldnames), list(reader)


class _Index:
    def __init__(self, students: list[Student]):
        self.by_r: dict[str, list[Student]] = defaultdict(list)
        self.by_email: dict[str, list[Student]] = defaultdict(list)
        self.by_name: dict[str, list[Student]] = defaultdict(list)
        for student in students:
            self.add(student)

    def add(self, student: Student) -> None:
        if student.r_number:
            self.by_r[normalize_r_number(student.r_number)].append(student)
        if student.email:
            self.by_email[student.email.strip().lower()].append(student)
        if student.typed_name:
            self.by_name[normalize_name(student.typed_name)].append(student)

    def match(self, r_number: str, email: str, name: str) -> tuple[Student | None, bool]:
        """The unique student found, plus whether any criterion matched several students."""
        ambiguous = False
        for candidates in (
            self.by_r.get(normalize_r_number(r_number), []) if r_number else [],
            self.by_email.get(email.strip().lower(), []) if email else [],
            self.by_name.get(normalize_name(name), []) if name else [],
        ):
            if len(candidates) == 1:
                return candidates[0], False
            if len(candidates) > 1:
                ambiguous = True
        return None, ambiguous


async def import_roster(db: AsyncSession, session_id: str, csv_bytes: bytes) -> dict:
    headers, rows = _read_rows(csv_bytes)
    columns = map_columns(headers)
    if "honors" not in columns:
        raise RosterImportError("The file needs an honors column (values: honors or highest honors)")
    if not columns.keys() & {"r_number", "email", "name"}:
        raise RosterImportError("The file needs at least one of: R number, email, name")

    students = (await db.execute(select(Student))).scalars().all()
    index = _Index(list(students))
    entries = {
        e.student_id: e
        for e in (await db.execute(select(SessionEntry).where(SessionEntry.session_id == session_id))).scalars()
    }

    matched_ids: set[str] = set()
    created_ids: set[str] = set()
    added_ids: set[str] = set()
    updated_ids: set[str] = set()
    seen_rows: dict[str, int] = {}
    created: list[str] = []
    rejected: list[dict] = []
    duplicates: list[dict] = []
    row_count = 0

    for row_num, raw in enumerate(rows, start=2):
        def cell(key: str) -> str:
            return (raw.get(columns[key]) or "").strip() if key in columns else ""

        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        row_count += 1

        honors = normalize_honors(cell("honors"))
        if honors is None:
            rejected.append({"row": row_num, "reason": f"Unrecognized honors value '{cell('honors')}'"})
            continue

        student, ambiguous = index.match(cell("r_number"), cell("email"), cell("name"))
        if student is not None:
            matched_ids.add(student.id)
        elif ambiguous:
            rejected.append({"row": row_num, "reason": "Matches more than one existing student, fix the R number or email"})
            continue
        else:
            name = cell("name")
            if not name:
                rejected.append({"row": row_num, "reason": "No matching student and no name to create one"})
                continue
            student = Student(
                typed_name=name,
                email=cell("email") or None,
                r_number=cell("r_number") or None,
                major=cell("major") or None,
            )
            db.add(student)
            await db.flush()
            index.add(student)
            created.append(name)
            created_ids.add(student.id)

        if student.id in seen_rows:
            duplicates.append({"row": row_num, "same_as_row": seen_rows[student.id]})
        seen_rows[student.id] = row_num

        entry = entries.get(student.id)
        if entry is None:
            entry = SessionEntry(session_id=session_id, student_id=student.id)
            db.add(entry)
            entries[student.id] = entry
            added_ids.add(student.id)
        else:
            updated_ids.add(student.id)
        entry.honors_level = honors
        if cell("major"):
            entry.major = cell("major")

    await db.commit()

    return {
        "status": "ok",
        "rows": row_count,
        "matched_existing": len(matched_ids - created_ids),
        "created_stubs": created,
        "added": len(added_ids),
        "updated": len(updated_ids - added_ids),
        "duplicates": duplicates,
        "rejected": rejected,
    }
