-- ============================================================================
-- Migration: Fix split picks caused by "Saint-Denis" → "Saint Denis" name change
--
-- Problem: The Dan Hooker vs Benoit Saint Denis fight at UFC 325 (2026-01-31)
-- was originally entered with fighter_b = "Benoit Saint-Denis" (with hyphen).
-- After the name was corrected to "Benoit Saint Denis" (no hyphen) a second
-- fights row was created, leaving 5 picks split across two fight records (2 + 3)
-- for the same bout.
--
-- Resolution:
--   keep → the fight row with "Benoit Saint Denis" (no hyphen) — Bout 2
--   drop → the fight row with "Benoit Saint-Denis" (with hyphen)
--   All picks on the drop row are re-pointed to the keep row, then the drop row
--   is deleted.  Any result attached to the drop row is migrated first.
-- ============================================================================

BEGIN;

-- Step 1: Identify the two fight_ids
CREATE TEMP TABLE _saint_denis_fix AS
SELECT
    keep_f.fight_id  AS keep_id,
    drop_f.fight_id  AS drop_id
FROM fights keep_f
JOIN fights drop_f
    ON  keep_f.event_id  = drop_f.event_id
    AND keep_f.fight_id <> drop_f.fight_id
JOIN events e ON e.event_id = keep_f.event_id
WHERE e.name ILIKE '%325%'
  -- keep row: no hyphen (the "Bout 2" record)
  AND (
        keep_f.fighter_a ILIKE 'Benoit Saint Denis'
     OR keep_f.fighter_b ILIKE 'Benoit Saint Denis'
  )
  -- drop row: hyphen variant
  AND (
        drop_f.fighter_a ILIKE 'Benoit Saint-Denis'
     OR drop_f.fighter_b ILIKE 'Benoit Saint-Denis'
  );

-- Sanity-check: must find exactly one pair before proceeding
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM _saint_denis_fix) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly 1 duplicate pair, found %. Aborting.', (SELECT COUNT(*) FROM _saint_denis_fix);
    END IF;
END $$;

-- Step 2: Migrate result from the drop fight to the keep fight (if one exists
--         on the drop row but not yet on the keep row).
UPDATE results
SET fight_id = m.keep_id
FROM _saint_denis_fix m
WHERE results.fight_id = m.drop_id
  AND NOT EXISTS (
      SELECT 1 FROM results r2 WHERE r2.fight_id = m.keep_id
  );

-- Step 3: Re-point picks from the hyphen fight to the no-hyphen fight.
--         Skip any pick where the analyst already has a pick on the kept fight
--         (the unique constraint would reject it).
UPDATE analyst_picks
SET fight_id = m.keep_id
FROM _saint_denis_fix m
WHERE analyst_picks.fight_id = m.drop_id
  AND NOT EXISTS (
      SELECT 1
      FROM analyst_picks ap2
      WHERE ap2.fight_id    = m.keep_id
        AND ap2.analyst_name = analyst_picks.analyst_name
  );

-- Step 4: Drop any picks that couldn't be re-pointed due to a constraint conflict
--         (true duplicates — analyst had picks on both rows).
DELETE FROM pick_tags
WHERE pick_id IN (
    SELECT ap.pick_id
    FROM analyst_picks ap
    JOIN _saint_denis_fix m ON ap.fight_id = m.drop_id
);

DELETE FROM analyst_picks
WHERE fight_id IN (SELECT drop_id FROM _saint_denis_fix);

-- Step 5: Delete the now-empty "Saint-Denis" fight record
DELETE FROM fights
WHERE fight_id IN (SELECT drop_id FROM _saint_denis_fix);

COMMIT;
