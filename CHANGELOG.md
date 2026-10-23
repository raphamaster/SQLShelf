# Changelog

## [Unreleased]

### Added
- **Address bar with history** — Back, Forward and Up (Alt+←, Alt+→, Alt+↑) walk through what you looked at: library, folder, tag, favorites, recent or a search. Typing a search is one history step, not one per keystroke. The breadcrumb next to the arrows shows where you are and jumps back to any level; the search field says what it searches in
- **Command bar** — New query, Open folder, Sort, Group and View, Quick search, a details-panel toggle (Alt+P) and a ⋯ menu that holds everything the menu bar had
- **Status bar** — query and selection counts on the left; on the right, the index state, a progress bar while a folder is indexed, and how many folders are being watched
- **Remembered layout** — panel widths, the details panel being shown or hidden, the sort order and the menu bar choice survive a restart

### Changed
- **New theme, without qt-material** — the interface now uses Qt's Fusion style with a palette and stylesheet generated from design tokens (`sqlshelf/ui/tokens.py` → `sqlshelf/ui/theme.py`). Buttons are no longer upper case or outlined in green; green is kept for selection, focus and the primary action. This is the first slice (F1) of the redesign in `docs/redesign-ui.md`
- **Sentence-case labels** — sidebar and details-panel section titles (Browse, Folders, Tags, Tables, …) are no longer written in capitals, in all three languages
- **Editor colours and font** — new syntax colours for both themes, italic comments, and Cascadia Mono 13 px (falling back to Consolas, then the system monospace font)
- **Three-panel window** — sidebar, query list and details panel in one resizable row; the menu bar is hidden by default (View → Show Menu Bar brings it back) and every shortcut keeps working without it. This is the second slice (F2) of the redesign
- **Sidebar as a folder tree** — All queries, Favorites and Recent, then every library folder with its subfolders (expand with the chevron; clicking a name opens it), then the most used tags with a colour dot and "All tags" for the rest. Every row shows how many queries it holds; Recent and the tags count what is inside the folder in view. Opening a folder moved to the + next to Folders. This is the third slice (F3) of the redesign
- **Browse subfolders** — choosing a subfolder lists only the queries under it, and search, Recent and tags stay inside it
- **Indexing no longer opens a dialog** — progress shows in the status bar and the window stays usable
- **Every library folder is watched** — changes on disk are picked up in all open folders, not only the one in view
- **Query list rebuilt** — a Details view with Name, Tables, Tags and Modified columns (click Name or Modified to sort; the Sort menu follows) and a Content view with the description, table chips and every tag on taller rows. Queries are grouped by date (Today, Yesterday, This week, This month, Older), by folder or not at all, from the Group menu; groups fold with a click and stay folded when the list refreshes. The star shows on hover and on the selected row, and a click on it adds or removes the favorite without changing the selection. Search terms are marked in names and tables, ignoring accents and case the way the search does. Tables and Tags give way when the list gets narrow, and an empty list explains how to search. This is the fourth slice (F4) of the redesign
- **Fuller row menu** — Copy SQL, Edit metadata, Add to favorites, Open in SSMS, Copy path, Reveal in Explorer and Duplicate. Copy SQL on a query that is not open reads it in the background, and Edit metadata opens the details panel in edit mode
- **Theme switching reaches the tag chips** — the tag chips in the details panel kept the old theme's colours after a dark/light switch until another query was opened
- **Details panel rebuilt** — a header with the file icon, the title (two lines at most, the rest in a tooltip), the file name and a star; then Copy SQL, Edit, Open in SSMS and a ⋯ menu (Select all, Copy path, Copy folder path, Reveal in Explorer). Below, the description — or a hint to write one — and a grid with the tags (click one to list its queries), the tables (click to find them in the SQL), the folder (click to reveal it) and when and by whom the file was last changed. Rows with nothing to show are hidden. A thin strip above the editor shows the line count, the file's encoding and the dialect. The old editor toolbar is gone: its actions live in the panel now. This is the fifth slice (F5) of the redesign
- **Panel actions wait for the query** — Copy SQL, Edit and Open stay disabled until the SQL is in the editor, so they never act on the previous query
- **Open externally outside Windows** — where SSMS does not exist, the button opens the file in its default application
- **Edit mode rebuilt** — an Editing banner with Cancel and Save (Ctrl S) replaces the panel header, and the form has labelled fields for title, description (three lines tall), tags, author and the creation date. Tags are chips with a × to remove them; typing filters the library's tags with how many queries use each, the typed part marked, and Enter adds the picked one or creates a new tag (the popup says which). A comma or space ends a tag, Backspace in the empty field removes the last one, and Esc closes the suggestions before it cancels the edit. The strip above the editor shows "editable" and the cursor's line and column. The file is written in the background, every field the form does not show is kept, and a failed save stays in edit mode with nothing lost. This is the sixth slice (F6) of the redesign
- **Author in the form** — the author can be set while editing; leaving it empty removes it from the file
- **Search chips** — typing `table:`, `col:`, `tag:` or `date:` followed by a value and a space, Enter or Tab turns it into a chip inside the search field; × removes a chip and Backspace in the empty input removes the last one. The search itself is unchanged: chips and the remaining text are sent as before
- **Scope bar while searching** — "Search in" switches between the whole library and the folder the search started from, next to the result count and "Clear search"
- **Search results grouped by folder** — a search groups its results by folder by default and remembers its own grouping, apart from the one used when browsing
- **Tag views count results** — the status bar says "N results" in a tag view, as it does in a search

