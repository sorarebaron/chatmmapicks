-- Migration: add DraftKings salary columns to fights table
-- Run in Supabase SQL Editor: Project → SQL Editor → New query

alter table fights
  add column if not exists fighter_a_salary integer,
  add column if not exists fighter_b_salary integer;
