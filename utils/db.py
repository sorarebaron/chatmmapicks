import streamlit as st
from supabase import create_client, Client


@st.cache_resource
def get_supabase() -> Client:
    """Return a cached Supabase client using service_role credentials from st.secrets."""
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["service_role_key"]
    return create_client(url, key)


@st.cache_data(ttl=300)
def get_fighter_aliases() -> list[dict]:
    """Fetch all fighter aliases from the database (cached 5 min)."""
    db = get_supabase()
    resp = db.table("fighter_aliases").select("alias_id, canonical_name, alias").execute()
    return resp.data or []


def save_alias(canonical_name: str, alias: str) -> None:
    """Upsert a fighter alias and bust the cache."""
    db = get_supabase()
    db.table("fighter_aliases").upsert(
        {"canonical_name": canonical_name, "alias": alias},
        on_conflict="alias",
    ).execute()
    get_fighter_aliases.clear()


def get_or_create_event(
    name: str,
    date: str | None = None,
    location: str | None = None,
) -> str:
    """Return event_id for an existing event (case-insensitive) or create a new one.
    If the event already exists, fills in date/location if they were previously blank.
    """
    db = get_supabase()
    resp = (
        db.table("events")
        .select("event_id, date, location")
        .ilike("name", name)
        .limit(1)
        .execute()
    )
    if resp.data:
        event_id = resp.data[0]["event_id"]
        updates: dict = {}
        if date and not resp.data[0].get("date"):
            updates["date"] = date
        if location and not resp.data[0].get("location"):
            updates["location"] = location
        if updates:
            db.table("events").update(updates).eq("event_id", event_id).execute()
        return event_id

    insert_data: dict = {"name": name}
    if date:
        insert_data["date"] = date
    if location:
        insert_data["location"] = location
    resp = db.table("events").insert(insert_data).execute()
    return resp.data[0]["event_id"]


def _name_variants(name: str) -> list[str]:
    """Return [name] plus the word-reversed form for exactly 2-word names.

    Handles the common Asian name-order ambiguity where 'Wang Cong' and
    'Cong Wang' refer to the same fighter but appear differently across sources.
    """
    parts = name.strip().split()
    if len(parts) == 2:
        return [name, f"{parts[1]} {parts[0]}"]
    return [name]


def get_or_create_fight(
    event_id: str,
    fighter_a: str,
    fighter_b: str,
    weight_class: str | None = None,
) -> str:
    """Return fight_id for an existing fight (either order) or create a new one.
    If the fight already exists, fills in weight_class if it was previously blank.
    Handles 2-word name-order transpositions (e.g. 'Wang Cong' vs 'Cong Wang').
    """
    db = get_supabase()

    fa_variants = _name_variants(fighter_a)
    fb_variants = _name_variants(fighter_b)

    # Try all (fa_variant, fb_variant) combinations in both fight orderings,
    # deduplicating to avoid redundant DB round-trips.
    seen: set[tuple[str, str]] = set()
    for fav in fa_variants:
        for fbv in fb_variants:
            for fa_try, fb_try in [(fav, fbv), (fbv, fav)]:
                if (fa_try, fb_try) in seen:
                    continue
                seen.add((fa_try, fb_try))
                resp = (
                    db.table("fights")
                    .select("fight_id, weight_class")
                    .eq("event_id", event_id)
                    .eq("fighter_a", fa_try)
                    .eq("fighter_b", fb_try)
                    .execute()
                )
                if resp.data:
                    fight_id = resp.data[0]["fight_id"]
                    if weight_class and not resp.data[0].get("weight_class"):
                        db.table("fights").update({"weight_class": weight_class}).eq("fight_id", fight_id).execute()
                    return fight_id

    insert_data: dict = {"event_id": event_id, "fighter_a": fighter_a, "fighter_b": fighter_b}
    if weight_class:
        insert_data["weight_class"] = weight_class
    resp = db.table("fights").insert(insert_data).execute()
    return resp.data[0]["fight_id"]


def save_analyst_pick(pick_data: dict) -> str:
    """Upsert a row into analyst_picks; update in place if the same analyst+fight
    already exists (preventing duplicates on re-ingestion). Returns the pick_id."""
    db = get_supabase()
    resp = (
        db.table("analyst_picks")
        .select("pick_id")
        .eq("fight_id", pick_data["fight_id"])
        .eq("analyst_name", pick_data["analyst_name"])
        .limit(1)
        .execute()
    )
    if resp.data:
        pick_id = resp.data[0]["pick_id"]
        db.table("analyst_picks").update(pick_data).eq("pick_id", pick_id).execute()
        return pick_id
    resp = db.table("analyst_picks").insert(pick_data).execute()
    return resp.data[0]["pick_id"]


def save_pick_tags(pick_id: str, tags: list[str]) -> None:
    """Replace all tags for a pick (deletes existing tags first, then inserts new ones)."""
    db = get_supabase()
    db.table("pick_tags").delete().eq("pick_id", pick_id).execute()
    rows = [{"pick_id": pick_id, "tag": t.strip()} for t in tags if t.strip()]
    if rows:
        db.table("pick_tags").insert(rows).execute()


