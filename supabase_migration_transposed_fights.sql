-- ============================================================================
-- Migration: Merge duplicate fight records caused by name-order transpositions
--
-- Problem: Some sources write Asian names in "Given Surname" order (e.g. "Cong Wang")
-- while others use "Surname Given" order (e.g. "Wang Cong"), causing two separate
-- fight records to be created for the same bout.
--
-- Detection: Two fights for the same event are considered duplicates when their
-- combined fighter-name word sets are identical (order-independent).
-- e.g. "Eduarda Moura vs Wang Cong"  tokens → cong eduarda moura wang
--      "Cong Wang vs Eduarda Moura"  tokens → cong eduarda moura wang  ← same
--
-- Resolution: Keep the fight with the lexicographically smallest fight_id
-- (arbitrary but deterministic); re-point all picks to it, then delete the rest.
-- ============================================================================

BEGIN;

-- Step 1: Build a temp table of (keep_id, drop_id) pairs
CREATE TEMP TABLE _transposed_dupe_fights AS
WITH fight_tokens AS (
    SELECT
        fight_id,
        event_id,
        -- Tokenise: lower all words from both fighter names, sort, rejoin
        ARRAY_TO_STRING(
            ARRAY(
                SELECT w
                FROM UNNEST(
                    STRING_TO_ARRAY(
                        LOWER(TRIM(fighter_a) || ' ' || TRIM(fighter_b)), ' '
                    )
                ) AS w
                ORDER BY w
            ),
            ' '
        ) AS token_key
    FROM fights
),
grouped AS (
    SELECT
        event_id,
        token_key,
        ARRAY_AGG(fight_id ORDER BY fight_id ASC) AS fight_ids
    FROM fight_tokens
    GROUP BY event_id, token_key
    HAVING COUNT(*) > 1
)
SELECT
    fight_ids[1]            AS keep_id,
    UNNEST(fight_ids[2:])   AS drop_id
FROM grouped;

-- Step 2: Re-point picks from duplicate fights to the canonical fight.
-- Skip picks where the same analyst already has a pick on the kept fight
-- (the unique constraint would reject them anyway).
UPDATE analyst_picks
SET fight_id = d.keep_id
FROM _transposed_dupe_fights d
WHERE analyst_picks.fight_id = d.drop_id
  AND NOT EXISTS (
      SELECT 1
      FROM analyst_picks ap2
      WHERE ap2.fight_id = d.keep_id
        AND ap2.analyst_name = analyst_picks.analyst_name
  );

-- Step 3: Remove any picks still attached to a duplicate fight (true constraint
-- conflicts where the analyst already has a pick on the kept fight — rare).
DELETE FROM pick_tags
WHERE pick_id IN (
    SELECT ap.pick_id
    FROM analyst_picks ap
    JOIN _transposed_dupe_fights d ON ap.fight_id = d.drop_id
);

DELETE FROM analyst_picks
WHERE fight_id IN (SELECT drop_id FROM _transposed_dupe_fights);

-- Step 4: Delete the now-empty duplicate fight records
DELETE FROM fights
WHERE fight_id IN (SELECT drop_id FROM _transposed_dupe_fights);

COMMIT;
