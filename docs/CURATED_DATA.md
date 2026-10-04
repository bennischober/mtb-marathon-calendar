# Curated Data

Human-written corrections to the generated calendar, merged on top of
`data/generated/events.json` at publish time.

## Why this is separate

Collection re-derives every field on every run. If a review decision lived in
the same record as machine output, the next ingestion run would quietly
overwrite it. So the two never share storage:

| Location | Owner | Lifetime |
| --- | --- | --- |
| `data/generated/events.json` | pipeline | disposable, rewritten every export |
| `data/curated/*.json` | humans | authoritative, committed to Git |

`mtb-pipeline export` applies these files last, on every export. You never have
to remember to re-apply them.

## What export does before it gets here

Each `export` runs three stages in a fixed order:

1. **Dump** what the database currently holds.
2. **Merge duplicates** across sources. Two records for the same race are folded
   together, choosing values by source priority and then by retrieval time.
   Every disagreement is reported on stdout, for example
   `conflict: {...}` with the kept value and the discarded one, and
   `merged: <id>: merged <id>` with the source that was folded in. All source
   references from both records survive.
   When exporting from a database, the same disagreements are also stored in the
   `merge_conflicts` table and can be listed later with
   `mtb-pipeline conflicts [--json] [--edition <id>]`. That table describes the
   most recent export: a field that stopped disagreeing stops being listed,
   rather than lingering as a stale accusation. Fixture exports (`--input`) have
   no database to write to and only print to stdout.
   Merging is deliberately conservative: it needs a distinctive name token plus
   a compatible date and the same country, so records with dates more than three
   days apart are kept apart as a possible reschedule rather than guessed at.
3. **Apply these overlays**, so a human decision always has the last word.

If you want to see the machine data without the overlays, for example to diff
them:

```sh
uv run mtb-pipeline export --no-curated --output /tmp/machine-only.json
```

## File format

One JSON object, or an array of them. Every entry needs an `edition_id`, which
is the `id` of an edition in the generated artifact.

```json
[
  {
    "edition_id": "radsport-events-salzkammergut-trophy-at-2027",
    "reason": "Verified against the organizer's own page",
    "location": { "region": "Upper Austria", "lat": 47.82 },
    "course_variants": [{ "id": "...-210km", "elevation_m": 7100 }],
    "review": {
      "status": "approved",
      "reason": "Organizer confirms a 210 km course",
      "reviewed_by": "your-name",
      "reviewed_at": "2026-10-02T12:00:00+00:00"
    },
    "editorial": {
      "summary": "One-day ultra with a brutal Salzkammergut profile."
    }
  }
]
```

`fields` are corrections written flat at the top level, so a minimal entry looks
like this:

```json
{ "edition_id": "some-edition-2027", "name": "Corrected Race Name" }
```

Files are applied in filename order, so `a-general.json` then `b-review.json`
lets concerns be split apart. Later files win.

## What an overlay may and may not do

**May correct:** `name`, `date_start`, `date_end`, `duration_days`, `format`,
`classification`, `classification_confidence`, `status`, `official_url`,
`registration_url`, the location fields, and individual course variants.

**May not change:** `id`, `event_id`, `edition`, `source_refs`, and any other
provenance field. Rewriting those would break the link back to the evidence that
produced the record, and `export` refuses rather than quietly dropping them.

**May not add or remove events.** An overlay for an `edition_id` that collection
never produced is reported as skipped and nothing is added. A record automation
never found has no evidence to attach an opinion to, so a typo cannot invent a
race.

**May not add or remove course variants.** Variant sets are evidence, not
opinion. Corrections must match an existing variant by `id` or `name`; a
mismatch is an error.

## Review statuses

| Status | Meaning |
| --- | --- |
| `unreviewed` | Nobody has looked at it. The default. |
| `approved` | Accepted into our calendar after human review. **Not** official race approval. |
| `rejected` | Real event, but out of scope. |
| `false_positive` | Wrongly detected as a race. The original record is kept. |

If `reviewed_at` is omitted it defaults to the export timestamp.

## Stale overlays

If an `edition_id` stops matching — usually because an upstream record changed
— `export` prints `skipped overlay for unknown edition '...'` instead of
failing. Fix the id or delete the entry.
## Series-scoped overlays

An overlay may address a whole **race series** instead of one edition by using
`event_id` instead of `edition_id`:

```json
{
  "event_id": "radsport-events-bike-festival-riva-del-garda-it",
  "editorial": { "summary": "Festival atmosphere; course changes every year." }
}
```

At publish time a series overlay applies to the series' **latest** edition (the
one with the newest `date_start`), and an editorial profile rides with the
series: human experience is about the race, not one year of it. The publish log
names the edition the overlay landed on, so it stays auditable.

A series overlay can still carry normal corrections (`name`, `location`, …);
they land on the latest edition like an edition overlay would. When both an
edition overlay and a series overlay match the same edition, the edition overlay
wins (more specific).

## What aggregation may and may not do to human data

This is the pipeline's data-treatment contract; enforced in the repository
layer, tested, and documented in `docs/PLAN.md`:

- A **review decision** (`approved`/`rejected`/`false_positive`) survives every
  re-ingest and re-collect. Aggregated records always carry the machine default
  (`unreviewed`), and the upsert keeps the human value when one exists.
- An **editorial profile** survives the same way.
- **Machine facts** (dates, prices, courses, status) are updated by aggregation
  — but only when the source page actually changed since the last read
  (content-hash detection). An unchanged page costs nothing and changes nothing.
- **Descriptions** from the organizer are replaced only when their page changed;
  never on a timer, and never over a human correction (the curated overlay is
  applied after enrichment at publish time and always wins).