### Removed
- `qt-material` dependency — one fewer package to install and bundle

### Fixed
- **Favorites from another folder** — starring a query listed from a different library folder (All queries, Favorites, a search across the library) now updates that folder's index instead of the one in view
- **Removing a folder closes its index** — the index database used to stay open after the folder was removed from the sidebar
- **Esc outside edit mode** — it no longer reloads the open query from disk
- **Details after saving** — the panel shows the saved title, description and tags right away instead of the ones from before the edit

### Tests
- Theme tokens: both themes define the same keys, tag colours are stable across runs, and every text colour has at least 4.5:1 contrast on every background; plus a live-switch check and a guard against all-caps UI strings
- Navigation history (back, forward, up, dropping the forward stack on a new step), layout settings, and the main window offscreen: shortcuts with the menu bar hidden, Alt+P, Alt+arrows, search history and the status bar — 268 tests
- Sidebar: folder trees and their counts, subfolder prefixes (no `LIKE` wildcards, case kept), tag and Recent counts per folder, the summary off the UI thread, and the sidebar widget itself (rows, active row, chevron and + clicks, folded tags); in the window, a subfolder narrows the list and the tags, and opening a query updates Recent — 334 tests
- Query list: date buckets around midnight, the first day of the week and a month change; folder labels; term marking across accents, case and several terms; and the list widget offscreen — group order, selection kept when regrouping or sorting, header and star clicks, keyboard moving over group rows, folded groups surviving a refresh, Content view and narrow-width columns, and every cell painting — 405 tests
- Details panel: modification text, line elision, the editor strip and the shared title/description/tags reading; the panel offscreen — hidden empty rows, action and chip signals, the ⋯ menu, the Open label per platform, Portuguese labels, and a chip replaced from its own click; in the window, the strip and actions after a query loads, Copy SQL without the frontmatter, Ctrl+E and Esc — 471 tests
- Edit mode: keeping unknown frontmatter keys and dropping an empty author; tag normalizing, splitting and suggestion order (prefix first, then use, ignoring accents and case); match spans inside words; the creation date text; the tag field offscreen — Enter, comma and space, ×, Backspace, suggestions with counts, Up/Down and click, the Enter hint, Esc before the window's shortcut and focus; the form and banner; in the window, Ctrl+S writing the file and keeping its other fields, a failed save staying in edit mode, Esc closing the suggestions then the edit, and the cursor in the strip — 540 tests
- Search: splitting and joining operator tokens (round trip, quotes, case, text around them); the search field offscreen — chips on space, Enter and Tab, the cursor kept, × and Backspace, clearing, silent updates, the placeholder, long values shortened, focus; the scope bar; and in the window, chips that still search, moving and clearing the scope, the search's own grouping and a tag shown as a chip — 597 tests

## [1.0.11] - 2026-09-17

### Changed
- **Leaner metadata panel** — the query header no longer shows column and alias chips, only table names, which is what's actually useful at a glance

## [1.0.10] - 2026-09-15

### Fixed
- **Table names in the query list** — aliases and T-SQL `@variables` were being indexed and shown where table names belong; `UPDATE t SET ... FROM MyTable t` listed `t`, and `EXEC @rv = dbo.MyProc` listed `rv`. Across a 192-file library this removed every bogus name
- **Modification date losing its first digit** — the date column rendered `4/09/2026 18:56` instead of `14/09/2026 18:56`, because the column was measured with the wrong font metrics and then elided
- **Search dying on a quote** — typing `"` built an unterminated FTS5 expression; the error was swallowed and the box returned nothing, so search looked dead until the text was cleared. Quotes are now escaped
- **Search silently losing whole folders** — an `UPDATE` on an indexed row, including a modification-time-only touch from a cloud-sync client, erased table and column names from the full-text row, so `table:` and `col:` searches stopped matching. The FTS row is now written by the indexer, which is the only code that has the names

### Performance
- **Interface no longer freezes during indexing** — reads and writes use separate SQLite connections, so navigating and searching no longer queue behind a reindex. Opening a 59-file folder stalls the interface for 212 ms instead of 713 ms, and searching during a full reindex of 109 files stays under 10 ms
- **Faster text search** — dropped the per-row snippet generation that accounted for 87 of the 92 ms a text search took on a 109-file folder, for a string the interface never displayed

