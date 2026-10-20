# Source health baseline

Recorded from manual collection runs on 2026-10-20 (all times local,
user agent `mtb-marathon-calendar-hardening-check/1.0`). Purpose: a
measured starting point before enabling unattended collection. Update
this table whenever a source's behavior changes materially.

## Snapshot

| Source | State | Kept | Fail% | Notes |
| --- | --- | --- | --- | --- |
| `radsport-events` | ok | 47 | 0 | JSON API adapter. API host returns 403 to robots.txt probes; treated as allow (public paths only). cadence 24 h. |
| `bawue-radsportverband` | ok | 26 | 0 | Season PDF parse. robots confirmed (all paths except `/typo3/`). Only source with confirmed terms. |
| `epic-series` | ok | 1 | 0 | HTML adapter, slow season (1 race in window). |
| `ffc` | **stale/failing** | 0 | 50 | Generic LLM extraction fails: `LLM request failed after retries: 400 Bad Request` against the configured endpoint (`api.prod.orthos.uni-ulm.de/v1`). Kept 0 editions this run. Needs either an endpoint that accepts the current prompt size or a fallback parse. |

Pipeline-wide: no LLM key in this environment, so all 74 kept editions
are `mtb_candidate` (DEGRADED state by design, nothing dropped).

## Observations for hardening

1. **ffc is the weak link.** The 400 suggests the request itself is
   malformed for this endpoint (prompt too large? unsupported
   parameter?), not transient flakiness. Reproduce with a single
   candidate before blaming the network. Also the only source without
   a hand-written adapter, so its failure mode is qualitatively
   different: no records at all instead of wrong records.
2. **Robots handling confirmed working** on three real hosts,
   including the 403-probe case on the radsport-events API host.
3. **terms=unconfirmed on three of four sources** — before unattended
   runs, each source's terms of service should be checked and recorded
   here (the `bawue` adapter already models this; others default to
   unconfirmed).
4. The workflow's sanity gate (fails when any source yields nothing)
   would currently fail every run because of ffc — see item 1. Either
   fix ffc or the gate will (correctly) block publishing.
