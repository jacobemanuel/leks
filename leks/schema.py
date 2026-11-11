"""Database layouts shared by the builder and the readers, plus the embedding input hash."""
import hashlib
import os
import sqlite3
from urllib.parse import quote

SCHEMA_VERSION = '3'

# master: every root, keyed by (root name, relative path). only build.update writes it
MASTER_DDL = '''
CREATE TABLE IF NOT EXISTS files(root TEXT, rel TEXT, ext TEXT, kind TEXT, title TEXT, summary TEXT,
  warning TEXT, status TEXT, status_src TEXT, date TEXT, mtime REAL, size INT, sha256 TEXT,
  units INT, unit TEXT, extractor TEXT, indexed_at REAL, PRIMARY KEY(root, rel));
CREATE INDEX IF NOT EXISTS i_files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS chunks(root TEXT, rel TEXT, ord INT, start_line INT, end_line INT,
  heading TEXT, text TEXT, ehash TEXT, PRIMARY KEY(root, rel, ord));
CREATE INDEX IF NOT EXISTS i_chunks_e ON chunks(ehash);
CREATE TABLE IF NOT EXISTS symbols(root TEXT, rel TEXT, name TEXT, kind TEXT, lang TEXT, line INT,
  end_line INT, parent TEXT, signature TEXT);
CREATE INDEX IF NOT EXISTS i_sym_file ON symbols(root, rel);
CREATE TABLE IF NOT EXISTS names(root TEXT, rel TEXT, size INT, mtime REAL, class TEXT,
  PRIMARY KEY(root, rel));
CREATE TABLE IF NOT EXISTS skipped(root TEXT, rel TEXT, why TEXT, size INT, PRIMARY KEY(root, rel));
CREATE TABLE IF NOT EXISTS extract_cache(sha256 TEXT PRIMARY KEY, extractor TEXT, version INT,
  unit TEXT, title TEXT, units TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
'''

# profile database, core part. the code lane and the locate lane add their own tables
PROFILE_DDL = '''
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, root TEXT, rel TEXT, name TEXT,
  ext TEXT, title TEXT, summary TEXT, warning TEXT, kind TEXT, status TEXT, status_src TEXT, date TEXT,
  mtime REAL, size INT, sha256 TEXT, units INT, unit TEXT, copies TEXT);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, doc INT, ord INT, start_line INT, end_line INT,
  heading TEXT, text TEXT, vec INT, refs INT);
CREATE INDEX IF NOT EXISTS i_chunks_doc ON chunks(doc);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, heading, path, text, idents,
  tokenize="porter unicode61 remove_diacritics 2", prefix='2 3');
CREATE VIRTUAL TABLE IF NOT EXISTS dfts USING fts5(title, heading, path, text,
  tokenize="porter unicode61 remove_diacritics 2");
CREATE VIRTUAL TABLE IF NOT EXISTS docfts USING fts5(title, summary, path, headings,
  tokenize="porter unicode61 remove_diacritics 2");
CREATE TABLE IF NOT EXISTS newer(doc INTEGER PRIMARY KEY, target INT, how TEXT);
CREATE TABLE IF NOT EXISTS vecmap(vec INTEGER PRIMARY KEY, ehash TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
'''

# what the embedder sees for one passage, and the key its vector is stored under
EMBED_CUT = 1200


def embed_input(title, heading, text):
    return ((title or '') + '|' + (heading or '') + '\n' + (text or ''))[:EMBED_CUT]


def ehash(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:32]


def open_ro(path, immutable=False):
    """Read-only connection. immutable=True is for a file nobody writes while it is open."""
    uri = 'file:' + quote(os.path.abspath(path)) + ('?immutable=1' if immutable else '?mode=ro')
    c = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
    c.execute('PRAGMA temp_store=MEMORY')
    c.execute('PRAGMA trusted_schema=OFF')
    return c


def open_rw(path):
    """Master connection: makes the folder, turns on WAL, applies the tables."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(path, timeout=120)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA temp_store=MEMORY')
    c.executescript(MASTER_DDL)
    c.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
    c.commit()
    return c
