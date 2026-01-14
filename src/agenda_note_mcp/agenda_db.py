"""
Agenda Database Reader

Reads Agenda notes directly from the SQLite database files.
Agenda stores data in .agenda-document directories containing Changes.db SQLite files.
Data is stored as CRDT changes with binary plist blobs.
"""

import sqlite3
import plistlib
import os
from pathlib import Path
from typing import Any, Iterator
from dataclasses import dataclass
from datetime import datetime
import re


# Note status constants
NOTE_STATUS_NORMAL = 0
NOTE_STATUS_COMPLETED = 1
NOTE_STATUS_ON_THE_AGENDA = 2

# Default Agenda data location (macOS standard path)
# Can be overridden via AGENDA_STORES_PATH environment variable
DEFAULT_AGENDA_STORES_PATH = Path.home() / "Library/Group Containers/WRBK2Z2EG7.group.com.momenta.agenda.macos/Release/Application/Stores"


def get_agenda_stores_path() -> Path:
    """Get the Agenda stores path from environment or use default."""
    custom_path = os.environ.get("AGENDA_STORES_PATH")
    if custom_path:
        return Path(custom_path)
    return DEFAULT_AGENDA_STORES_PATH


@dataclass
class AgendaNote:
    """Represents an Agenda note."""
    id: str
    title: str
    content: str
    project_id: str | None = None
    project_title: str | None = None
    store_id: str | None = None


@dataclass
class AgendaProject:
    """Represents an Agenda project."""
    id: str
    title: str
    store_id: str | None = None


@dataclass
class AgendaTag:
    """Represents a tag found in Agenda notes."""
    name: str
    count: int
    note_ids: list[str] | None = None


@dataclass
class AgendaNoteDated:
    """Represents an Agenda note with date information."""
    id: str
    title: str
    content: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    project_id: str | None = None
    project_title: str | None = None
    store_id: str | None = None


