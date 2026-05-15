-- Migration: add win and ITD odds columns to fights table
-- Run in Supabase SQL Editor: Project → SQL Editor → New query

alter table fights
  add column if not exists fighter_a_win_odds integer,
  add column if not exists fighter_b_win_odds integer,
  add column if not exists fighter_a_itd_odds integer,
  add column if not exists fighter_b_itd_odds integer;
