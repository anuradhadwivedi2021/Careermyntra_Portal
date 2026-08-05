-- ============================================================
-- Migration: Auto-parse messy Fees values (Lakh/Crore/range
-- notation) into a clean numeric column — pure DB-side fix.
-- No changes needed to the Python upload script.
--
-- Handles formats seen in DTE CAP cutoff Excel files:
--   "1.93 L"          -> 193000
--   "₹3.82 L"         -> 382000
--   "1.2 Cr"          -> 12000000
--   "75.88 K - 1 L"   -> 87940   (range -> midpoint average)
--   "27,211"          -> 27211
--   "" / NULL / "NA"  -> NULL
-- ============================================================

-- ---------------------------------------------------
-- 1. Helper: parse a single amount token (no range/dash)
-- ---------------------------------------------------
CREATE OR REPLACE FUNCTION parse_single_fee_amount(raw TEXT)
RETURNS NUMERIC AS $$
DECLARE
    m TEXT[];
    num NUMERIC;
    suffix TEXT;
BEGIN
    IF raw IS NULL THEN RETURN NULL; END IF;

    m := regexp_match(btrim(raw), '^([0-9.]+)\s*(K|L|LAKH|LAKHS|CR|CRORE|CRORES)?$', 'i');
    IF m IS NULL THEN
        RETURN NULL;
    END IF;

    BEGIN
        num := m[1]::NUMERIC;
    EXCEPTION WHEN OTHERS THEN
        RETURN NULL;
    END;

    suffix := upper(coalesce(m[2], ''));
    IF suffix LIKE 'K%' THEN
        num := num * 1000;
    ELSIF suffix LIKE 'L%' THEN
        num := num * 100000;
    ELSIF suffix LIKE 'CR%' THEN
        num := num * 10000000;
    END IF;

    RETURN num;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------
-- 2. Main parser: handles ranges, currency symbols, commas
-- ---------------------------------------------------
CREATE OR REPLACE FUNCTION parse_fee_amount(raw TEXT)
RETURNS NUMERIC AS $$
DECLARE
    cleaned TEXT;
    parts TEXT[];
    low NUMERIC;
    high NUMERIC;
BEGIN
    IF raw IS NULL THEN RETURN NULL; END IF;

    cleaned := btrim(replace(replace(raw, '₹', ''), ',', ''));

    IF cleaned = '' OR upper(cleaned) IN ('NA', 'N/A', '-', '--', 'NIL') THEN
        RETURN NULL;
    END IF;

    -- Range format e.g. "75.88 K - 1 L" -> average of both bounds
    IF position('-' IN cleaned) > 0 THEN
        parts := regexp_split_to_array(cleaned, '\s*-\s*');
        IF array_length(parts, 1) = 2 THEN
            low := parse_single_fee_amount(parts[1]);
            high := parse_single_fee_amount(parts[2]);
            IF low IS NOT NULL AND high IS NOT NULL THEN
                RETURN round((low + high) / 2, 2);
            END IF;
        END IF;
        RETURN NULL;
    END IF;

    RETURN round(parse_single_fee_amount(cleaned), 2);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------
-- 3. Table migration
--    IMPORTANT: replace predictor_data_be_btech / fees below
--    with your actual table + column name if different.
--    Run \d predictor_data_be_btech in psql first to confirm.
-- ---------------------------------------------------

-- 3a. Widen the fees column so text values are never rejected
ALTER TABLE predictor_data_be_btech
    ALTER COLUMN fees TYPE TEXT;

-- 3b. Add a clean numeric column alongside the raw text
ALTER TABLE predictor_data_be_btech
    ADD COLUMN IF NOT EXISTS fees_amount NUMERIC;

-- 3c. Backfill existing rows
UPDATE predictor_data_be_btech
SET fees_amount = parse_fee_amount(fees)
WHERE fees_amount IS NULL AND fees IS NOT NULL;

-- ---------------------------------------------------
-- 4. Trigger: auto-populate fees_amount on every insert/update
-- ---------------------------------------------------
CREATE OR REPLACE FUNCTION trg_parse_fees_fn()
RETURNS TRIGGER AS $$
BEGIN
    NEW.fees_amount := parse_fee_amount(NEW.fees);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_predictor_fees_parse ON predictor_data_be_btech;

CREATE TRIGGER trg_predictor_fees_parse
    BEFORE INSERT OR UPDATE ON predictor_data_be_btech
    FOR EACH ROW
    EXECUTE FUNCTION trg_parse_fees_fn();