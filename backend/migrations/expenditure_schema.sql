-- ============================================================
-- Expenditure Module — PostgreSQL Schema
-- Database: careermyntra_db
-- Run as: careermyntra_user
-- ============================================================

-- ---------------------------------------------------
-- 1. Persons Master
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS expenditure_persons (
    person_id      SERIAL PRIMARY KEY,
    name           VARCHAR(150) NOT NULL,
    mobile_number  VARCHAR(15),
    status         VARCHAR(10) NOT NULL DEFAULT 'Active'
                   CHECK (status IN ('Active', 'Inactive')),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO expenditure_persons (name, status)
VALUES
    ('Pawan Khamgaonkar', 'Active'),
    ('Anil Khajinkar',    'Active'),
    ('Dhanraj Surner',    'Active')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------
-- 2. Expense Type Master
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS expenditure_expense_types (
    expense_type_id    SERIAL PRIMARY KEY,
    expense_type_name  VARCHAR(150) NOT NULL UNIQUE,
    created_at         TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO expenditure_expense_types (expense_type_name) VALUES
    ('Security Deposit'), ('Furniture'), ('Office Rent'), ('House Rent'),
    ('Office Electricity'), ('House Electricity'), ('Internet'),
    ('Office Supplies'), ('Tea & Snacks'), ('Cleaning'), ('Garbage'),
    ('Drinking Water'), ('Usable Water'), ('Travel'), ('Salary'),
    ('Food'), ('Miscellaneous')
ON CONFLICT (expense_type_name) DO NOTHING;

-- ---------------------------------------------------
-- 3. Expenses
--    Each recurring expense is exploded into one row PER PERIOD
--    at creation time; all rows from one recurring entry share
--    the same recurring_batch_id so they can be edited/deleted
--    as a group or individually.
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS expenditure_expenses (
    expense_id            SERIAL PRIMARY KEY,
    expense_name           VARCHAR(200) NOT NULL,
    expense_category       VARCHAR(20) NOT NULL
                            CHECK (expense_category IN ('Office', 'Individual')),
    expense_type_id        INTEGER REFERENCES expenditure_expense_types(expense_type_id),
    amount                  NUMERIC(12,2) NOT NULL CHECK (amount >= 0),
    paid_by_person_id      INTEGER NOT NULL REFERENCES expenditure_persons(person_id),
    paid_date               DATE NOT NULL,
    expense_month           DATE NOT NULL,          -- normalised to first day of month
    is_recurring             BOOLEAN NOT NULL DEFAULT FALSE,
    recurring_type           VARCHAR(20)
                              CHECK (recurring_type IN ('Monthly','Quarterly','Six Months','Yearly')),
    recurring_batch_id       UUID,                   -- groups all periods of one recurring entry
    recurring_start_month    DATE,
    recurring_end_month      DATE,
    is_split_expense         BOOLEAN NOT NULL DEFAULT FALSE,
    split_type                VARCHAR(20) CHECK (split_type IN ('Equal', 'Custom')),
    description               TEXT,
    attachment_path            VARCHAR(500),
    created_at                 TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_expenses_month ON expenditure_expenses(expense_month);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenditure_expenses(expense_category);
CREATE INDEX IF NOT EXISTS idx_expenses_paid_by ON expenditure_expenses(paid_by_person_id);
CREATE INDEX IF NOT EXISTS idx_expenses_batch ON expenditure_expenses(recurring_batch_id);
CREATE INDEX IF NOT EXISTS idx_expenses_type ON expenditure_expenses(expense_type_id);

-- ---------------------------------------------------
-- 4. Expense Splits (only when is_split_expense = TRUE)
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS expenditure_expense_splits (
    split_id       SERIAL PRIMARY KEY,
    expense_id     INTEGER NOT NULL REFERENCES expenditure_expenses(expense_id) ON DELETE CASCADE,
    person_id      INTEGER NOT NULL REFERENCES expenditure_persons(person_id),
    share_amount   NUMERIC(12,2) NOT NULL CHECK (share_amount >= 0),
    UNIQUE (expense_id, person_id)
);

CREATE INDEX IF NOT EXISTS idx_splits_expense ON expenditure_expense_splits(expense_id);
CREATE INDEX IF NOT EXISTS idx_splits_person ON expenditure_expense_splits(person_id);

-- ---------------------------------------------------
-- 5. Settlements
-- ---------------------------------------------------
CREATE TABLE IF NOT EXISTS expenditure_settlements (
    settlement_id     SERIAL PRIMARY KEY,
    from_person_id    INTEGER NOT NULL REFERENCES expenditure_persons(person_id),
    to_person_id      INTEGER NOT NULL REFERENCES expenditure_persons(person_id),
    amount             NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    payment_date        DATE NOT NULL,
    payment_mode         VARCHAR(20) NOT NULL
                          CHECK (payment_mode IN ('Cash', 'UPI', 'Bank Transfer', 'Card')),
    reference_number      VARCHAR(100),
    remarks                 TEXT,
    created_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (from_person_id <> to_person_id)
);

CREATE INDEX IF NOT EXISTS idx_settlements_from ON expenditure_settlements(from_person_id);
CREATE INDEX IF NOT EXISTS idx_settlements_to ON expenditure_settlements(to_person_id);
CREATE INDEX IF NOT EXISTS idx_settlements_date ON expenditure_settlements(payment_date);