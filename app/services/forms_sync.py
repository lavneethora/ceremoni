import base64
import os
from dataclasses import dataclass

import httpx

from app.db import async_session
from app.models import Student, Recording
from app.services.storage import storage

from app.config import settings

@dataclass(frozen=True)
class FormSource:
    """One Microsoft Form: its response workbook, its upload folder, its key.

    A ceremony that collects its own submissions gets its own Form, and each
    Form brings its own workbook and folder. Sessions name theirs in
    ceremony.yaml; anything that does not falls back to the environment.
    """

    key: str
    workbook: str
    folder: str


def form_source(options: dict | None = None) -> FormSource:
    """A session's Form, defaulting field by field to the configured one."""
    options = options or {}
    return FormSource(
        key=options.get("key") or settings.ms_form_key,
        workbook=options.get("workbook") or settings.ms_workbook_name,
        folder=options.get("folder") or settings.ms_forms_folder,
    )


def form_for_response_id(stored_id: str) -> FormSource:
    """The Form a stored response id came from, read off its key prefix.

    Reprocessing has to go back to the Form that student actually submitted
    to, which is not necessarily the session being looked at.
    """
    from app.services.config_loader import session_forms

    key = (stored_id or "").split(":", 1)[0]
    for options in session_forms():
        if options.get("key") == key:
            return form_source(options)
    return form_source()


def scoped_response_id(response_id: str, key: str) -> str:
    """A response id that cannot be confused with another Form's.

    Microsoft Forms numbers responses from 1 for every form, so a second form's
    response 1 collides with the first form's response 1. The sync treats a
    known id as already imported, so without scoping, every early submission to
    a new form is silently skipped.
    """
    return f"{key}:{response_id}" if response_id else ""

# Flexible column matching: if the header CONTAINS the key, it maps to the field
COLUMN_PATTERNS = {
    "full name": "name",
    "ttu email": "email",
    "r number": "r_number",
    "degree level": "degree_level",
    "college": "college",
    "major": "major",
    "graduation ceremony": "ceremony_time",
    "phonetic hint": "phonetic_hint",
    "voice recording": "voice_recording",
}


def _match_columns(header_row):
    """Flexibly match form headers to our fields, ignoring case/newlines."""
    col_index = {}
    for i, header in enumerate(header_row):
        if header is None:
            continue
        clean = str(header).strip().replace("\n", " ").lower()

        if clean == "id":
            col_index["form_response_id"] = i
        elif clean == "email":
            col_index["submitter_email"] = i
        else:
            for pattern, field in COLUMN_PATTERNS.items():
                if pattern in clean:
                    col_index[field] = i
                    break

    return col_index


async def _find_workbook_id(client, headers, workbook_name):
    """Find this Form's workbook on OneDrive. Exact name, then an unambiguous search."""
    resp = await client.get(
        f"https://graph.microsoft.com/v1.0/me/drive/root:/{workbook_name}",
        headers=headers,
    )
    if resp.status_code == 200:
        return resp.json()["id"]

    resp = await client.get(
        "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=200",
        headers=headers,
    )
    if resp.status_code == 200:
        matches = [
            item
            for item in resp.json().get("value", [])
            if item.get("name", "").lower().startswith("ceremoni")
            and item.get("name", "").lower().endswith(".xlsx")
        ]
        # Only guess when there is nothing to confuse it with. A second Form's
        # response workbook is also called "Ceremoni ....xlsx", so picking one
        # of several means syncing a different ceremony's responses with no
        # error anywhere to say so.
        if len(matches) == 1:
            print(f"Forms sync: Found workbook by search: {matches[0]['name']}")
            return matches[0]["id"]
        if matches:
            names = ", ".join(m.get("name", "") for m in matches)
            print(
                f"Forms sync: no workbook named '{workbook_name}', and {len(matches)} "
                f"could match ({names}). Refusing to guess. Set MS_WORKBOOK_NAME to "
                "the exact file name."
            )

    return None


