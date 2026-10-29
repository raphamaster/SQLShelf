# SQLShelf — Database Schema Reference

The index lives at `<project_root>/.sqlshelf/index.db`. It is a fully regenerable SQLite 3 file; deleting it and reopening the project rebuilds everything from the `.sql` files on disk. Never store anything in this database that does not also exist on disk.

Current schema version: **6** (stored in `meta`; `IndexDB.SCHEMA_VERSION` in `index_db.py`).

---

## Tables

### `meta`

Key/value store for database-level metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `key`  | TEXT | PRIMARY KEY | Unique setting name |
| `value`| TEXT |             | Setting value |

Known keys:

| Key | Example value | Description |
|-----|--------------|-------------|
| `schema_version` | `"6"` | Integer version used for migration decisions |
| `project_root`   | `"C:/Users/Raphael/queries"` | Absolute path to the project folder at index creation time |
| `last_full_scan` | `"2026-06-23T14:00:00+00:00"` | ISO 8601 UTC timestamp of the last full reindex |

---

### `queries`

One row per `.sql` file found in the project folder. The file on disk is always the source of truth; this table is a cache.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id`             | INTEGER | PRIMARY KEY autoincrement | Internal surrogate key |
| `rel_path`       | TEXT    | NOT NULL, UNIQUE | POSIX-style path relative to the project root (e.g. `reports/sales.sql`) |
| `title`          | TEXT    | NOT NULL | Human-readable title from frontmatter, or the filename stem |
| `description`    | TEXT    |          | Free-text description from frontmatter |
| `body`           | TEXT    | NOT NULL | SQL body (frontmatter block stripped out) |
| `file_mtime`     | INTEGER | NOT NULL | Last-modified timestamp of the file (Unix epoch, integer seconds) |
| `file_size`      | INTEGER | NOT NULL | File size in bytes at last index time |
| `content_hash`   | TEXT    | NOT NULL | SHA-256 hex digest of the raw file bytes; used to skip unchanged files when mtime alone is ambiguous |
| `has_frontmatter`| INTEGER | NOT NULL, DEFAULT 1 | `1` if the file contained a valid `/* --- … --- */` YAML block, `0` otherwise |
| `created_at`     | TEXT    |          | ISO 8601 date from frontmatter `created` field (nullable) |
| `updated_at`     | TEXT    |          | ISO 8601 date from frontmatter `updated` field (nullable) |
| `dialect`        | TEXT    | NOT NULL, DEFAULT `'sql'` | The SQL dialect the query is read in: `tsql`, `postgres`, `mysql`, `mariadb`, `oracle`, `bigquery`, or `sql` when nothing shows. The frontmatter `dialect` field wins; otherwise it is detected from the syntax (`dialects.resolve`) |

**Incremental indexing logic** (`index_db.py`): on scan, if `file_mtime` matches the stored value the row is skipped entirely. If mtime changed, the SHA-256 is recomputed — if the hash also matches, only `file_mtime` is updated (touch without content change). Otherwise the row is deleted and re-inserted.

---

### `tags`

Deduplicated, case-insensitive tag vocabulary.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id`   | INTEGER | PRIMARY KEY autoincrement | Surrogate key |
| `name` | TEXT    | NOT NULL, UNIQUE COLLATE NOCASE | Tag string, stored lowercase |

---

### `query_tags`

Many-to-many join between queries and tags.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `query_id` | INTEGER | NOT NULL, FK → `queries(id)` CASCADE DELETE | Query that has the tag |
| `tag_id`   | INTEGER | NOT NULL, FK → `tags(id)` CASCADE DELETE    | Tag applied to the query |

Primary key: `(query_id, tag_id)`.

---

### `query_objects`

