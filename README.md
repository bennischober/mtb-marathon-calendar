# MTB Marathon Race Calendar

An automated-first calendar that aggregates publicly available information about mountain bike marathon races in **Germany, Austria, Switzerland, Italy, and France** — including one-day ultra marathons and multi-day stage/epic races.

> **Scope decision:** This is a discovery and comparison layer, not a replacement for official race pages or registration systems. We exclude UCI World Championship / World Series premium events from the default view and focus on open/citizen races that are scattered across organizer sites, federation lists, event platforms, and the wider web.

## Goal

A continuously refreshed, provenance-aware dataset built primarily through automated ingestion:
1. **Official feeds and calendars** (organizers, federations, series)
2. **Public event platforms, sitemaps, and web scraping**
3. **Automated web discovery** through approved search APIs and public feeds
4. **Submissions and exception review** only as a fallback for gaps automation cannot reach

The website links out to the original event and registration pages; it does not handle registration itself.

The core calendar will prioritize automated event/date/location data. Later iterations can add course variants, previous editions, route/area details, editorial race profiles, community information, and human review/false-positive annotations.

## Plan

See `docs/PLAN.md` for the full roadmap and `docs/DATA_SOURCES.md` for the research on where to get data.

## Repository layout (planned)

```
mtb-marathon-calendar/
├── docs/
│   ├── PLAN.md          # Roadmap & decisions
│   └── DATA_SOURCES.md  # Where to scrape/fetch data from
├── data/                # Generated and curated event data (JSON)
├── ingestion/           # Source registry, fetchers, parsers, normalizers
├── site/                # Static website (list, map, calendar views)
└── .github/workflows/   # Scheduled ingestion and deployment
```
