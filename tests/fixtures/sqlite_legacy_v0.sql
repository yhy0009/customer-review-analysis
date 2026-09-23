-- Historical storage.py schema before implementation unification.
-- Test fixture only; never use to initialize a new application database.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_review_id TEXT,
    product_name TEXT,
    review_date TEXT,
    rating TEXT,
    review_text TEXT,
    source_file TEXT,
    raw_payload TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'RAW',
    analysis_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clean_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_review_id TEXT,
    product_name TEXT NOT NULL,
    review_date TEXT NOT NULL,
    rating INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    cleaned_at TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    raw_id INTEGER,
    FOREIGN KEY (raw_id) REFERENCES raw_reviews(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS analysis_results (
    review_id INTEGER PRIMARY KEY,
    sentiment TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    analyzed_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    FOREIGN KEY (review_id) REFERENCES clean_reviews(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_raw_status
ON raw_reviews(status);

CREATE INDEX IF NOT EXISTS idx_raw_source_review_id
ON raw_reviews(source_review_id);

CREATE INDEX IF NOT EXISTS idx_clean_product_name
ON clean_reviews(product_name);

CREATE INDEX IF NOT EXISTS idx_clean_review_date
ON clean_reviews(review_date);

CREATE INDEX IF NOT EXISTS idx_clean_rating
ON clean_reviews(rating);

CREATE INDEX IF NOT EXISTS idx_analysis_sentiment
ON analysis_results(sentiment);