async def _read_workbook_rows(client, headers, workbook_id):
    """Read the used range of Sheet1. Returns (header_row, data_rows, col_index) or raises."""
    range_url = (
        f"https://graph.microsoft.com/v1.0/me/drive/items/{workbook_id}"
        f"/workbook/worksheets('Sheet1')/usedRange"
    )
    resp = await client.get(range_url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not read workbook: {resp.status_code} - {resp.text[:200]}")

    rows = resp.json().get("values", [])
    if len(rows) < 2:
        return [], [], {}

    header_row = rows[0]
    data_rows = rows[1:]
    col_index = _match_columns(header_row)
    return header_row, data_rows, col_index


async def _find_audio_files(client, headers, folder_name):
    """Find all audio files in the Forms upload folder on OneDrive.

    Forms stores uploads at: /Apps/Microsoft Forms/{form_name}/Question/
    Each question with file uploads gets a subfolder, and inside are the uploaded files.
    Returns a dict mapping filename (lowered) to info dict.
    """
    audio_files = {}

    try:
        # Navigate: /Apps/Microsoft Forms/
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me/drive/root:/Apps/Microsoft Forms:/children",
            headers=headers,
        )
        if resp.status_code != 200:
            print(f"Forms sync: Could not list /Apps/Microsoft Forms/: {resp.status_code}")
            return audio_files

        # Find this Form's upload folder by its exact name. Matching on
        # "Ceremoni" alone was fine with one Form and is not any more: a second
        # Form's folder contains it too, and taking whichever Graph listed
        # first would feed one ceremony the other ceremony's recordings.
        folders = [i for i in resp.json().get("value", []) if i.get("folder")]
        form_folder_id = next(
            (i["id"] for i in folders if i.get("name", "") == folder_name), None
        )

        if not form_folder_id:
            near = [i for i in folders if "ceremoni" in i.get("name", "").lower()]
            if len(near) == 1:
                print(
                    f"Forms sync: no folder named '{folder_name}', "
                    f"using the only candidate: {near[0].get('name', '')}"
                )
                form_folder_id = near[0]["id"]
            elif near:
                names = ", ".join(i.get("name", "") for i in near)
                print(
                    f"Forms sync: no folder named '{folder_name}', and {len(near)} "
                    f"could match ({names}). Refusing to guess which Form's "
                    "recordings to use. Set MS_folder_name to the exact name."
                )
                return audio_files

        if not form_folder_id:
            print(f"Forms sync: no upload folder named '{folder_name}' in OneDrive")
            return audio_files

        # List children of form folder (looking for Question subfolder)
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/me/drive/items/{form_folder_id}/children",
            headers=headers,
        )
        if resp.status_code != 200:
            return audio_files

        for child in resp.json().get("value", []):
            if child.get("folder"):
                # This is likely the "Question" folder, go deeper
                resp2 = await client.get(
                    f"https://graph.microsoft.com/v1.0/me/drive/items/{child['id']}/children",
                    headers=headers,
                )
                if resp2.status_code != 200:
                    continue

                for sub in resp2.json().get("value", []):
                    if sub.get("folder"):
                        # Another level: response ID folders
                        resp3 = await client.get(
                            f"https://graph.microsoft.com/v1.0/me/drive/items/{sub['id']}/children",
                            headers=headers,
                        )
                        if resp3.status_code == 200:
                            for f in resp3.json().get("value", []):
                                name = f.get("name", "")
                                if any(name.lower().endswith(ext) for ext in [".m4a", ".wav", ".mp3", ".mp4", ".ogg", ".webm", ".flac", ".aac", ".wma", ".opus"]):
                                    download_url = f.get("@microsoft.graph.downloadUrl")
                                    item_id = f["id"]
                                    audio_files[name.lower()] = {
                                        "name": name,
                                        "download_url": download_url,
                                        "item_id": item_id,
                                        "parent_folder": sub.get("name", ""),
                                    }
                    else:
                        # Direct file in Question folder
                        name = sub.get("name", "")
                        if any(name.lower().endswith(ext) for ext in [".m4a", ".wav", ".mp3", ".mp4", ".ogg", ".webm", ".flac", ".aac", ".wma", ".opus"]):
                            download_url = sub.get("@microsoft.graph.downloadUrl")
                            audio_files[name.lower()] = {
                                "name": name,
                                "download_url": download_url,
                                "item_id": sub["id"],
                                "parent_folder": child.get("name", ""),
                            }

        print(f"Forms sync: Found {len(audio_files)} audio files in OneDrive Forms folder")
        for k, v in audio_files.items():
            print(f"  {k} (in folder: {v['parent_folder']})")

    except Exception as e:
        print(f"Forms sync: Error scanning audio files: {e}")

    return audio_files


