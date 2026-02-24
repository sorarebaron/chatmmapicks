-- Migration: deduplicate analyst_picks and add unique constraint
-- Run this once against your Supabase project to clean up existing duplicate picks
-- and prevent future duplicates at the database level.
--
-- Strategy: keep the most recently created pick for each (fight_id, analyst_name)
-- pair (the newest record is most likely to have the corrected platform/data),
-- then delete the older duplicates along with their tags.

-- Step 1: Delete tags belonging to the older duplicate picks
DELETE FROM pick_tags
WHERE pick_id IN (
    SELECT pick_id FROM analyst_picks
    WHERE pick_id NOT IN (
        SELECT DISTINCT ON (fight_id, analyst_name) pick_id
        FROM analyst_picks
        ORDER BY fight_id, analyst_name, created_at DESC
    )
);

-- Step 2: Delete the older duplicate picks themselves
DELETE FROM analyst_picks
WHERE pick_id NOT IN (
    SELECT DISTINCT ON (fight_id, analyst_name) pick_id
    FROM analyst_picks
    ORDER BY fight_id, analyst_name, created_at DESC
);

-- Step 3: Add unique constraint so the database rejects duplicates going forward
ALTER TABLE analyst_picks
    ADD CONSTRAINT analyst_picks_fight_analyst_unique
    UNIQUE (fight_id, analyst_name);