SQL objects (tables, columns, aliases, procedures, functions) extracted from the query body via `sqlglot`, with the parser of the query's dialect (`queries.dialect`; see `docs/sql-dialects.md`). CTEs are excluded from the table set.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `query_id`   | INTEGER | NOT NULL, FK → `queries(id)` CASCADE DELETE | Owning query |
| `object_type`| TEXT    | NOT NULL, CHECK IN (`'table'`,`'column'`,`'alias'`,`'procedure'`,`'function'`) | Kind of SQL object |
| `object_name`| TEXT    | NOT NULL, COLLATE NOCASE | Identifier as it appears in the SQL AST |

`alias` rows hold the table aliases of the query (`FROM Orders AS o` → `o`). They are stored so that navigating the list never parses the SQL again; they are not tables, and `table:` does not match them.

Primary key: `(query_id, object_type, object_name)`.

Index: `ix_query_objects_name (object_name, object_type)` — accelerates `table:X` and `col:X` search filters.

---

### `favorites`

Stores the set of queries the user has starred. Uses `rel_path` directly (no FK) so favorites survive a full reindex.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `rel_path` | TEXT | PRIMARY KEY | POSIX-style path relative to the project root |

---

### `recently_viewed`

Ring-buffer of the 20 most recently opened queries (oldest entry deleted automatically on insert).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `rel_path`  | TEXT | NOT NULL, PRIMARY KEY | POSIX-style path relative to the project root |
| `viewed_at` | TEXT | NOT NULL | ISO 8601 UTC timestamp of the last view event |

---

### `access_log`

One row per access event, read by the Reports dialog (_Help → Reports_) to rank the most-accessed queries and tags. Written by `IndexDB.record_access()`. Like `favorites`, it references `rel_path` as plain text, so the history survives a full reindex.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id`          | INTEGER | PRIMARY KEY autoincrement | Surrogate key |
| `rel_path`    | TEXT    | NOT NULL | POSIX-style path relative to the project root |
| `action`      | TEXT    | NOT NULL, CHECK IN (`'open'`,`'copy'`,`'open_in_ssms'`) | What the user did with the query |
| `accessed_at` | TEXT    | NOT NULL | ISO 8601 UTC timestamp of the event |

Indexes: `ix_access_log_rel_path (rel_path)` and `ix_access_log_accessed_at (accessed_at)`.

---

### `queries_fts` (virtual table)

FTS5 full-text index over `queries`. Each row is written by `_insert_prepared()` in `index_db.py` together with its `queries` row, and removed by the `queries_ad` trigger (see [Triggers](#triggers)).

```sql
CREATE VIRTUAL TABLE queries_fts USING fts5(
    title,
    description,
    body,
    objects,
    tokenize = "unicode61 remove_diacritics 2"
);
```

| Column | Source |
|--------|--------|
| `title`       | `queries.title` |
| `description` | `queries.description` |
| `body`        | `queries.body` |
| `objects`     | Space-joined list of `query_objects.object_name` values for the row |

The `unicode61 remove_diacritics 2` tokenizer normalises accents, so searches in Portuguese (`nome`, `nomé`) match regardless of diacritics.

The `rowid` of each `queries_fts` row equals the `id` of the corresponding `queries` row, enabling efficient `JOIN queries_fts ON queries_fts.rowid = q.id`.

---

## Triggers

One AFTER trigger on `queries` keeps `queries_fts` in sync:

| Trigger | Event | Action |
|---------|-------|--------|
| `queries_ad` | AFTER DELETE | Deletes the FTS row where `rowid = old.id` |

FTS rows are written by `_insert_prepared()` in `index_db.py`, which is the only
code that knows the extracted object names.

> There used to be `queries_ai` (AFTER INSERT) and `queries_au` (AFTER UPDATE)
> triggers as well. A trigger cannot see the extracted object names, so both
> could only write `objects = ''`, relying on `_insert_prepared()` to overwrite
> the row immediately afterwards. That held only on the insert path: any other
> `UPDATE` on `queries` — a mtime-only touch from a cloud-sync client, for
> instance — fired `queries_au` and silently erased every table and column name
> from the search index, with nothing to put them back until the next full
> reindex. Both triggers were removed. `queries_ad` stays because several code
> paths delete straight from `queries`.

---

## Indexes

| Name | On | Columns | Purpose |
|------|----|---------|---------|
| (implicit PK) | `queries` | `id` | Row lookup by surrogate key |
| (implicit UNIQUE) | `queries` | `rel_path` | Lookup / upsert by file path |
| (implicit UNIQUE) | `tags` | `name` | Deduplication on insert |
| `ix_query_objects_name` | `query_objects` | `object_name, object_type` | `table:X` and `col:X` filter scans |
| `ix_access_log_rel_path` | `access_log` | `rel_path` | Per-query access counts |
| `ix_access_log_accessed_at` | `access_log` | `accessed_at` | Time-ordered reads of the log |

---

## Foreign keys and cascades

`PRAGMA foreign_keys = ON` is set on every connection. All child tables cascade-delete when a parent `queries` row is removed, so deleting a query automatically cleans up its tags, objects, and FTS entry:

```
queries ──< query_tags   (CASCADE DELETE)
        ──< query_objects (CASCADE DELETE)