async def _download_audio(client, headers, audio_info):
    """Download an audio file, trying the direct download URL first, then Graph API."""
    if audio_info.get("download_url"):
        try:
            resp = await client.get(audio_info["download_url"], follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 100:
                return resp.content
        except Exception as e:
            print(f"Forms sync: Direct download failed: {e}")

    try:
        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/me/drive/items/{audio_info['item_id']}/content",
            headers=headers,
            follow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.content) > 100:
            return resp.content
    except Exception as e:
        print(f"Forms sync: Graph download failed: {e}")

    return None


async def _fetch_audio_for_row(client, headers, voice_cell, response_id, audio_files, total_rows):
    """Try the three known methods to fetch the audio bytes for one row.
    Returns (audio_bytes, audio_ext) or (None, None) if all methods fail.
    """
    audio_content = None
    audio_ext = ".m4a"

    # Method 1: Graph shares API on SharePoint URL in the cell
    if voice_cell and voice_cell.startswith("http"):
        try:
            encoded = base64.urlsafe_b64encode(voice_cell.encode()).decode().rstrip("=")
            share_id = f"u!{encoded}"
            share_url = f"https://graph.microsoft.com/v1.0/shares/{share_id}/driveItem/content"

            audio_resp = await client.get(share_url, headers=headers, follow_redirects=True)
            if audio_resp.status_code == 200 and len(audio_resp.content) > 100:
                audio_content = audio_resp.content
                cell_lower = voice_cell.lower()
                for ext in [".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".webm", ".flac", ".aac", ".opus"]:
                    if ext in cell_lower:
                        audio_ext = ext
                        break
                print(f"Forms sync: Downloaded audio via Graph shares API ({len(audio_content)} bytes)")
            else:
                print(f"Forms sync: Graph shares API returned {audio_resp.status_code}, trying direct URL...")
                audio_resp = await client.get(voice_cell, headers=headers, follow_redirects=True)
                if audio_resp.status_code == 200 and len(audio_resp.content) > 100:
                    audio_content = audio_resp.content
                    print(f"Forms sync: Downloaded audio from direct URL ({len(audio_content)} bytes)")
        except Exception as e:
            print(f"Forms sync: Cell URL download failed: {e}")

    # Method 2: Match by response ID in the OneDrive folder structure
    if not audio_content and audio_files:
        for filename, info in audio_files.items():
            if info["parent_folder"] == response_id:
                print(f"Forms sync: Found audio by response ID match: {info['name']}")
                audio_content = await _download_audio(client, headers, info)
                if audio_content:
                    for ext in [".wav", ".mp3", ".m4a", ".ogg", ".webm"]:
                        if filename.endswith(ext):
                            audio_ext = ext
                            break
                break

    # Method 3: If only one audio file and one row, match them
    if not audio_content and audio_files and total_rows == 1 and len(audio_files) == 1:
        info = list(audio_files.values())[0]
        print(f"Forms sync: Only one audio file, matching to only student: {info['name']}")
        audio_content = await _download_audio(client, headers, info)
        if audio_content:
            filename = info["name"].lower()
            for ext in [".wav", ".mp3", ".m4a", ".ogg", ".webm"]:
                if filename.endswith(ext):
                    audio_ext = ext
                    break

    if audio_content:
        return audio_content, audio_ext
    return None, None


async def _save_and_process_recording(session, student, audio_content, audio_ext):
    """Save audio bytes to storage, create Recording row, run pipeline. Returns Recording or raises."""
    rec = Recording(student_id=student.id, processing_status="uploaded")
    session.add(rec)
    await session.flush()

    path = await storage.save(student.id, f"{rec.id}_original{audio_ext}", audio_content)
    rec.original_audio_url = path
    print(f"Forms sync: Saved audio for {student.typed_name} ({len(audio_content)} bytes)")

    from app.services.pipeline import process, generate_final_audio
    await process(rec.id, session)
    await generate_final_audio(rec.id, session)
    print(f"Forms sync: Processed audio for {student.typed_name}")
    return rec


def _get_row_value(row, col_index, key):
    idx = col_index.get(key)
    if idx is not None and idx < len(row) and row[idx] is not None:
        return str(row[idx]).strip()
    return ""


async def sync(access_token: str, form: dict | None = None):
    """Sync one Form's responses using the admin's delegated OAuth token.

    `form` is the selected session's form options from ceremony.yaml, so
    syncing the Medallion session cannot reach into the demo Form's data.
    """
    src = form_source(form)
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=60) as client:
        workbook_id = await _find_workbook_id(client, headers, src.workbook)
        if not workbook_id:
            return {"status": "error", "message": f"Could not find workbook '{src.workbook}' on OneDrive"}

        try:
            _, data_rows, col_index = await _read_workbook_rows(client, headers, workbook_id)
        except RuntimeError as e:
            return {"status": "error", "message": str(e)}

        if not data_rows:
            return {"status": "ok", "synced": 0, "message": "No responses yet"}

        if "name" not in col_index:
            return {"status": "error", "message": "Could not find name column in workbook headers"}

        audio_files = await _find_audio_files(client, headers, src.folder)

        synced = 0
        skipped = 0
        audio_matched = 0
        audio_failed = 0

        async with async_session() as session:
            for row in data_rows:
                response_id = _get_row_value(row, col_index, "form_response_id")
                if not response_id:
                    continue
                # Stored scoped, so a new form's response 1 is not mistaken for
                # the previous form's. Audio files are still matched on the raw
                # number, which is what their filenames carry.
                stored_id = scoped_response_id(response_id, src.key)

                from sqlalchemy import select as sa_select

                # Check if student exists
                existing = await session.execute(
                    sa_select(Student).where(Student.ms_form_response_id == stored_id)
                )
                existing_student = existing.scalar_one_or_none()

                if existing_student:
                    rec_check = await session.execute(
                        sa_select(Recording).where(Recording.student_id == existing_student.id)
                    )
                    has_recording = rec_check.scalar_one_or_none()

                    if has_recording:
                        skipped += 1
                        continue

                    student = existing_student
                    print(f"Forms sync: Re-processing audio for existing student: {student.typed_name}")
                else:
                    name = _get_row_value(row, col_index, "name")
                    if not name:
                        continue

                    college = _get_row_value(row, col_index, "college").rstrip(";").strip()

                    student = Student(
                        typed_name=name,
                        email=_get_row_value(row, col_index, "email") or _get_row_value(row, col_index, "submitter_email"),
                        r_number=_get_row_value(row, col_index, "r_number"),
                        degree_level=_get_row_value(row, col_index, "degree_level"),
                        college=college,
                        major=_get_row_value(row, col_index, "major"),
                        phonetic_hint=_get_row_value(row, col_index, "phonetic_hint") or None,
                        ms_form_response_id=stored_id,
                    )
                    session.add(student)
                    await session.flush()
                    synced += 1

                voice_cell = _get_row_value(row, col_index, "voice_recording")
                print(f"Forms sync: Voice cell for {student.typed_name}: '{voice_cell[:100] if voice_cell else '(empty)'}'")

                audio_content, audio_ext = await _fetch_audio_for_row(
                    client, headers, voice_cell, response_id, audio_files, len(data_rows)
                )

                if audio_content:
                    try:
                        await _save_and_process_recording(session, student, audio_content, audio_ext)
                        audio_matched += 1
                    except Exception as e:
                        print(f"Forms sync: Audio processing failed for {student.typed_name}: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    if voice_cell:
                        audio_failed += 1
                        print(f"Forms sync: Could not download audio for {student.typed_name}")

            await session.commit()

        return {
            "status": "ok",
            "synced": synced,
            "skipped": skipped,
            "audio_matched": audio_matched,
            "audio_failed": audio_failed,
            "total_rows": len(data_rows),
            "audio_files_found": len(audio_files),
        }


async def _delete_recordings_for_student(session, student):
    """Delete DB rows and best-effort delete local storage files for a student's recordings."""
    from sqlalchemy import select as sa_select

    recs = await session.execute(
        sa_select(Recording).where(Recording.student_id == student.id)
    )
    for rec in recs.scalars().all():
        for path in [rec.original_audio_url, rec.cleaned_audio_url, rec.generated_audio_url]:
            if not path:
                continue
            try:
                # Has to go through the storage backend. This used to call
                # os.remove, which silently did nothing once audio moved to
                # Supabase, because "supabase://..." is not a filesystem path.
                # Every re-sync then left its audio behind and wrote the next
                # copy under a fresh recording id, which is how one student
                # ended up with ten copies of the same recording.
                await storage.delete_from_path(path)
            except Exception as e:
                print(f"Forms sync: Could not remove file {path}: {e}")
        await session.delete(rec)
    await session.flush()


async def reprocess_single_student(access_token: str, student_id: str):
    """Delete a student's existing recording and re-run the full download + pipeline."""
    headers = {"Authorization": f"Bearer {access_token}"}

    # Which Form to go back to is decided by the student, not by whichever
    # session happens to be on screen: their stored response id carries the
    # key of the Form they actually submitted to.
    async with async_session() as lookup:
        student = await lookup.get(Student, student_id)
        if not student:
            return {"status": "error", "message": "Student not found"}
        if not student.ms_form_response_id:
            return {"status": "error", "message": f"Student {student.typed_name} has no form response ID"}
        src = form_for_response_id(student.ms_form_response_id)

    async with httpx.AsyncClient(timeout=60) as client:
        workbook_id = await _find_workbook_id(client, headers, src.workbook)
        if not workbook_id:
            return {"status": "error", "message": f"Could not find workbook '{src.workbook}' on OneDrive"}

        try:
            _, data_rows, col_index = await _read_workbook_rows(client, headers, workbook_id)
        except RuntimeError as e:
            return {"status": "error", "message": str(e)}

        if not data_rows:
            return {"status": "error", "message": "Workbook has no data rows"}

        if "name" not in col_index:
            return {"status": "error", "message": "Could not find name column in workbook headers"}

        async with async_session() as session:
            student = await session.get(Student, student_id)
            if not student:
                return {"status": "error", "message": "Student not found"}

            if not student.ms_form_response_id:
                return {"status": "error", "message": f"Student {student.typed_name} has no form response ID"}

            # Find matching row in workbook
            target_row = None
            raw_response_id = None
            for row in data_rows:
                row_id = _get_row_value(row, col_index, "form_response_id")
                if scoped_response_id(row_id, src.key) == student.ms_form_response_id:
                    target_row = row
                    raw_response_id = row_id
                    break

            if target_row is None:
                return {"status": "error", "message": f"No workbook row matches student's response ID ({student.ms_form_response_id})"}

            # Delete existing recordings so the sync path treats this as fresh
            await _delete_recordings_for_student(session, student)

            audio_files = await _find_audio_files(client, headers, src.folder)
            voice_cell = _get_row_value(target_row, col_index, "voice_recording")
            print(f"Forms sync (reprocess): Voice cell for {student.typed_name}: '{voice_cell[:100] if voice_cell else '(empty)'}'")

            audio_content, audio_ext = await _fetch_audio_for_row(
                client, headers, voice_cell, raw_response_id, audio_files, len(data_rows)
            )

            if not audio_content:
                await session.commit()
                return {
                    "status": "error",
                    "message": f"Could not download audio for {student.typed_name}",
                }

            try:
                rec = await _save_and_process_recording(session, student, audio_content, audio_ext)
                await session.commit()
                return {
                    "status": "ok",
                    "student_id": student.id,
                    "recording_id": rec.id,
                    "processing_status": rec.processing_status,
                }
            except Exception as e:
                await session.rollback()
                import traceback
                traceback.print_exc()
                return {"status": "error", "message": f"Audio processing failed: {e}"}
