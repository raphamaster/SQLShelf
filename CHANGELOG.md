# Changelog

## [1.0.1] - 2025-06-14

### Fixed
- UI labels showing translation keys (e.g. `SIDEBAR.OPEN_FOLDER`) instead of text — `locales/` directory was missing from the PyInstaller bundle

---

## [1.0.0] - 2025-06-14

First public release of SQLShelf — open-source desktop SQL query manager built with Python + PySide6.

### Features

**Core**
- Open and index any folder of `.sql` files with automatic frontmatter YAML detection
- Multi-folder explorer with persistent project selection and global "All queries" view
- Full-text search (FTS5) with `table:`, `col:`, and `tag:` prefix filters
- Debounced async search with green search bar styling
- File watcher (watchdog) with ~500ms debounce — index stays in sync without manual refresh
- SQLite index is fully regeneratable; `.sql` files on disk are the source of truth

**Editor**
- SQL syntax highlighting via `QSyntaxHighlighter`
- Line numbers and current-line highlight
- Metadata panel with read/edit mode (title, description, tags, favorite, author)
- Tag chips with inline editor
- Clickable absolute file path in metadata panel
- Copy frontmatter template to clipboard (Edit menu)

**UI**
- Light and dark themes via Settings → Theme (live switching)
- Language toggle EN/PT-BR via Settings → Language (i18n system)
- Two-line query list delegate with title, description and tag summary
- Sidebar with priority nav (Favorites, Recent) and collapsible Browse section
- Command palette button in toolbar
- Logo swaps on theme toggle

**Indexing**
- Progress dialog during initial indexing
- Deindex (remove stale entries) on project close
- Startup status feedback

### Platform

| Platform | Artifact |
|----------|----------|
| Windows 10/11 x64 | `SQLShelf-1.0.1-windows-x64-setup.exe` (Inno Setup installer) |
| Linux x86_64 | `SQLShelf-1.0.1-linux-x86_64.AppImage` |

### Requirements (if running from source)

- Python 3.12+
- `pip install -r requirements.txt`
- `python main.py`