queries_fts ← trigger (not FK)
```

`favorites`, `recently_viewed` and `access_log` reference `rel_path` as plain text (no FK) so they survive a full reindex.

---

## Schema versioning and migrations

`IndexDB._apply_schema()` runs at startup:

1. If the `meta` table does not exist → create the full schema from `schema.sql` and store `SCHEMA_VERSION`.
2. If the stored `schema_version` differs from `SCHEMA_VERSION`, or the `queries` table is missing a required column → delete the database file and recreate it; the next scan reindexes every file.

The index is regenerable from the `.sql` files, so "delete and reindex" is the only migration. To change the schema: edit `schema.sql`, bump `SCHEMA_VERSION`, and add any new `queries` column to the required set in `_is_schema_compatible()`.

---

## Search query syntax

The `search.py` module translates user-typed text into SQL before hitting the database:

| Syntax | Translates to |
|--------|---------------|
| *(empty)* | `SELECT … FROM queries ORDER BY file_mtime DESC, title` |
| free text | `queries_fts MATCH '"token1"* "token2"*'`, ordered by relevance (see below) |
| `table:Orders` | `query_objects WHERE object_type='table' AND object_name='Orders'` |
| `col:CustomerId` | `query_objects WHERE object_type='column' AND object_name='CustomerId'` |
| `tag:finance` | `query_tags JOIN tags WHERE name='finance'` |
| `date:DD/MM/YYYY` | `queries WHERE file_mtime BETWEEN start AND end` (full day, local time) |
| `dialect:oracle` | `queries WHERE dialect='oracle'`; the value takes the frontmatter names, so `dialect:PL/SQL` is the same filter |
| combinations | All filters joined with `AND`; FTS added when free text is present |

### Ranking

A free-text search is ordered by `relevance_key()` in `search.py`, closest matches first:

1. **Tier** (`SearchResult.match_tier`) — `0` when the `title` column holds every searched word, `1` when `description` does, `2` otherwise (the words are only in the SQL or its object names). Each tier is one extra `MATCH` restricted to that column (`title : (…)`).
2. **Rank** (`SearchResult.rank`) — `bm25(queries_fts, 10.0, 4.0, 1.0, 2.0)`, weighting `title`, `description`, `body` and `objects` in that order. Lower is better.
3. **Most recently modified** — `file_mtime`, newest first, to break ties.

`bm25()` alone was not enough: term frequency saturates, so a script that repeats an alias like `p_cdc` thirty times scored about as high as one word in the title, and came before the query named "Pedidos_CDC".

Searches without free text have rank `0` and tier `0`, so they come out newest first. The query list offers this order as "Relevance (closest first)" only while a search is shown; a search starts with it and remembers its own sort (`search_sort_key` in `config.json`), apart from the one used to browse folders. Results merged from several library folders use the same key.

`SearchResult.snippet` is part of the model but is no longer populated: generating it with `snippet()` re-tokenises the body of every matching row and accounted for roughly 95% of the time a text search took, while no part of the UI displays it.
