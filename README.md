# Agenda Note MCP Server

A Model Context Protocol (MCP) server that enables Claude AI to interact with the [Agenda](https://agenda.com) note-taking app on macOS. This server provides comprehensive access to your Agenda notes through both x-callback-URL commands and direct SQLite database access.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](https://www.apple.com/macos)

## Features

### 24 Tools for Full Agenda Integration

| Category | Tools | Description |
|----------|-------|-------------|
| **Read/Search** | 10 | Search notes, read content, list projects, filter by date/tags |
| **Create/Modify** | 7 | Create notes/projects, append content, manage "On the Agenda" |
| **Metadata** | 4 | Get identifiers, selected items |
| **UI Navigation** | 6 | Open views, projects, notes in Agenda |

### Key Capabilities

- **Direct Database Access**: Read notes directly from Agenda's SQLite database for instant results
- **Full x-callback-url Support**: Create notes, projects, and trigger Agenda actions
- **Tag Management**: List all tags, search by tags with AND/OR logic
- **Date Filtering**: Find notes within specific date ranges
- **Project Search**: Search within specific projects
- **"On the Agenda" Management**: List and toggle OTA status

## Requirements

- **macOS** (Agenda is macOS/iOS only)
- **Python 3.12+**
- **[Agenda](https://agenda.com)** app installed
- **[uv](https://docs.astral.sh/uv/)** package manager (recommended)
- **Claude Desktop** or any MCP-compatible client

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Jayjaejae/agenda-note-mcp.git
cd agenda-note-mcp
```

### 2. Install Dependencies

```bash
uv sync
```

### 3. Configure Claude Desktop

Add to your Claude Desktop configuration file:

**Location**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "agenda": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/agenda-note-mcp",
        "run",
        "agenda-note-mcp"
      ]
    }
  }
}
```

### 4. (Optional) Install Helper App for Callback Support

For tools that retrieve data from Agenda (like `get-selected-note`):

1. Copy `helper-app/AgendaMCPCallback.app` to `/Applications` or `~/Applications`
2. Launch it once to register the `agenda-mcp://` URL scheme
3. Grant necessary permissions when prompted

## Available Tools

### Reading & Searching Notes

| Tool | Description | Example |
|------|-------------|---------|
| `search-notes` | Search notes by keyword | "Search for meeting notes" |
| `read-note` | Read full note content by ID | "Read note ABC-123" |
| `list-notes` | List all note titles | "Show all my notes" |
| `list-projects` | List all projects | "What projects do I have?" |
| `get-notes-by-date-range` | Filter notes by date | "Notes from January 2024" |
| `list-on-the-agenda` | List OTA notes | "What's on my agenda?" |
| `list-tags` | List all hashtags | "What tags am I using?" |
| `search-by-tags` | Find notes with tags | "Notes tagged #work" |
| `search-in-project` | Search within project | "Search 'budget' in Finance" |

### Creating & Modifying

| Tool | Description |
|------|-------------|
| `create-agenda-note` | Create a new note with full options |
| `create-agenda-project` | Create a new project |
| `create-category` | Create a new category |
| `append-to-note` | Add content to existing note |
| `replace-note` | Replace note content |
| `set-on-the-agenda` | Toggle OTA status |
| `add-note` | Simple note creation |

### Metadata & Selection

| Tool | Description |
|------|-------------|
| `get-identifier` | Get note/project UUID |
| `get-selected-project` | Get currently selected project |
| `get-selected-note` | Get currently selected note |
| `get-selection` | Get current selection info |

### UI Navigation

| Tool | Description |
|------|-------------|
| `on-the-agenda` | Open OTA view |
| `today` | Open Today view |
| `open-overview` | Open saved overview |
| `open-search` | Open search with query |
| `open-project` | Open specific project |
| `open-agenda-note` | Open specific note |

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENDA_STORES_PATH` | Custom Agenda data location | Standard macOS path |
| `AGENDA_CALLBACK_DIR` | Custom callback directory | `~/.agenda-mcp-callbacks` |

### Example Configuration

```bash
# In your shell profile or Claude Desktop config
export AGENDA_STORES_PATH="/custom/path/to/Agenda/Stores"
export AGENDA_CALLBACK_DIR="/custom/path/to/callbacks"
```

## Usage Examples

### Search Notes
```
User: Search my notes for "project planning"
Claude: [Uses search-notes tool]
Found 5 notes matching 'project planning':
- Q1 Project Planning (ID: ABC-123)
- Annual Planning Meeting (ID: DEF-456)
...
```

### Create a Note
```
User: Create a note titled "Meeting Notes" with "Discussed Q2 goals" in the Work project
Claude: [Uses create-agenda-note tool]
Created note 'Meeting Notes' in Agenda
```

### Filter by Date
```
User: Show me notes from last week
Claude: [Uses get-notes-by-date-range tool]
Found 12 notes between 2024-01-08 and 2024-01-14:
...
```

### Search by Tags
```
User: Find all notes tagged with #urgent or #priority
Claude: [Uses search-by-tags tool]
Found 8 notes with tags (OR): #urgent, #priority
...
```

## Architecture

```
+-----------------------------------------------------------+
|                    Claude Desktop                          |
|                         |                                  |
|                    MCP Protocol                            |
|                         v                                  |
|  +----------------------------------------------------+   |
|  |              Agenda MCP Server                      |   |
|  |  +-------------+      +-------------------------+  |   |
|  |  | x-callback  |      |  SQLite Direct Access   |  |   |
|  |  |   Handler   |      |     (agenda_db.py)      |  |   |
|  |  +------+------+      +-----------+-------------+  |   |
|  +---------|--------------------------|--------------+   |
|            |                          |                   |
|            v                          v                   |
|     +------------+          +------------------+          |
|     | Agenda App |          | Agenda Database  |          |
|     |  (macOS)   |          | (SQLite files)   |          |
|     +------------+          +------------------+          |
+-----------------------------------------------------------+
```

## Technical Details

### Database Access

Agenda stores notes in SQLite databases using a CRDT (Conflict-free Replicated Data Type) format:

- **Location**: `~/Library/Group Containers/WRBK2Z2EG7.group.com.momenta.agenda.macos/Release/Application/Stores/`
- **Format**: Binary plist blobs in `Changes.db` files
- **Key patterns**: `Section:{UUID}:title`, `Paragraph:{UUID}:content`, etc.

### x-callback-url Support

Full implementation of [Agenda's x-callback-url scheme](https://agenda.community/t/x-callback-url-support-and-reference/27253):

- Note creation with all parameters
- Project and category creation
- View navigation
- Selection queries (with helper app)

## Development

### Running Locally

```bash
# Install dependencies
uv sync

# Run the server directly
uv run agenda-note-mcp

# Run tests
uv run pytest
```

### Building the Helper App

```bash
cd helper-app
swiftc -o AgendaMCPCallback main.swift
# Then create .app bundle manually
```

## Troubleshooting

### Notes Not Found

- Ensure Agenda is running and has synced
- Check `AGENDA_STORES_PATH` if using custom location
- Verify database files exist in the Stores directory

### Callback Tools Not Working

- Install and launch `AgendaMCPCallback.app`
- Check that `agenda-mcp://` URL scheme is registered
- Verify callback directory permissions

### Permission Issues

- Grant Full Disk Access to the terminal/app if needed
- Ensure read permissions on Agenda's data directory

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Agenda](https://agenda.com) - The beautiful note-taking app
- [Model Context Protocol](https://modelcontextprotocol.io/) - The protocol specification
- [alexgoller/mcp-server-agenda](https://github.com/alexgoller/mcp-server-agenda) - Original MCP server implementation

## Related Projects

- [MCP Servers](https://github.com/modelcontextprotocol/servers) - Official MCP server implementations
- [Claude Desktop](https://claude.ai/download) - Anthropic's desktop client