def get_events() -> list[dict]:
    """Return all events ordered by date descending."""
    db = get_supabase()
    resp = (
        db.table("events")
        .select("event_id, name, date, location")
        .order("date", desc=True)
        .execute()
    )
    return resp.data or []


def get_picks_for_event(event_id: str) -> list[dict]:
    """Return a flat list of all picks for an event, joined with fight and event data."""
    db = get_supabase()

    # Get all fights for this event
    fights_resp = (
        db.table("fights")
        .select("fight_id, fighter_a, fighter_b, weight_class, bout_order")
        .eq("event_id", event_id)
        .execute()
    )
    fights = {f["fight_id"]: f for f in (fights_resp.data or [])}

    if not fights:
        return []

    # Get event info
    event_resp = (
        db.table("events")
        .select("name, date, location")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    event = event_resp.data[0] if event_resp.data else {}

    # Get all picks for those fights
    fight_ids = list(fights.keys())
    picks_resp = (
        db.table("analyst_picks")
        .select("pick_id, fight_id, analyst_name, platform, source_url, picked_fighter, method_prediction, confidence_tag, reasoning_notes, created_at")
        .in_("fight_id", fight_ids)
        .execute()
    )
    picks = picks_resp.data or []

    # Get tags for all picks
    pick_ids = [p["pick_id"] for p in picks]
    tags_by_pick: dict[str, list[str]] = {}
    if pick_ids:
        tags_resp = (
            db.table("pick_tags")
            .select("pick_id, tag")
            .in_("pick_id", pick_ids)
            .execute()
        )
        for row in (tags_resp.data or []):
            tags_by_pick.setdefault(row["pick_id"], []).append(row["tag"])

    # Assemble flat rows
    rows = []
    for pick in picks:
        fight = fights.get(pick["fight_id"], {})
        tags = tags_by_pick.get(pick["pick_id"], [])
        context_parts = [pick.get("reasoning_notes") or ""]
        if tags:
            context_parts.append(", ".join(tags))
        context = " | ".join(p for p in context_parts if p)

        rows.append({
            "date": event.get("date") or "",
            "analyst": pick.get("analyst_name") or "",
            "platform": pick.get("platform") or pick.get("analyst_name") or "",
            "event": event.get("name") or "",
            "location": event.get("location") or "",
            "fight": f"{fight.get('fighter_a', '')} vs {fight.get('fighter_b', '')}",
            "weight_class": fight.get("weight_class") or "",
            "pick": pick.get("picked_fighter") or "",
            "context": context,
            # Extra columns available in the DB but not in the original CSV
            "method": pick.get("method_prediction") or "",
            "confidence": pick.get("confidence_tag") or "",
        })

    # Sort by fight bout_order if available, then analyst name
    rows.sort(key=lambda r: (
        fights.get(next((p["fight_id"] for p in picks if p["analyst_name"] == r["analyst"]), ""), {}).get("bout_order") or 999,
        r["analyst"],
    ))

    return rows


# ── QC Editor helpers ─────────────────────────────────────────────────────────

def get_fights_for_event(event_id: str) -> list[dict]:
    """Return fights for an event with pick_count, sorted by bout_order (nulls last)."""
    from collections import Counter
    db = get_supabase()
    fights = (
        db.table("fights")
        .select("*")
        .eq("event_id", event_id)
        .order("bout_order")
        .execute()
        .data or []
    )
    if not fights:
        return []
    fight_ids = [f["fight_id"] for f in fights]
    picks = (
        db.table("analyst_picks")
        .select("fight_id")
        .in_("fight_id", fight_ids)
        .execute()
        .data or []
    )
    counts = Counter(p["fight_id"] for p in picks)
    for f in fights:
        f["pick_count"] = counts.get(f["fight_id"], 0)
    # Stable sort: fights with bout_order=None go last
    fights.sort(key=lambda f: (f["bout_order"] is None, f["bout_order"] or 0))
    return fights


def get_picks_for_fight(fight_id: str) -> list[dict]:
    """Return raw picks with a 'tags' list for a fight, sorted by analyst_name."""
    from collections import defaultdict
    db = get_supabase()
    picks = (
        db.table("analyst_picks")
        .select("*")
        .eq("fight_id", fight_id)
        .order("analyst_name")
        .execute()
        .data or []
    )
    if not picks:
        return []
    pick_ids = [p["pick_id"] for p in picks]
    tags_rows = (
        db.table("pick_tags")
        .select("pick_id, tag")
        .in_("pick_id", pick_ids)
        .execute()
        .data or []
    )
    tag_map: dict[str, list[str]] = defaultdict(list)
    for t in tags_rows:
        tag_map[t["pick_id"]].append(t["tag"])
    for p in picks:
        p["tags"] = tag_map.get(p["pick_id"], [])
    return picks


def update_event(event_id: str, name: str, date: str | None, location: str | None) -> None:
    """Update event metadata fields."""
    get_supabase().table("events").update({
        "name": name,
        "date": date or None,
        "location": location or None,
    }).eq("event_id", event_id).execute()


def update_fight(
    fight_id: str,
    fighter_a: str,
    fighter_b: str,
    weight_class: str | None,
    bout_order: int | None,
) -> None:
    """Update fight metadata fields."""
    get_supabase().table("fights").update({
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "weight_class": weight_class or None,
        "bout_order": bout_order,
    }).eq("fight_id", fight_id).execute()


def update_pick(
    pick_id: str,
    analyst_name: str,
    platform: str | None,
    source_url: str | None,
    picked_fighter: str,
    method_prediction: str | None,
    confidence_tag: str | None,
    reasoning_notes: str | None,
) -> None:
    """Update an analyst pick by pick_id."""
    get_supabase().table("analyst_picks").update({
        "analyst_name": analyst_name,
        "platform": platform or None,
        "source_url": source_url or None,
        "picked_fighter": picked_fighter,
        "method_prediction": method_prediction or None,
        "confidence_tag": confidence_tag or None,
        "reasoning_notes": reasoning_notes or None,
    }).eq("pick_id", pick_id).execute()


def delete_pick(pick_id: str) -> None:
    """Delete an analyst pick (pick_tags cascade via FK)."""
    get_supabase().table("analyst_picks").delete().eq("pick_id", pick_id).execute()


def delete_fight(fight_id: str) -> None:
    """Delete a fight, its picks (cascade via FK), and any associated result row."""
    db = get_supabase()
    # Explicitly remove result row first (may not have FK cascade configured)
    db.table("results").delete().eq("fight_id", fight_id).execute()
    db.table("fights").delete().eq("fight_id", fight_id).execute()


def delete_alias(alias_id: str) -> None:
    """Delete a fighter alias and bust the cache."""
    get_supabase().table("fighter_aliases").delete().eq("alias_id", alias_id).execute()
    get_fighter_aliases.clear()


# ── Results helpers ────────────────────────────────────────────────────────────

def get_fights_with_results_for_event(event_id: str) -> list[dict]:
    """Return fights for an event with their result (if any) joined in.

    Only includes fights that have at least one analyst pick — fights with zero
    picks are data artifacts (e.g. cancelled bouts) and are excluded.
    Each item is a fight dict with an extra 'result' key (dict or None).
    Sorted by bout_order ascending (nulls last).
    """
    from collections import Counter
    db = get_supabase()
    fights = (
        db.table("fights")
        .select("*")
        .eq("event_id", event_id)
        .execute()
        .data or []
    )
    if not fights:
        return []

    fight_ids = [f["fight_id"] for f in fights]

    # Filter to fights that have at least one pick
    picks_rows = (
        db.table("analyst_picks")
        .select("fight_id")
        .in_("fight_id", fight_ids)
        .execute()
        .data or []
    )
    fights_with_picks = {r["fight_id"] for r in picks_rows}
    fights = [f for f in fights if f["fight_id"] in fights_with_picks]

    if not fights:
        return []

    fight_ids = [f["fight_id"] for f in fights]
    results_rows = (
        db.table("results")
        .select("*")
        .in_("fight_id", fight_ids)
        .execute()
        .data or []
    )
    results_by_fight = {r["fight_id"]: r for r in results_rows}

    for f in fights:
        f["result"] = results_by_fight.get(f["fight_id"])

    fights.sort(key=lambda f: (f["bout_order"] is None, f["bout_order"] or 0))
    return fights


def upsert_result(
    fight_id: str,
    winner: str | None,
    method: str | None,
    round_num: int | None,
    time: str | None,
    referee: str | None = None,
    judge1_name: str | None = None,
    judge1_score: str | None = None,
    judge1_winner: str | None = None,
    judge2_name: str | None = None,
    judge2_score: str | None = None,
    judge2_winner: str | None = None,
    judge3_name: str | None = None,
    judge3_score: str | None = None,
    judge3_winner: str | None = None,
) -> dict:
    """Insert or update a result row for a fight (upsert on fight_id)."""
    db = get_supabase()
    row: dict = {
        "fight_id": fight_id,
        "winner": winner or None,
        "method": method or None,
        "round": round_num,
        "time": time or None,
        "referee": referee or None,
        "judge1_name": judge1_name or None,
        "judge1_score": judge1_score or None,
        "judge1_winner": judge1_winner or None,
        "judge2_name": judge2_name or None,
        "judge2_score": judge2_score or None,
        "judge2_winner": judge2_winner or None,
        "judge3_name": judge3_name or None,
        "judge3_score": judge3_score or None,
        "judge3_winner": judge3_winner or None,
    }
    resp = db.table("results").upsert(row, on_conflict="fight_id").execute()
    return resp.data[0]


def delete_result(result_id: str) -> None:
    """Delete a result row by result_id."""
    get_supabase().table("results").delete().eq("result_id", result_id).execute()
