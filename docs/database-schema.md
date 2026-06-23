# SQLShelf — Database Schema Reference

The index lives at `<project_root>/.sqlshelf/index.db`. It is a fully regenerable SQLite 3 file; deleting it and reopening the project rebuilds everything from the `.sql` files on disk. Never store anything in this database that does not also exist on disk.

Current schema version: **2** (stored in `meta`).

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
| `schema_version` | `"2"` | Integer version used for migration decisions |
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

SQL objects (tables, columns, procedures, functions) extracted from the query body via `sqlglot` with dialect `tsql`. CTEs are excluded from the table set.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `query_id`   | INTEGER | NOT NULL, FK → `queries(id)` CASCADE DELETE | Owning query |
| `object_type`| TEXT    | NOT NULL, CHECK IN (`'table'`,`'column'`,`'procedure'`,`'function'`) | Kind of SQL object |
| `object_name`| TEXT    | NOT NULL, COLLATE NOCASE | Identifier as it appears in the SQL AST |

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

### `queries_fts` (virtual table)

FTS5 full-text index over `queries`. Kept in sync with `queries` via triggers.

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

Three AFTER triggers on `queries` keep `queries_fts` in sync:

| Trigger | Event | Action |
|---------|-------|--------|
| `queries_ai` | AFTER INSERT | Inserts a new FTS row with `rowid = new.id` |
| `queries_ad` | AFTER DELETE | Deletes the FTS row where `rowid = old.id` |
| `queries_au` | AFTER UPDATE | Deletes the old FTS row and inserts a fresh one |

> Note: these triggers insert only an empty `objects` string (`''`). The `_insert_query()` method in `index_db.py` overrides the FTS row after inserting `query_objects`, replacing it with the full space-joined object names. This two-step approach avoids querying `query_objects` from inside the trigger.

---

## Indexes

| Name | On | Columns | Purpose |
|------|----|---------|---------|
| (implicit PK) | `queries` | `id` | Row lookup by surrogate key |
| (implicit UNIQUE) | `queries` | `rel_path` | Lookup / upsert by file path |
| (implicit UNIQUE) | `tags` | `name` | Deduplication on insert |
| `ix_query_objects_name` | `query_objects` | `object_name, object_type` | `table:X` and `col:X` filter scans |

---

## Foreign keys and cascades

`PRAGMA foreign_keys = ON` is set on every connection. All child tables cascade-delete when a parent `queries` row is removed, so deleting a query automatically cleans up its tags, objects, and FTS entry:

```
queries ──< query_tags   (CASCADE DELETE)
        ──< query_objects (CASCADE DELETE)
queries_fts ← trigger (not FK)
```

`favorites` and `recently_viewed` reference `rel_path` as plain text (no FK) so they survive a full reindex drop-and-recreate cycle.

---

## Schema versioning and migrations

`IndexDB._apply_schema()` runs at startup:

1. If the `meta` table does not exist → create the full schema from `schema.sql` and set `schema_version = '2'`.
2. If the schema is missing required columns → drop the database file entirely and recreate (safe because the index is regenerable).
3. If `schema_version = '1'` → run `_migrate_v1_to_v2()`, which adds the `favorites` and `recently_viewed` tables.

To add a new migration: increment `SCHEMA_VERSION`, add an `elif v == "2": _migrate_v2_to_v3()` branch, and implement the method.

---

## Search query syntax

The `search.py` module translates user-typed text into SQL before hitting the database:

| Syntax | Translates to |
|--------|---------------|
| *(empty)* | `SELECT … FROM queries ORDER BY file_mtime DESC, title` |
| free text | `queries_fts MATCH '"token1"* "token2"*'` ordered by `bm25()` |
| `table:Orders` | `query_objects WHERE object_type='table' AND object_name='Orders'` |
| `col:CustomerId` | `query_objects WHERE object_type='column' AND object_name='CustomerId'` |
| `tag:finance` | `query_tags JOIN tags WHERE name='finance'` |
| `date:DD/MM/YYYY` | `queries WHERE file_mtime BETWEEN start AND end` (full day, local time) |
| combinations | All filters joined with `AND`; FTS added when free text is present |

Snippets are generated by FTS5's built-in `snippet()` function over the `body` column (column index 2), with `[` / `]` highlight markers and a window of 20 tokens.
