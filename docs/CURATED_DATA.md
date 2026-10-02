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

## File format

One JSON object, or an array of them. Every entry needs an `edition_id`, which
is the `id` of an edition in the generated artifact.

```json
[
  {
    "edition_id": "radsport-events-salzkammergut-trophy-at-2027",
    "reason": "Verified against the organizer's own page",
    "fields": {},
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

To inspect what would be published without curated data, for example to diff the
machine artifact:

```sh
uv run mtb-pipeline export --no-curated --output /tmp/machine-only.json
```