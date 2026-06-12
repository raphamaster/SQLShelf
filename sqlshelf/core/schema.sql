-- .sqlshelf/index.db — fully regenerable from the .sql files on disk

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE queries (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    description     TEXT,
    body            TEXT    NOT NULL,
    file_mtime      INTEGER NOT NULL,
    file_size       INTEGER NOT NULL,
    content_hash    TEXT    NOT NULL,
    has_frontmatter INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE query_tags (
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (query_id, tag_id)
);

CREATE TABLE query_objects (
    query_id    INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    object_type TEXT    NOT NULL CHECK (object_type IN ('table','column','procedure','function')),
    object_name TEXT    NOT NULL COLLATE NOCASE,
    PRIMARY KEY (query_id, object_type, object_name)
);

CREATE INDEX ix_query_objects_name ON query_objects (object_name, object_type);

CREATE VIRTUAL TABLE queries_fts USING fts5(
    title,
    description,
    body,
    objects,
    tokenize = "unicode61 remove_diacritics 2"
);

-- Standard FTS5 table (no content= option) — sync via regular DELETE/INSERT,
-- NOT the special 'delete' INSERT command (which is only for external-content tables).

CREATE TRIGGER queries_ai AFTER INSERT ON queries BEGIN
    INSERT INTO queries_fts(rowid, title, description, body, objects)
    VALUES (new.id, new.title, new.description, new.body, '');
END;

CREATE TRIGGER queries_ad AFTER DELETE ON queries BEGIN
    DELETE FROM queries_fts WHERE rowid = old.id;
END;

CREATE TRIGGER queries_au AFTER UPDATE ON queries BEGIN
    DELETE FROM queries_fts WHERE rowid = old.id;
    INSERT INTO queries_fts(rowid, title, description, body, objects)
    VALUES (new.id, new.title, new.description, new.body, '');
END;
