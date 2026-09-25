# MTB Marathon Race Calendar

One website that shows **all** mountain bike marathon (XCM / open / citizen) races in a single spot — focused on **Germany, Austria, Switzerland, Italy, and France**.

> **Scope decision:** We deliberately exclude UCI World Championship / World Series premium events with fixed official coverage. The focus is on open/citizen races — the ones that are hard to find because they're scattered across dozens of organizer sites, federation lists, and social media.

## Goal

A "combined knowledge" page aggregating race data from:
1. **Official lists** (national federations, race series)
2. **Web scraping** (organizer sites, race portals)
3. **Community submissions** (races not in any official list, found via social media)

## Plan

See `docs/PLAN.md` for the full roadmap and `docs/DATA_SOURCES.md` for the research on where to get data.

## Repository layout (planned)

```
mtb-marathon-calendar/
├── docs/
│   ├── PLAN.md          # Roadmap & decisions
│   └── DATA_SOURCES.md  # Where to scrape/fetch data from
├── data/                # Normalized races dataset (JSON/CSV)
├── scraper/             # Python/Node scrapers per source
├── site/                # Static website (list, map, calendar views)
└── .github/workflows/   # Weekly scraper run (cron)
```