### Internal
- Project is now type-checked with `mypy` and formatted with `black`, both wired into `pyproject.toml`, with the development tools recorded in `requirements-dev.txt`

### Tests
- Regression coverage for alias and variable extraction, special characters in search, and full-text consistency after reindexing — 174 tests

## [1.0.9] - 2026-08-12

### Changed
- **Default query ordering** — query lists now open with the most recently modified files first in every navigation view
- **Large-file rendering** — scripts above 1,000 lines remain fully viewable and editable while expensive whole-document syntax and occurrence highlighting are suspended

### Fixed
- **Navigation freezes** — switching among folders, Favorites, Recent, tags and global navigation no longer blocks the interface on file reads or SQLite index locks
- **Redundant query reloads** — refreshing or reordering a result list preserves the selected query without reopening the same file
- **Alias extraction cache** — table aliases are now persisted in index schema v4, avoiding repeated `sqlglot` parsing during navigation
- **Stale query display** — the previous SQL body is cleared while a newly selected file loads asynchronously, preventing actions from targeting old content

### Tests
- Added UI regression coverage for large files, selection preservation, cross-folder result identity and default modification-date ordering

## [1.0.8] - 2026-08-03

### Added
- **Access log & Reports dialog** — SQLShelf now records when a query is opened, copied, or opened in SSMS; _Help → Reports_ shows the most-accessed queries and tags across all loaded folders
- **Modification date column** — the query list now shows each file's last-modified date/time (dd/mm/aaaa HH:MM) alongside the table name

### Fixed
- **Large `.sql` file performance** — opening, editing and navigating large SQL files (1000+ lines) no longer freezes the UI: object extraction (sqlglot) now runs off the UI thread with debouncing and caching, occurrence highlighting is capped and debounced, and the syntax highlighter's keyword patterns were merged into a single regex for faster re-scans

---

## [1.0.7] - 2026-06-23

### Added
- **System tray** — closing the main window now minimizes SQLShelf to the Windows system tray; double-click the tray icon or _Restore_ to bring it back
- **Windows autostart** — new toggle in Preferences (_Edit → Preferences_) to launch SQLShelf automatically on Windows login via the registry
- **Quit action** — _File → Quit_ exits the application immediately without minimizing to the tray
- **Status bar result count** — the status bar shows how many queries match the current search in real time
- **Statistics dialog** — _Help → Statistics_ shows a summary of the open project: total queries, tags, tables, columns and index size
- **Version in About dialog** — _Help → About_ now displays the current application version

### Changed
- **Preferences moved to Edit menu** — _Preferences_ action relocated to _Edit → Preferences_ for better discoverability
- **Open in SSMS removed** — _View → Open in SSMS_ action removed; use the clickable file path chip in the metadata panel to open a file externally

### Fixed
- **Tray exit** — `QApplication.quit()` is now called on the tray _Quit_ action, ensuring the event loop exits cleanly when the main window is hidden

### Docs
- Added comprehensive database schema reference (`docs/database-schema.md`) covering every table, column, trigger, index and search operator

---

## [1.0.6] - 2026-06-22

### Added
- **Force reindex progress modal** — clicking _Forçar Indexação_ now runs the scan in a background thread and shows a progress dialog with a progress bar, instead of blocking the UI silently

---

## [1.0.5] - 2026-06-22

### Added
- **Alias chips** — table aliases extracted from the SQL body (e.g. `FROM Orders AS o` → chip `o`) are displayed as a dedicated ALIASES section in the metadata panel; clicking navigates to the first occurrence in the editor
- **Select All / Copy buttons** — two buttons added to the editor toolbar in view mode: Select All selects the full SQL body; Copy copies it to the clipboard without selecting

### Fixed
- **Ctrl+N shortcut** — removed a duplicate `QShortcut("Ctrl+N")` that caused the _Ambiguous shortcut overload: Ctrl+N_ warning in the terminal; the shortcut on the menu action is sufficient

### Changed
- **Occurrence highlighting** — placing the cursor on a word (or selecting text) highlights all other occurrences in the SQL editor with a distinct background + foreground color, preserving readability over syntax-highlighted tokens
- **Metadata chip navigation** — left-clicking a table, column or alias chip now jumps to the first occurrence of that token in the current SQL editor (whole-word, case-insensitive); right-click shows a context menu with _Go to occurrence_, _Search in all queries_ and _Copy name_
- **File path chip** — right-click now shows a context menu: _Copy absolute path_, _Copy folder path_, _Reveal in Explorer_

---

## [1.0.4] - 2026-06-16

### Added
- **Check for Updates** — new menu item under Help; queries the GitHub Releases API in a background thread and shows a dialog with the result; if a newer version is found, offers a direct "Download Update" button (downloads and launches the Windows installer) or "Open Release Page" (browser); a silent check also runs 5 s after startup and displays a status-bar notification if an update is available
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