class AgendaDB:
    """Reads Agenda data from SQLite databases."""

    def __init__(self, stores_path: Path | None = None):
        self.stores_path = stores_path or get_agenda_stores_path()

    def _get_db_paths(self) -> Iterator[tuple[str, Path]]:
        """Get all Changes.db paths from agenda-document directories."""
        if not self.stores_path.exists():
            return

        for doc_dir in self.stores_path.glob("*.agenda-document"):
            db_path = doc_dir / "Changes.db"
            if db_path.exists():
                store_id = doc_dir.name.replace(".agenda-document", "")
                yield store_id, db_path

    def _parse_blob(self, blob: bytes) -> Any:
        """Parse a binary plist blob."""
        try:
            return plistlib.loads(blob)
        except Exception:
            return None

    def _get_latest_value(self, cursor: sqlite3.Cursor, key_pattern: str) -> Any:
        """Get the latest value for a key pattern (handles CRDT updates)."""
        cursor.execute(
            "SELECT blob FROM Change WHERE key = ? ORDER BY timestamp DESC LIMIT 1",
            (key_pattern,)
        )
        row = cursor.fetchone()
        if row:
            return self._parse_blob(row[0])
        return None

    def _extract_text_from_content(self, content: Any) -> str:
        """Extract plain text from Agenda content structure."""
        if not content:
            return ""

        # Handle list of content items
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and "string" in item:
                    parts.append(item["string"])
            return "".join(parts)

        # Handle string that might be JSON
        if isinstance(content, str):
            # Try to parse as JSON if it looks like JSON
            if content.startswith("["):
                try:
                    import json
                    parsed = json.loads(content)
                    return self._extract_text_from_content(parsed)
                except json.JSONDecodeError:
                    pass
            return content

        return str(content)

    def search_notes(self, query: str, limit: int = 50) -> list[AgendaNote]:
        """Search for notes containing the query string."""
        results = []
        query_lower = query.lower()

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Get all section (note) IDs and their titles
                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Section:%:title'")
                section_keys = cursor.fetchall()

                for (key,) in section_keys:
                    section_id = key.split(":")[1]
                    title = self._get_latest_value(cursor, f"Section:{section_id}:title")

                    if not title:
                        continue

                    title_str = str(title)

                    # Check if query matches title
                    title_match = query_lower in title_str.lower()

                    # Get note content (paragraphs)
                    cursor.execute(
                        "SELECT key, blob FROM Change WHERE key LIKE ? ORDER BY timestamp DESC",
                        (f"Paragraph:%:sectionIdentifier",)
                    )

                    note_content = []
                    content_match = False

                    # Find paragraphs belonging to this section
                    para_cursor = conn.cursor()
                    para_cursor.execute(
                        "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:sectionIdentifier'"
                    )

                    for (para_id,) in para_cursor.fetchall():
                        section_ref = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
                        if section_ref == section_id:
                            content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")
                            if content:
                                text = self._extract_text_from_content(content)
                                note_content.append(text)
                                if query_lower in text.lower():
                                    content_match = True

                    if title_match or content_match:
                        # Join content parts and clean up
                        full_content = "".join(note_content).strip()
                        results.append(AgendaNote(
                            id=section_id,
                            title=title_str,
                            content=full_content[:500] if full_content else "",  # Truncate for preview
                            store_id=store_id
                        ))

                        if len(results) >= limit:
                            conn.close()
                            return results

                conn.close()

            except Exception as e:
                print(f"Error searching {db_path}: {e}")
                continue

        return results

    def list_projects(self) -> list[AgendaProject]:
        """List all projects (Document level in Agenda)."""
        projects = []

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Get Document title (project name)
                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Document:%:title'")
                doc_keys = cursor.fetchall()

                for (key,) in doc_keys:
                    doc_id = key.split(":")[1]
                    title = self._get_latest_value(cursor, f"Document:{doc_id}:title")

                    if title:
                        projects.append(AgendaProject(
                            id=doc_id,
                            title=str(title),
                            store_id=store_id
                        ))

                conn.close()

            except Exception as e:
                print(f"Error listing projects from {db_path}: {e}")
                continue

        return projects

    def read_note(self, note_id: str) -> AgendaNote | None:
        """Read a specific note by ID."""
        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Check if this section exists in this database
                title = self._get_latest_value(cursor, f"Section:{note_id}:title")

                if title:
                    # Get all paragraphs for this note
                    content_parts = []

                    cursor.execute(
                        "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:sectionIdentifier'"
                    )

                    for (para_id,) in cursor.fetchall():
                        section_ref = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
                        if section_ref == note_id:
                            # Get paragraph priority for ordering
                            priority = self._get_latest_value(cursor, f"Paragraph:{para_id}:priority")
                            content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")
                            if content:
                                text = self._extract_text_from_content(content)
                                content_parts.append((priority or 0, text))

                    # Sort by priority and join
                    content_parts.sort(key=lambda x: x[0] if isinstance(x[0], (int, float)) else 0)
                    full_content = "".join(text for _, text in content_parts)

                    conn.close()
                    return AgendaNote(
                        id=note_id,
                        title=str(title),
                        content=full_content,
                        store_id=store_id
                    )

                conn.close()

            except Exception as e:
                print(f"Error reading note from {db_path}: {e}")
                continue

        return None

    def list_notes(self, project_id: str | None = None, limit: int = 100) -> list[AgendaNote]:
        """List all notes, optionally filtered by project."""
        notes = []

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Get all section (note) titles
                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Section:%:title'")
                section_keys = cursor.fetchall()

                for (key,) in section_keys:
                    section_id = key.split(":")[1]
                    title = self._get_latest_value(cursor, f"Section:{section_id}:title")

                    if title and str(title) != "Untitled Note":
                        # Check if deleted
                        deleted = self._get_latest_value(cursor, f"Section:{section_id}:markedDeleted")
                        if deleted:
                            continue

                        notes.append(AgendaNote(
                            id=section_id,
                            title=str(title),
                            content="",  # Don't load content for listing
                            store_id=store_id
                        ))

                        if len(notes) >= limit:
                            conn.close()
                            return notes

                conn.close()

            except Exception as e:
                print(f"Error listing notes from {db_path}: {e}")
                continue

        return notes

    def _get_note_content_preview(self, cursor: sqlite3.Cursor, section_id: str, max_length: int = 300) -> str:
        """Get a preview of note content."""
        content_parts = []

        cursor.execute(
            "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:sectionIdentifier'"
        )

        for (para_id,) in cursor.fetchall():
            section_ref = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
            if section_ref == section_id:
                priority = self._get_latest_value(cursor, f"Paragraph:{para_id}:priority")
                content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")
                if content:
                    text = self._extract_text_from_content(content)
                    if text:
                        content_parts.append((priority or 0, text))

        content_parts.sort(key=lambda x: x[0] if isinstance(x[0], (int, float)) else 0)
        full_content = " ".join(text.strip() for _, text in content_parts if text.strip())

        if len(full_content) > max_length:
            full_content = full_content[:max_length] + "..."

        return full_content

    def _get_section_full_text(self, cursor: sqlite3.Cursor, section_id: str) -> str:
        """Get full text content of a section."""
        content_parts = []

        cursor.execute(
            "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:sectionIdentifier'"
        )

        for (para_id,) in cursor.fetchall():
            section_ref = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
            if section_ref == section_id:
                content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")
                if content:
                    text = self._extract_text_from_content(content)
                    if text:
                        content_parts.append(text)

        return " ".join(content_parts)

    def get_notes_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 100
    ) -> list[AgendaNoteDated]:
        """Get notes that fall within a date range."""
        results = []

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Get project info for this store
                project_title = None
                project_id = None
                cursor.execute("SELECT key, blob FROM Change WHERE key LIKE 'Document:%:title' ORDER BY timestamp DESC LIMIT 1")
                doc_row = cursor.fetchone()
                if doc_row and doc_row[1]:
                    project_title = self._parse_blob(doc_row[1])
                    project_id = doc_row[0].split(':')[1]

                # Find sections with startDate
                cursor.execute(
                    "SELECT DISTINCT substr(key, 9, 36) as section_id FROM Change WHERE key LIKE 'Section:%:startDate'"
                )

                for (section_id,) in cursor.fetchall():
                    note_start_date = self._get_latest_value(cursor, f"Section:{section_id}:startDate")

                    if not isinstance(note_start_date, datetime):
                        continue

                    # Check date range
                    note_date = note_start_date.replace(tzinfo=None)
                    if not (start_date <= note_date <= end_date):
                        continue

                    # Check if deleted
                    is_deleted = self._get_latest_value(cursor, f"Section:{section_id}:markedDeleted")
                    if is_deleted:
                        continue

                    title = self._get_latest_value(cursor, f"Section:{section_id}:title")
                    if not title or str(title) == "Untitled Note":
                        continue

                    note_end_date = self._get_latest_value(cursor, f"Section:{section_id}:endDate")
                    if note_end_date and not isinstance(note_end_date, datetime):
                        note_end_date = None

                    content_preview = self._get_note_content_preview(cursor, section_id, max_length=200)

                    results.append(AgendaNoteDated(
                        id=section_id,
                        title=str(title),
                        content=content_preview,
                        start_date=note_start_date,
                        end_date=note_end_date,
                        project_id=project_id,
                        project_title=str(project_title) if project_title else None,
                        store_id=store_id
                    ))

                    if len(results) >= limit:
                        conn.close()
                        results.sort(key=lambda x: x.start_date or datetime.min)
                        return results

                conn.close()

            except Exception as e:
                print(f"Error getting notes by date from {db_path}: {e}")
                continue

        results.sort(key=lambda x: x.start_date or datetime.min)
        return results

    def list_on_the_agenda(self, limit: int = 50, include_content: bool = True) -> list[AgendaNote]:
        """List all notes marked as 'On the Agenda'."""
        notes = []

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                # Get project title for this store
                project_title = None
                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Document:%:title'")
                doc_keys = cursor.fetchall()
                for (doc_key,) in doc_keys:
                    doc_id = doc_key.split(":")[1]
                    project_title = self._get_latest_value(cursor, f"Document:{doc_id}:title")
                    if project_title:
                        project_title = str(project_title)
                        break

                # Get sections with status
                cursor.execute(
                    "SELECT DISTINCT substr(key, 9, 36) as section_id FROM Change WHERE key LIKE 'Section:%:status'"
                )

                for (section_id,) in cursor.fetchall():
                    status = self._get_latest_value(cursor, f"Section:{section_id}:status")
                    if status != NOTE_STATUS_ON_THE_AGENDA:
                        continue

                    deleted = self._get_latest_value(cursor, f"Section:{section_id}:markedDeleted")
                    if deleted:
                        continue

                    title = self._get_latest_value(cursor, f"Section:{section_id}:title")
                    if not title or str(title) == "Untitled Note":
                        continue

                    content = ""
                    if include_content:
                        content = self._get_note_content_preview(cursor, section_id, max_length=300)

                    notes.append(AgendaNote(
                        id=section_id,
                        title=str(title),
                        content=content,
                        project_title=project_title,
                        store_id=store_id
                    ))

                    if len(notes) >= limit:
                        conn.close()
                        return notes

                conn.close()

            except Exception as e:
                print(f"Error listing on-the-agenda notes from {db_path}: {e}")
                continue

        return notes

    def get_on_the_agenda_status(self, note_id: str) -> bool | None:
        """Get the 'On the Agenda' status for a note."""
        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                title = self._get_latest_value(cursor, f"Section:{note_id}:title")
                if title:
                    status = self._get_latest_value(cursor, f"Section:{note_id}:status")
                    conn.close()
                    return status == NOTE_STATUS_ON_THE_AGENDA

                conn.close()

            except Exception:
                continue

        return None

    def list_tags(self, include_note_ids: bool = False, limit: int = 100) -> list[AgendaTag]:
        """List all tags (#hashtags) used across all notes."""
        hashtag_pattern = re.compile(r'#(\w+)', re.UNICODE)
        tags_data: dict[str, dict] = {}

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:content'"
                )
                para_ids = [row[0] for row in cursor.fetchall()]

                for para_id in para_ids:
                    section_id = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
                    content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")

                    if not content:
                        continue

                    text = self._extract_text_from_content(content)
                    tags = hashtag_pattern.findall(text)

                    for tag in tags:
                        tag_lower = tag.lower()
                        if tag_lower not in tags_data:
                            tags_data[tag_lower] = {
                                'name': tag,
                                'count': 0,
                                'note_ids': set()
                            }
                        tags_data[tag_lower]['count'] += 1
                        if section_id:
                            tags_data[tag_lower]['note_ids'].add(section_id)

                conn.close()

            except Exception as e:
                print(f"Error listing tags from {db_path}: {e}")
                continue

        result = []
        for tag_info in tags_data.values():
            result.append(AgendaTag(
                name=tag_info['name'],
                count=tag_info['count'],
                note_ids=list(tag_info['note_ids']) if include_note_ids else None
            ))

        result.sort(key=lambda x: -x.count)
        return result[:limit]

    def search_by_tags(
        self,
        tags: list[str],
        match_mode: str = "or",
        limit: int = 50
    ) -> list[AgendaNote]:
        """Search notes containing specified tags."""
        results = []
        tags_lower = [t.lower().lstrip('#') for t in tags]
        hashtag_pattern = re.compile(r'#(\w+)', re.UNICODE)

        for store_id, db_path in self._get_db_paths():
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Section:%:title'")

                for (key,) in cursor.fetchall():
                    section_id = key.split(":")[1]

                    deleted = self._get_latest_value(cursor, f"Section:{section_id}:markedDeleted")
                    if deleted:
                        continue

                    full_text = self._get_section_full_text(cursor, section_id)
                    found_tags = set(t.lower() for t in hashtag_pattern.findall(full_text))

                    if match_mode == "and":
                        matched = all(t in found_tags for t in tags_lower)
                    else:
                        matched = any(t in found_tags for t in tags_lower)

                    if matched:
                        title = self._get_latest_value(cursor, f"Section:{section_id}:title")
                        if title and str(title) != "Untitled Note":
                            results.append(AgendaNote(
                                id=section_id,
                                title=str(title),
                                content=full_text[:300] if full_text else "",
                                store_id=store_id
                            ))

                            if len(results) >= limit:
                                conn.close()
                                return results

                conn.close()

            except Exception as e:
                print(f"Error searching by tags from {db_path}: {e}")
                continue

        return results

    def search_notes_in_project(
        self,
        query: str,
        project_id: str | None = None,
        project_title: str | None = None,
        limit: int = 50
    ) -> list[AgendaNote]:
        """Search for notes within a specific project."""
        if not project_id and not project_title:
            raise ValueError("Either project_id or project_title must be provided")

        results = []
        query_lower = query.lower()
        target_store_id = None

        # Find the target store by project title if needed
        if not project_id and project_title:
            for store_id, db_path in self._get_db_paths():
                try:
                    conn = sqlite3.connect(str(db_path))
                    cursor = conn.cursor()

                    cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Document:%:title'")
                    doc_keys = cursor.fetchall()

                    for (key,) in doc_keys:
                        doc_id = key.split(":")[1]
                        title = self._get_latest_value(cursor, f"Document:{doc_id}:title")

                        if title and str(title).lower() == project_title.lower():
                            project_id = doc_id
                            target_store_id = store_id
                            break

                    conn.close()

                    if project_id:
                        break

                except Exception as e:
                    print(f"Error finding project from {db_path}: {e}")
                    continue

            if not project_id:
                return []

        # Search within the target store
        for store_id, db_path in self._get_db_paths():
            if target_store_id and store_id != target_store_id:
                continue

            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()

                cursor.execute("SELECT DISTINCT key FROM Change WHERE key LIKE 'Section:%:title'")
                section_keys = cursor.fetchall()

                for (key,) in section_keys:
                    section_id = key.split(":")[1]

                    deleted = self._get_latest_value(cursor, f"Section:{section_id}:markedDeleted")
                    if deleted:
                        continue

                    title = self._get_latest_value(cursor, f"Section:{section_id}:title")
                    if not title:
                        continue

                    title_str = str(title)
                    title_match = query_lower in title_str.lower()

                    note_content = []
                    content_match = False

                    para_cursor = conn.cursor()
                    para_cursor.execute(
                        "SELECT DISTINCT substr(key, 11, 36) as para_id FROM Change WHERE key LIKE 'Paragraph:%:sectionIdentifier'"
                    )

                    for (para_id,) in para_cursor.fetchall():
                        section_ref = self._get_latest_value(cursor, f"Paragraph:{para_id}:sectionIdentifier")
                        if section_ref == section_id:
                            content = self._get_latest_value(cursor, f"Paragraph:{para_id}:content")
                            if content:
                                text = self._extract_text_from_content(content)
                                note_content.append(text)
                                if query_lower in text.lower():
                                    content_match = True

                    if title_match or content_match:
                        full_content = "".join(note_content).strip()
                        results.append(AgendaNote(
                            id=section_id,
                            title=title_str,
                            content=full_content[:300] if full_content else "",
                            project_id=project_id,
                            store_id=store_id
                        ))

                        if len(results) >= limit:
                            conn.close()
                            return results

                conn.close()

            except Exception as e:
                print(f"Error searching in project from {db_path}: {e}")
                continue

        return results
