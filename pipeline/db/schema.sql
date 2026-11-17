-- Mineral Processing Benchmarking database (SQLite)
-- Fixed tables for things that rarely change; `facts` holds every parameter so new
-- parameters never need a schema change.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,          -- slug, e.g. "artemis-blackwater"
    name              TEXT NOT NULL,
    country           TEXT,
    region            TEXT,                      -- province/state
    lat               REAL,
    lon               REAL,
    primary_commodity TEXT,                      -- Au, Cu, Ni ...
    by_products       TEXT,                      -- JSON array
    mine_type         TEXT,
    status            TEXT CHECK (status IN ('study','construction','operating','care_maintenance','closed') OR status IS NULL)
);

CREATE TABLE IF NOT EXISTS reports (
    id                    TEXT PRIMARY KEY,      -- folder name under reports/
    project_id            TEXT REFERENCES projects(id),
    title                 TEXT,
    issuer                TEXT,
    standard              TEXT CHECK (standard IN ('NI 43-101','S-K 1300','JORC','other') OR standard IS NULL),
    study_level           TEXT CHECK (study_level IN ('scoping','PEA','PFS','FS','operating','resource') OR study_level IS NULL),
    tier                  INTEGER DEFAULT 1,
    effective_date        TEXT,                  -- ISO date
    publish_date          TEXT,
    qps                   TEXT,                  -- JSON array of names
    source_url            TEXT,
    file_path             TEXT,                  -- relative to repo root
    sha256                TEXT UNIQUE,
    pages                 INTEGER,
    currency              TEXT,                  -- currency the study reports costs in
    cost_base_year        INTEGER,
    supersedes_report_id  TEXT REFERENCES reports(id),
    parse_status          TEXT DEFAULT 'pending',
    added_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flowsheets (
    report_id           TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
    unit_ops            TEXT NOT NULL,           -- JSON array of keys from flowsheet_taxonomy.yaml
    families            TEXT,                    -- JSON array, DERIVED
    comminution_circuit TEXT,                    -- DERIVED
    description_quote   TEXT,
    page                INTEGER
);

CREATE TABLE IF NOT EXISTS facts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id         TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    parameter_key     TEXT NOT NULL,             -- key from parameters.yaml
    metal             TEXT,                      -- for per-metal parameters
    value             REAL,
    value_text        TEXT,                      -- for enum/text parameters
    unit              TEXT,                      -- as reported
    value_norm        REAL,
    unit_norm         TEXT,
    statistic         TEXT,
    basis             TEXT CHECK (basis IN ('estimate','actual')) DEFAULT 'estimate',
    context           TEXT,                      -- JSON object
    page              INTEGER,
    quote             TEXT,
    quote_verified    INTEGER DEFAULT 0,         -- 1 = quote found on that page
    confidence        REAL,
    extractor         TEXT,                      -- e.g. "claude-code" / "api:<model>"
    extractor_version TEXT,
    review_status     TEXT CHECK (review_status IN ('auto_accepted','needs_review','reviewed','rejected')) DEFAULT 'needs_review',
    review_note       TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_facts_param  ON facts(parameter_key);
CREATE INDEX IF NOT EXISTS idx_facts_report ON facts(report_id);

CREATE TABLE IF NOT EXISTS cost_items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id            TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    cost_type            TEXT NOT NULL CHECK (cost_type IN ('initial_capex','sustaining','closure','opex')),
    category_l1          TEXT NOT NULL,          -- key from cost_taxonomy.yaml
    category_l2          TEXT,
    raw_label            TEXT NOT NULL,          -- exactly as in the report
    value                REAL NOT NULL,
    currency             TEXT NOT NULL,
    value_usd            REAL,
    value_usd_escalated  REAL,
    escalation_year      INTEGER,
    basis_unit           TEXT NOT NULL,          -- total | per_t_milled | ...
    base_year            INTEGER,
    is_subtotal          INTEGER DEFAULT 0,      -- 1 = a reported subtotal/total line (not summed)
    includes_indirects   INTEGER,                -- for process plant lines: NULL unknown, 0 direct only, 1 includes allocated indirects/contingency
    basis                TEXT CHECK (basis IN ('estimate','actual')) DEFAULT 'estimate',
    page                 INTEGER,
    quote                TEXT,
    quote_verified       INTEGER DEFAULT 0,
    mapping_method       TEXT,                   -- synonym | llm | manual
    mapping_confidence   REAL,
    extractor            TEXT,
    extractor_version    TEXT,
    review_status        TEXT CHECK (review_status IN ('auto_accepted','needs_review','reviewed','rejected')) DEFAULT 'needs_review',
    review_note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cost_report ON cost_items(report_id);
CREATE INDEX IF NOT EXISTS idx_cost_cat    ON cost_items(cost_type, category_l1, category_l2);

-- Discovery output: things we know about but haven't downloaded/processed
CREATE TABLE IF NOT EXISTS candidates (
    url            TEXT PRIMARY KEY,
    source         TEXT,                         -- edgar | company_site | search | inbox
    guessed_issuer TEXT,
    guessed_title  TEXT,
    found_at       TEXT DEFAULT (datetime('now')),
    status         TEXT DEFAULT 'new',           -- new | downloaded | rejected | failed | wanted
    note           TEXT
);

-- Full-text search over every parsed page
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(report_id UNINDEXED, page UNINDEXED, text);

-- Escalation and FX series (fetched from BLS / Bank of Canada)
CREATE TABLE IF NOT EXISTS index_series (
    series  TEXT NOT NULL,                       -- e.g. "BLS:WPU1191" or "FX:USDCAD"
    year    INTEGER NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (series, year)
);
