import asyncio
import subprocess
import json
import os
import time
from urllib.parse import quote
from typing import Any
from pathlib import Path

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
from pydantic import AnyUrl
import mcp.server.stdio

from .agenda_db import AgendaDB

# Store notes as a simple key-value dict to demonstrate state management
notes: dict[str, str] = {}

server = Server("mcp-server-agenda")

# Agenda database reader for direct SQLite access
agenda_db = AgendaDB()

# Callback directory where the helper app writes results
# Can be overridden via AGENDA_CALLBACK_DIR environment variable
DEFAULT_CALLBACK_DIR = Path.home() / ".agenda-mcp-callbacks"


def get_callback_dir() -> Path:
    """Get the callback directory from environment or use default."""
    custom_path = os.environ.get("AGENDA_CALLBACK_DIR")
    if custom_path:
        return Path(custom_path)
    return DEFAULT_CALLBACK_DIR


CALLBACK_DIR = get_callback_dir()


class CallbackWatcher:
    """
    Watches for callback files from the AgendaMCPCallback helper app.
    The helper app receives x-callback-url responses and writes them to files.
    """

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self.callback_dir = CALLBACK_DIR

    def _clear_old_callbacks(self):
        """Clear old callback files before waiting for a new one."""
        if self.callback_dir.exists():
            for f in self.callback_dir.glob("callback-*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
            # Also clear latest.json
            latest = self.callback_dir / "latest.json"
            if latest.exists():
                try:
                    latest.unlink()
                except Exception:
                    pass

    async def wait_for_callback(self) -> dict[str, Any]:
        """Wait for a callback file to appear."""
        self._clear_old_callbacks()

        # Ensure callback directory exists
        self.callback_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        latest_file = self.callback_dir / "latest.json"

        while time.time() - start_time < self.timeout:
            if latest_file.exists():
                try:
                    with open(latest_file, 'r') as f:
                        data = json.load(f)

                    # Return the params from the callback
                    return data.get("params", {})
                except json.JSONDecodeError:
                    pass  # File not fully written yet
                except Exception:
                    pass

            await asyncio.sleep(0.1)  # Poll every 100ms

        raise TimeoutError(f"Callback not received within {self.timeout} seconds")


class XCallbackURLHandler:
    """Handles x-callback-url execution on macOS systems."""

    # Custom URL scheme for callbacks - handled by AgendaMCPCallback.app
    CALLBACK_SCHEME = "agenda-mcp://callback"

    @staticmethod
    def call_url(url: str) -> str:
        """
        Executes an x-callback-url on macOS using the 'open' command.
        """
        try:
            result = subprocess.run(
                ['open', url],
                check=True,
                capture_output=True,
                text=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to execute x-callback-url: {e}")

    @staticmethod
    async def call_url_with_callback(base_url: str, params: list[str], timeout: float = 10.0) -> dict[str, Any]:
        """
        Execute an x-callback-url and wait for the result via file callback.

        Uses the AgendaMCPCallback helper app which handles the agenda-mcp:// URL scheme
        and writes callback data to ~/.agenda-mcp-callbacks/latest.json
        """
        watcher = CallbackWatcher(timeout=timeout)

        # Add callback URLs using our custom URL scheme
        callback_url = XCallbackURLHandler.CALLBACK_SCHEME
        params.append(f"x-success={quote(callback_url)}")
        params.append(f"x-error={quote(callback_url)}")

        # Build the full URL
        if params:
            full_url = f"{base_url}?{'&'.join(params)}"
        else:
            full_url = base_url

        # Execute the URL
        XCallbackURLHandler.call_url(full_url)

        # Wait for the callback file
        result = await watcher.wait_for_callback()

        return result


@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """
    List available note resources.
    Each note is exposed as a resource with a custom note:// URI scheme.
    """
    return [
        types.Resource(
            uri=AnyUrl(f"note://internal/{name}"),
            name=f"Note: {name}",
            description=f"A simple note named {name}",
            mimeType="text/plain",
        )
        for name in notes
    ]


@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    """
    Read a specific note's content by its URI.
    The note name is extracted from the URI host component.
    """
    if uri.scheme != "note":
        raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

    name = uri.path
    if name is not None:
        name = name.lstrip("/")
        return notes[name]
    raise ValueError(f"Note not found: {name}")


@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """
    List available prompts.
    Each prompt can have optional arguments to customize its behavior.
    """
    return [
        types.Prompt(
            name="summarize-notes",
            description="Creates a summary of all notes",
            arguments=[
                types.PromptArgument(
                    name="style",
                    description="Style of the summary (brief/detailed)",
                    required=False,
                )
            ],
        )
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    """
    Generate a prompt by combining arguments with server state.
    The prompt includes all current notes and can be customized via arguments.
    """
    if name != "summarize-notes":
        raise ValueError(f"Unknown prompt: {name}")

    style = (arguments or {}).get("style", "brief")
    detail_prompt = " Give extensive details." if style == "detailed" else ""

    return types.GetPromptResult(
        description="Summarize the current notes",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=f"Here are the current notes to summarize:{detail_prompt}\n\n"
                    + "\n".join(
                        f"- {name}: {content}"
                        for name, content in notes.items()
                    ),
                ),
            )
        ],
    )


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    """
    return [
        types.Tool(
            name="add-note",
            description="Add a new note",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
        ),
        # === View Opening Tools ===
        types.Tool(
            name="on-the-agenda",
            description="Open the 'On the Agenda' overview in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="today",
            description="Open the Today overview in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="open-overview",
            description="Open a saved overview in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the saved overview"},
                    "identifier": {"type": "string", "description": "Identifier of the saved overview"},
                },
            },
        ),
        types.Tool(
            name="open-search",
            description="Open search in Agenda with optional query",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                },
            },
        ),
        types.Tool(
            name="open-project",
            description="Open a project in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the project"},
                    "identifier": {"type": "string", "description": "Identifier of the project"},
                    "separate_window": {"type": "boolean", "description": "Open in separate window"},
                },
            },
        ),
        types.Tool(
            name="open-agenda-note",
            description="Open a note in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "identifier": {"type": "string"},
                    "project_title": {"type": "string"},
                    "separate_window": {"type": "boolean"}
                },
            },
        ),
        # === Data Query Tools (with callback support) ===
        types.Tool(
            name="get-identifier",
            description="Get the identifier of a note or project in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note or project"},
                    "project_title": {"type": "string", "description": "Project title (for notes)"},
                },
            },
        ),
        types.Tool(
            name="get-selected-project",
            description="Get the currently selected project in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="get-selected-note",
            description="Get the currently selected note in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="get-selection",
            description="Get the currently selected item (note or project) in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # === Creation Tools ===
        types.Tool(
            name="create-category",
            description="Create a new category in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the category"},
                },
                "required": ["title"],
            },
        ),
        types.Tool(
            name="create-agenda-project",
            description="Create a new project in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category_title": {"type": "string"},
                    "identifier": {"type": "string"},
                    "select": {"type": "boolean"},
                    "sort_order": {
                        "type": "string",
                        "enum": ["newest-first", "oldest-first"]
                    }
                },
                "required": ["title"]
            },
        ),
        types.Tool(
            name="create-agenda-note",
            description="Create a note in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "project_title": {"type": "string"},
                    "identifier": {"type": "string", "description": "Project identifier (alternative to project_title)"},
                    "on_the_agenda": {"type": "boolean"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                    "template_name": {"type": "string"},
                    "template_input": {"type": "string"},
                    "collapsed": {"type": "boolean"},
                    "completed": {"type": "boolean"},
                    "pinned": {"type": "boolean"},
                    "footnote": {"type": "boolean"},
                    "select": {"type": "boolean"},
                    "attachment": {"type": "string", "description": "Base64 encoded attachment data"},
                    "filename": {"type": "string", "description": "Filename for the attachment"},
                    "event_title": {"type": "string", "description": "Calendar event title to link"},
                    "display_style": {
                        "type": "string",
                        "enum": ["full-width", "thumbnail", "inline"],
                        "description": "Display style for attachments"
                    },
                    "display_size": {
                        "type": "integer",
                        "enum": [25, 50, 75, 100],
                        "description": "Display size percentage for attachments"
                    },
                },
                "required": ["title", "text"],
            },
        ),
        # === Modification Tools ===
        types.Tool(
            name="append-to-note",
            description="Append text or attachment to an existing note in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note"},
                    "identifier": {"type": "string", "description": "Identifier of the note"},
                    "project_title": {"type": "string", "description": "Project containing the note"},
                    "text": {"type": "string", "description": "Text to append"},
                    "attachment": {"type": "string", "description": "Base64 encoded attachment data"},
                    "filename": {"type": "string", "description": "Filename for the attachment"},
                    "display_style": {
                        "type": "string",
                        "enum": ["full-width", "thumbnail", "inline"],
                        "description": "Display style for attachments"
                    },
                    "display_size": {
                        "type": "integer",
                        "enum": [25, 50, 75, 100],
                        "description": "Display size percentage"
                    },
                },
            },
        ),
        types.Tool(
            name="replace-note",
            description="Replace the content of an existing note in Agenda",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note"},
                    "identifier": {"type": "string", "description": "Identifier of the note"},
                    "project_title": {"type": "string", "description": "Project containing the note"},
                    "text": {"type": "string", "description": "New text content for the note"},
                },
                "required": ["text"],
            },
        ),
        # === SQLite-based Read Tools (direct database access) ===
        types.Tool(
            name="search-notes",
            description="Search notes in Agenda by keyword (searches titles and content)",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "limit": {"type": "integer", "description": "Maximum number of results (default 20)"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="read-note",
            description="Read the full content of a note from Agenda by ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "identifier": {"type": "string", "description": "Note identifier (UUID)"},
                },
                "required": ["identifier"],
            },
        ),
        types.Tool(
            name="list-projects",
            description="List all projects in Agenda",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="list-notes",
            description="List all notes in Agenda (titles only)",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of notes (default 100)"},
                },
            },
        ),
        # === New SQLite-based Tools ===
        types.Tool(
            name="get-notes-by-date-range",
            description="Get notes within a date range. Returns notes with their assigned dates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format (inclusive)"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format (inclusive)"},
                    "limit": {"type": "integer", "description": "Maximum number of results (default 100)"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        types.Tool(
            name="list-on-the-agenda",
            description="List all notes marked as 'On the Agenda' in Agenda app",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Maximum number of notes (default 50)"},
                    "include_content": {"type": "boolean", "description": "Include content preview (default true)"},
                },
            },
        ),
        types.Tool(
            name="set-on-the-agenda",
            description="Set the 'On the Agenda' status for a specific note",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the note"},
                    "identifier": {"type": "string", "description": "Identifier (UUID) of the note"},
                    "project_title": {"type": "string", "description": "Project containing the note"},
                    "on_the_agenda": {"type": "boolean", "description": "Set to true to add, false to remove"},
                },
                "required": ["on_the_agenda"],
            },
        ),
        types.Tool(
            name="list-tags",
            description="List all tags (#hashtags) used across all notes with usage counts",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_note_ids": {"type": "boolean", "description": "Include list of note IDs for each tag (default false)"},
                    "limit": {"type": "integer", "description": "Maximum number of tags (default 100)"},
                },
            },
        ),
        types.Tool(
            name="search-by-tags",
            description="Search notes containing specific tags",
            inputSchema={
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tags to search for (with or without # prefix)"
                    },
                    "match_mode": {
                        "type": "string",
                        "enum": ["and", "or"],
                        "description": "Match mode: 'and' requires all tags, 'or' requires any tag (default: or)"
                    },
                    "limit": {"type": "integer", "description": "Maximum number of results (default 50)"},
                },
                "required": ["tags"],
            },
        ),
        types.Tool(
            name="search-in-project",
            description="Search notes within a specific project",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "project_id": {"type": "string", "description": "Project identifier (UUID)"},
                    "project_title": {"type": "string", "description": "Project title (alternative to project_id)"},
                    "limit": {"type": "integer", "description": "Maximum number of results (default 50)"},
                },
                "required": ["query"],
            },
        ),
    ]


def build_url(base_command: str, params: list[str]) -> str:
    """Build an x-callback URL for Agenda."""
    base_url = f"agenda://x-callback-url/{base_command}"
    if params:
        return f"{base_url}?{'&'.join(params)}"
    return base_url


def add_param(params: list[str], key: str, value: str | None, url_key: str | None = None):
    """Add a parameter to the params list if value is not None."""
    if value is not None:
        params.append(f"{url_key or key}={quote(str(value))}")


def add_bool_param(params: list[str], arguments: dict, key: str, url_key: str | None = None):
    """Add a boolean parameter to the params list if present."""
    if key in arguments:
        params.append(f"{url_key or key}={str(arguments[key]).lower()}")


def add_string_param(params: list[str], arguments: dict, key: str, url_key: str | None = None):
    """Add a string parameter to the params list if present."""
    if key in arguments and arguments[key]:
        params.append(f"{url_key or key}={quote(arguments[key])}")


def add_int_param(params: list[str], arguments: dict, key: str, url_key: str | None = None):
    """Add an integer parameter to the params list if present."""
    if key in arguments:
        params.append(f"{url_key or key}={arguments[key]}")


def format_callback_result(result: dict[str, Any]) -> str:
    """Format the callback result for display."""
    # Check for error
    if "errorCode" in result or "errorMessage" in result:
        error_code = result.get("errorCode", "unknown")
        error_msg = result.get("errorMessage", "Unknown error")
        return f"Error ({error_code}): {error_msg}"

    # Format success result
    output_parts = []
    for key, value in result.items():
        if key not in ["x-source"]:  # Skip internal params
            output_parts.append(f"{key}: {value}")

    return "\n".join(output_parts) if output_parts else "Success (no data returned)"


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Handle tool execution requests.
    Tools can modify server state and notify clients of changes.
    """
    arguments = arguments or {}

    if name == "add-note":
        note_name = arguments.get("name")
        content = arguments.get("content")

        if not note_name or not content:
            raise ValueError("Missing name or content")

        notes[note_name] = content
        await server.request_context.session.send_resource_list_changed()

        return [
            types.TextContent(
                type="text",
                text=f"Added note '{note_name}' with content: {content}",
            )
        ]

    # === View Opening Tools ===
    elif name == "on-the-agenda":
        url = build_url("on-the-agenda", [])
        try:
            XCallbackURLHandler.call_url(url)
            return [types.TextContent(type="text", text="Opened 'On the Agenda' overview")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open overview: {str(e)}")]

    elif name == "today":
        url = build_url("today", [])
        try:
            XCallbackURLHandler.call_url(url)
            return [types.TextContent(type="text", text="Opened Today overview")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open Today: {str(e)}")]

    elif name == "open-overview":
        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")

        url = build_url("open-overview", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'saved overview'))
            return [types.TextContent(type="text", text=f"Opened overview '{desc}'")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open overview: {str(e)}")]

    elif name == "open-search":
        params = []
        add_string_param(params, arguments, "query")

        url = build_url("open-search", params)
        try:
            XCallbackURLHandler.call_url(url)
            query = arguments.get('query', '')
            msg = f"Opened search with query '{query}'" if query else "Opened search"
            return [types.TextContent(type="text", text=msg)]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open search: {str(e)}")]

    elif name == "open-project":
        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")
        add_bool_param(params, arguments, "separate_window", "separate-window")

        url = build_url("open-project", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'project'))
            return [types.TextContent(type="text", text=f"Opened project '{desc}'")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open project: {str(e)}")]

    elif name == "open-agenda-note":
        if not any(key in arguments for key in ["title", "identifier"]):
            raise ValueError("Either title or identifier must be provided")

        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")
        add_string_param(params, arguments, "project_title", "project-title")
        add_bool_param(params, arguments, "separate_window", "separate-window")

        url = build_url("open-note", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'note'))
            return [types.TextContent(type="text", text=f"Opened note '{desc}' in Agenda")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to open note: {str(e)}")]

    # === Data Query Tools (with callback support) ===
    elif name == "get-identifier":
        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "project_title", "project-title")

        base_url = "agenda://x-callback-url/get-identifier"
        try:
            result = await XCallbackURLHandler.call_url_with_callback(base_url, params)
            return [types.TextContent(type="text", text=format_callback_result(result))]
        except TimeoutError:
            return [types.TextContent(type="text", text="Timeout waiting for Agenda response")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to get identifier: {str(e)}")]

    elif name == "get-selected-project":
        base_url = "agenda://x-callback-url/get-selected-project"
        try:
            result = await XCallbackURLHandler.call_url_with_callback(base_url, [])
            return [types.TextContent(type="text", text=format_callback_result(result))]
        except TimeoutError:
            return [types.TextContent(type="text", text="Timeout waiting for Agenda response")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to get selected project: {str(e)}")]

    elif name == "get-selected-note":
        base_url = "agenda://x-callback-url/get-selected-note"
        try:
            result = await XCallbackURLHandler.call_url_with_callback(base_url, [])
            return [types.TextContent(type="text", text=format_callback_result(result))]
        except TimeoutError:
            return [types.TextContent(type="text", text="Timeout waiting for Agenda response")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to get selected note: {str(e)}")]

    elif name == "get-selection":
        base_url = "agenda://x-callback-url/get-selection"
        try:
            result = await XCallbackURLHandler.call_url_with_callback(base_url, [])
            return [types.TextContent(type="text", text=format_callback_result(result))]
        except TimeoutError:
            return [types.TextContent(type="text", text="Timeout waiting for Agenda response")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to get selection: {str(e)}")]

    # === SQLite-based Read Tools ===
    elif name == "search-notes":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 20)

        if not query:
            raise ValueError("Missing required parameter: query")

        try:
            results = agenda_db.search_notes(query, limit=limit)
            if not results:
                return [types.TextContent(type="text", text=f"No notes found matching '{query}'")]

            output = [f"Found {len(results)} notes matching '{query}':\n"]
            for note in results:
                output.append(f"\n**{note.title}** (ID: {note.id})")
                if note.content:
                    preview = note.content[:200].replace("\n", " ")
                    output.append(f"  {preview}...")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to search notes: {str(e)}")]

    elif name == "read-note":
        identifier = arguments.get("identifier", "")

        if not identifier:
            raise ValueError("Missing required parameter: identifier")

        try:
            note = agenda_db.read_note(identifier)
            if not note:
                return [types.TextContent(type="text", text=f"Note not found: {identifier}")]

            output = f"# {note.title}\n\n{note.content}"
            return [types.TextContent(type="text", text=output)]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to read note: {str(e)}")]

    elif name == "list-projects":
        try:
            projects = agenda_db.list_projects()
            if not projects:
                return [types.TextContent(type="text", text="No projects found")]

            output = [f"Found {len(projects)} projects:\n"]
            for project in projects:
                output.append(f"- **{project.title}** (ID: {project.id})")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to list projects: {str(e)}")]

    elif name == "list-notes":
        limit = arguments.get("limit", 100)

        try:
            notes_list = agenda_db.list_notes(limit=limit)
            if not notes_list:
                return [types.TextContent(type="text", text="No notes found")]

            output = [f"Found {len(notes_list)} notes:\n"]
            for note in notes_list:
                output.append(f"- **{note.title}** (ID: {note.id})")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to list notes: {str(e)}")]

    # === New SQLite-based Tools ===
    elif name == "get-notes-by-date-range":
        start_date_str = arguments.get("start_date", "")
        end_date_str = arguments.get("end_date", "")
        limit = arguments.get("limit", 100)

        if not start_date_str or not end_date_str:
            raise ValueError("Missing required parameters: start_date and end_date")

        try:
            from datetime import datetime
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")

        try:
            results = agenda_db.get_notes_by_date_range(start_date, end_date, limit=limit)

            if not results:
                return [types.TextContent(type="text", text=f"No notes found between {start_date_str} and {end_date_str}")]

            output = [f"Found {len(results)} notes between {start_date_str} and {end_date_str}:\n"]

            for note in results:
                date_str = note.start_date.strftime("%Y-%m-%d") if note.start_date else "No date"
                end_date_display = f" to {note.end_date.strftime('%Y-%m-%d')}" if note.end_date else ""
                project_info = f" in '{note.project_title}'" if note.project_title else ""

                output.append(f"\n**{note.title}** ({date_str}{end_date_display}){project_info}")
                output.append(f"  ID: {note.id}")
                if note.content:
                    preview = note.content[:150].replace("\n", " ")
                    output.append(f"  {preview}")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to get notes by date range: {str(e)}")]

    elif name == "list-on-the-agenda":
        limit = arguments.get("limit", 50)
        include_content = arguments.get("include_content", True)

        try:
            notes_list = agenda_db.list_on_the_agenda(limit=limit, include_content=include_content)
            if not notes_list:
                return [types.TextContent(type="text", text="No notes currently marked as 'On the Agenda'")]

            output = [f"Found {len(notes_list)} notes 'On the Agenda':\n"]
            for note in notes_list:
                project_info = f" (Project: {note.project_title})" if note.project_title else ""
                output.append(f"\n**{note.title}**{project_info} (ID: {note.id})")
                if note.content:
                    preview = note.content.replace("\n", " ")[:200]
                    output.append(f"  {preview}")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to list on-the-agenda notes: {str(e)}")]

    elif name == "set-on-the-agenda":
        if not any(key in arguments for key in ["title", "identifier"]):
            raise ValueError("Either title or identifier must be provided")
        if "on_the_agenda" not in arguments:
            raise ValueError("Missing required parameter: on_the_agenda")

        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")
        add_string_param(params, arguments, "project_title", "project-title")
        add_bool_param(params, arguments, "on_the_agenda", "on-the-agenda")

        url = build_url("append-to-note", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'note'))
            status = "added to" if arguments.get("on_the_agenda") else "removed from"
            return [types.TextContent(type="text", text=f"Note '{desc}' {status} On the Agenda")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to set On the Agenda status: {str(e)}")]

    elif name == "list-tags":
        include_note_ids = arguments.get("include_note_ids", False)
        limit = arguments.get("limit", 100)

        try:
            tags = agenda_db.list_tags(include_note_ids=include_note_ids, limit=limit)

            if not tags:
                return [types.TextContent(type="text", text="No tags found in any notes")]

            output = [f"Found {len(tags)} tags:\n"]
            for tag in tags:
                line = f"- **#{tag.name}** ({tag.count} uses)"
                if include_note_ids and tag.note_ids:
                    line += f" - in {len(tag.note_ids)} notes"
                output.append(line)

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to list tags: {str(e)}")]

    elif name == "search-by-tags":
        tags = arguments.get("tags", [])
        match_mode = arguments.get("match_mode", "or")
        limit = arguments.get("limit", 50)

        if not tags:
            raise ValueError("Missing required parameter: tags")

        try:
            results = agenda_db.search_by_tags(tags, match_mode=match_mode, limit=limit)

            if not results:
                tags_str = ", ".join(f"#{t.lstrip('#')}" for t in tags)
                return [types.TextContent(type="text", text=f"No notes found with tags: {tags_str}")]

            tags_str = ", ".join(f"#{t.lstrip('#')}" for t in tags)
            output = [f"Found {len(results)} notes with tags ({match_mode.upper()}): {tags_str}\n"]

            for note in results:
                output.append(f"\n**{note.title}** (ID: {note.id})")
                if note.content:
                    preview = note.content[:200].replace("\n", " ")
                    output.append(f"  {preview}...")

            return [types.TextContent(type="text", text="\n".join(output))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to search by tags: {str(e)}")]

    elif name == "search-in-project":
        query = arguments.get("query", "")
        project_id = arguments.get("project_id")
        project_title = arguments.get("project_title")
        limit = arguments.get("limit", 50)

        if not query:
            raise ValueError("Missing required parameter: query")
        if not project_id and not project_title:
            raise ValueError("Either project_id or project_title must be provided")

        try:
            results = agenda_db.search_notes_in_project(
                query,
                project_id=project_id,
                project_title=project_title,
                limit=limit
            )

            if not results:
                project_desc = project_title or project_id
                return [types.TextContent(type="text", text=f"No notes found matching '{query}' in project '{project_desc}'")]

            project_desc = project_title or project_id
            output = [f"Found {len(results)} notes matching '{query}' in project '{project_desc}':\n"]

            for note in results:
                output.append(f"\n**{note.title}** (ID: {note.id})")
                if note.content:
                    preview = note.content[:200].replace("\n", " ")
                    output.append(f"  {preview}...")

            return [types.TextContent(type="text", text="\n".join(output))]
        except ValueError as e:
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to search in project: {str(e)}")]

    # === Creation Tools ===
    elif name == "create-category":
        if "title" not in arguments:
            raise ValueError("Missing required parameter: title")

        params = [f"title={quote(arguments['title'])}"]

        url = build_url("create-category", params)
        try:
            XCallbackURLHandler.call_url(url)
            return [types.TextContent(type="text", text=f"Created category '{arguments['title']}'")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to create category: {str(e)}")]

    elif name == "create-agenda-project":
        if "title" not in arguments:
            raise ValueError("Missing required parameter: title")

        params = [f"title={quote(arguments['title'])}"]
        add_string_param(params, arguments, "category_title", "category-title")
        add_string_param(params, arguments, "identifier")
        add_bool_param(params, arguments, "select")
        add_string_param(params, arguments, "sort_order", "sort-order")

        url = build_url("create-project", params)
        try:
            XCallbackURLHandler.call_url(url)
            return [types.TextContent(type="text", text=f"Created project '{arguments['title']}' in Agenda")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to create project: {str(e)}")]

    elif name == "create-agenda-note":
        if "title" not in arguments or "text" not in arguments:
            raise ValueError("Missing required parameters: title and text")

        params = [
            f"title={quote(arguments['title'])}",
            f"text={quote(arguments['text'])}"
        ]

        # Project identification
        add_string_param(params, arguments, "project_title", "project-title")
        add_string_param(params, arguments, "identifier")

        # Date parameters
        add_string_param(params, arguments, "date")
        add_string_param(params, arguments, "start_date", "start-date")
        add_string_param(params, arguments, "end_date", "end-date")

        # Template parameters
        add_string_param(params, arguments, "template_name", "template-name")
        add_string_param(params, arguments, "template_input", "template-input")

        # Boolean flags
        add_bool_param(params, arguments, "on_the_agenda", "on-the-agenda")
        add_bool_param(params, arguments, "collapsed")
        add_bool_param(params, arguments, "completed")
        add_bool_param(params, arguments, "pinned")
        add_bool_param(params, arguments, "footnote")
        add_bool_param(params, arguments, "select")

        # Attachment parameters
        add_string_param(params, arguments, "attachment")
        add_string_param(params, arguments, "filename")
        add_string_param(params, arguments, "display_style", "display-style")
        add_int_param(params, arguments, "display_size", "display-size")

        # Calendar event
        add_string_param(params, arguments, "event_title", "event-title")

        url = build_url("create-note", params)
        try:
            XCallbackURLHandler.call_url(url)
            return [types.TextContent(type="text", text=f"Created note '{arguments['title']}' in Agenda")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to create note: {str(e)}")]

    # === Modification Tools ===
    elif name == "append-to-note":
        if not any(key in arguments for key in ["title", "identifier"]):
            raise ValueError("Either title or identifier must be provided")

        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")
        add_string_param(params, arguments, "project_title", "project-title")
        add_string_param(params, arguments, "text")
        add_string_param(params, arguments, "attachment")
        add_string_param(params, arguments, "filename")
        add_string_param(params, arguments, "display_style", "display-style")
        add_int_param(params, arguments, "display_size", "display-size")

        url = build_url("append-to-note", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'note'))
            return [types.TextContent(type="text", text=f"Appended content to note '{desc}'")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to append to note: {str(e)}")]

    elif name == "replace-note":
        if not any(key in arguments for key in ["title", "identifier"]):
            raise ValueError("Either title or identifier must be provided")
        if "text" not in arguments:
            raise ValueError("Missing required parameter: text")

        params = []
        add_string_param(params, arguments, "title")
        add_string_param(params, arguments, "identifier")
        add_string_param(params, arguments, "project_title", "project-title")
        add_string_param(params, arguments, "text")

        url = build_url("replace-note", params)
        try:
            XCallbackURLHandler.call_url(url)
            desc = arguments.get('title', arguments.get('identifier', 'note'))
            return [types.TextContent(type="text", text=f"Replaced content of note '{desc}'")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Failed to replace note content: {str(e)}")]

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="mcp-server-agenda",
                server_version="0.2.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
