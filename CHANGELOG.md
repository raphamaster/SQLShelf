# Changelog

## [1.0.4] - 2026-06-16

### Added
- **Sort button (⇅) in query list** — dropdown with four options: Name A→Z, Name Z→A, Modified newest, Modified oldest; active option shows a checkmark; sort persists across searches; translated in English, Portuguese (PT-BR) and Spanish
- **Portable distribution** — Windows and Linux releases now include a portable `.zip` alongside the installer/AppImage; extract and run with no installation required

### Fixed
- **Query list panel minimum width** — a second `setMinimumWidth(200)` call was silently overriding the 360 px set earlier, causing the panel to shrink on smaller screens; both calls are now 360 px
- **Query list panel resizing** — panel now uses `setMinimumWidth` instead of `setFixedWidth` so it never shrinks below 360 px but can still grow when the splitter is dragged or the window is maximized

### Changed
- **Tables/columns chip sections** — wrapped in a scroll area (max 52 px height) so queries with many tables or columns no longer push the SQL editor off screen
- **Search bar** — removed the help (?) button; search operator tooltip moved to the search field itself, visible on hover
- **Quick Search rename** — "Command Palette" renamed to "Quick Search" across all locales (EN, PT-BR, ES) to better reflect its purpose; hint and placeholder text now fully localized

---

## [1.0.3] - 2026-06-15

### Added
- **Tag autocomplete** — typing in the tag field (query editor and New Query dialog) now suggests existing tags from the index
- **Sort by modification date** — query list is sorted by file modification time (newest first) by default across all views (All queries, folder, search results)
- **Modification date in metadata panel** — file's last modification date is shown as a dedicated section below the file path
- **`date:` search filter** — search bar and command palette (Ctrl+P) now support `date:DD/MM/YYYY` to filter queries modified on a specific day; can be combined with other operators (e.g. `date:15/06/2026 tag:report`)

---

## [1.0.2] - 2025-06-14

### Fixed
- About dialog links now display in the app's accent green color (`#0ADE99`)

---

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
