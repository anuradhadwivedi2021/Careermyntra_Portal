-- migrations/add_college_predictor.sql
-- Run manually on VPS: psql -U postgres -d careermyntra_portal -f add_college_predictor.sql

CREATE TABLE IF NOT EXISTS cap_cutoff_data (
    id                SERIAL PRIMARY KEY,
    college_code      VARCHAR(30),
    college_name      VARCHAR(300) NOT NULL,
    branch_name       VARCHAR(200) NOT NULL,
    district          VARCHAR(100),
    university        VARCHAR(300),
    cap_year          VARCHAR(20)  NOT NULL,          -- e.g. "2024-25"
    cap_round         VARCHAR(50)  NOT NULL,           -- e.g. "CAP Round I"
    category          VARCHAR(50)  NOT NULL,           -- OPEN, OBC, SC, ST ...
    seat_type         VARCHAR(50)  DEFAULT 'AI',       -- AI / Home University / Other State
    exam_type         VARCHAR(50)  DEFAULT 'MHT-CET',  -- MHT-CET / JEE Main
    cutoff_percentile NUMERIC(7,4),                    -- e.g. 88.5432
    cutoff_score      NUMERIC(8,2),                    -- raw score if available
    fees              NUMERIC(10,2),                   -- annual fees in INR
    naac_grade        VARCHAR(10),                     -- A++, A+, A, B++ ...
    nba_accredited    VARCHAR(10)  DEFAULT 'No',       -- Yes / No
    placement_highest NUMERIC(12,2),                   -- in LPA
    placement_average NUMERIC(8,2),                    -- in LPA
    website           VARCHAR(300),
    address           TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Prevent exact duplicate records for same college+branch+year+round+category+seat_type
    CONSTRAINT cap_cutoff_unique
        UNIQUE (college_name, branch_name, cap_year, cap_round, category, seat_type)
);

-- Index for fast lookup by category + exam + year
CREATE INDEX IF NOT EXISTS idx_cap_cutoff_filter
    ON cap_cutoff_data (exam_type, category, cap_year);

-- Index for district filter
CREATE INDEX IF NOT EXISTS idx_cap_cutoff_district
    ON cap_cutoff_data (district);

-- Index for branch filter
CREATE INDEX IF NOT EXISTS idx_cap_cutoff_branch
    ON cap_cutoff_data (branch_name);