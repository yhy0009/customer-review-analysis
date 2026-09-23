-- Released v1 schema, retained independently of the current schema for migrations.
PRAGMA user_version = 1;
CREATE TABLE raw_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_review_id TEXT,
    product_name TEXT NOT NULL,
    review_date TEXT NOT NULL,
    rating TEXT NOT NULL,
    review_text TEXT NOT NULL,
    source_file TEXT,
    raw_payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'RAW'
        CHECK (status IN ('RAW', 'REJECTED', 'CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE clean_reviews (
    id INTEGER PRIMARY KEY REFERENCES raw_reviews(id) ON DELETE CASCADE,
    source_review_id TEXT,
    product_name TEXT NOT NULL,
    review_date TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text TEXT NOT NULL,
    cleaned_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CLEANED'
        CHECK (status IN ('CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE analysis_results (
    review_id INTEGER PRIMARY KEY REFERENCES clean_reviews(id) ON DELETE CASCADE,
    sentiment TEXT NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    summary TEXT,
    keywords TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_raw_reviews_status ON raw_reviews(status);
