-- ============================================================================
-- Migration: Fix split picks caused by "Michael" → "Michal" spelling correction
--
-- Problem: Bout #4 at UFC Vegas 113 (Michal Oleksiejczuk vs Marc-Andre Barriault)
-- was originally entered with fighter_a = "Michael Oleksiejczuk".  After the name
-- was corrected to "Michal Oleksiejczuk" a second fights row was created, leaving
-- 4 picks split across two fight records (3 + 1) for the same bout.
--
-- Resolution:
--   keep → the fight row containing the CORRECT spelling "Michal Oleksiejczuk"
--   drop → the fight row containing the typo  "Michael Oleksiejczuk"
--   All picks on the drop row are re-pointed to the keep row, then the drop row
--   is deleted.
-- ============================================================================

BEGIN;

-- Step 1: Identify the two fight_ids
CREATE TEMP TABLE _michal_fix AS
SELECT
    keep_f.fight_id  AS keep_id,
    drop_f.fight_id  AS drop_id
FROM fights keep_f
JOIN fights drop_f
    ON  keep_f.event_id   = drop_f.event_id
    AND keep_f.bout_order = drop_f.bout_order
    AND keep_f.fight_id  <> drop_f.fight_id
JOIN events e ON e.event_id = keep_f.event_id
WHERE e.name ILIKE '%Vegas 113%'
  AND keep_f.bout_order = 4
  AND keep_f.fighter_a ILIKE 'Michal Oleksiejczuk'   -- correct spelling (keep)
  AND drop_f.fighter_a ILIKE 'Michael Oleksiejczuk'; -- typo spelling  (drop)

-- Sanity-check: must find exactly one pair before proceeding
DO $$
BEGIN
    IF (SELECT COUNT(*) FROM _michal_fix) <> 1 THEN
        RAISE EXCEPTION 'Expected exactly 1 duplicate pair, found %. Aborting.', (SELECT COUNT(*) FROM _michal_fix);
    END IF;
END $$;

-- Step 2: Re-point picks from the "Michael" fight to the "Michal" fight.
-- Skip any pick where the analyst already has a pick on the kept fight
-- (the unique constraint would reject it).
UPDATE analyst_picks
SET fight_id = m.keep_id
FROM _michal_fix m
WHERE analyst_picks.fight_id = m.drop_id
  AND NOT EXISTS (
      SELECT 1
      FROM analyst_picks ap2
      WHERE ap2.fight_id    = m.keep_id
        AND ap2.analyst_name = analyst_picks.analyst_name
  );

-- Step 3: Drop any picks that couldn't be re-pointed due to a constraint conflict
--         (true duplicates — analyst had picks on both rows).
DELETE FROM pick_tags
WHERE pick_id IN (
    SELECT ap.pick_id
    FROM analyst_picks ap
    JOIN _michal_fix m ON ap.fight_id = m.drop_id
);

DELETE FROM analyst_picks
WHERE fight_id IN (SELECT drop_id FROM _michal_fix);

-- Step 4: Delete the now-empty "Michael" fight record
DELETE FROM fights
WHERE fight_id IN (SELECT drop_id FROM _michal_fix);

COMMIT;
