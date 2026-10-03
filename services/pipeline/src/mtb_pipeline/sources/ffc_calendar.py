"""FFC (Fédération Française de Cyclisme) calendar via generic LLM extraction.

Verified 2026-10-03 (docs/DATA_SOURCES.md): the competition calendar at
``competitions.ffc.fr`` is public and server-rendered. Discipline/group
filters run through ASP.NET postbacks that require the page's
``__VIEWSTATE``, ``__VIEWSTATEGENERATOR`` and ``__EVENTVALIDATION`` fields;
with those carried through, a filtered view returns exactly the wanted rows.

Yield reality: the marathon-adjacent French events sit in the *Randonnée*
group (VTT rows incl. Roc d'Azur XCM) — roughly 30-40 events/year, not the
hundreds the 972-event surface suggests. The VTT-XC discipline filter is
licensed cross-country racing and is deliberately NOT fetched.

This adapter contains no parsing code: ``pages()`` navigates, the generic
LLM extraction reads the pages.
"""

from __future__ import annotations

import re
import time
import urllib.parse

from ..http_client import FetchedDocument, Fetcher
from .base import CollectOptions, SourceProfile
from .generic import GenericSourceAdapter

CALENDAR_URL = "https://competitions.ffc.fr/calendrier/calendrier.aspx"

# Filters verified against the live calendar.
GROUP_RANDONNEE = "10"  # "Randonnée" — the citizen/gran-fondo group
DISCIPLINE_VTT_XC = "9"  # "VTT - Cross country" (the row discipline for VTT randos)

_ROW_LINK = re.compile(r'<a[^>]+href="(/calendrier/competition/[^"]+)"', re.I)
_VIEWSTATE = re.compile(r'id="__VIEWSTATE"[^>]*value="([^"]*)"', re.S)
_GENERATOR = re.compile(r'name="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', re.S)
_VALIDATION = re.compile(r'id="__EVENTVALIDATION"[^>]*value="([^"]*)"', re.S)


class FFCCalendarSource(GenericSourceAdapter):
    profile = SourceProfile(
        name="ffc",
        homepage="https://www.ffc.fr/",
        endpoint=CALENDAR_URL,
        priority="official_federation",
        countries=("FR",),
        cadence_hours=24 * 7,
        data_format="html",
        parser_status="verified",
        robots_status="allowed",
        terms_status="unconfirmed",
        notes=(
            "French federation competition calendar. Randonnée-group VTT "
            "events only (XCM/rando style); licensed XC racing is out of "
            "calendar scope. Extraction via generic LLM stage."
        ),
    )

    #: how long to wait between the postback navigation steps
    step_delay: float = 0.5

    def pages(self, fetcher: Fetcher, options: CollectOptions) -> list[FetchedDocument]:
        # 1. Load the calendar, grab the postback state.
        calendar = fetcher.get(CALENDAR_URL)
        state = _postback_state(calendar.content)
        if state is None:
            return [calendar]

        # 2. POST the filter: group=Randonnée (+ VTT discipline).
        filtered = _filtered_view(
            fetcher,
            state,
            {
                "ctl00$cpContent$ctl07$filtreDisciplineID": DISCIPLINE_VTT_XC,
                "ctl00$cpContent$ctl07$filtreGroupeID": GROUP_RANDONNEE,
            },
        )

        # 3. Collect the event page URLs from the filtered view.
        html = filtered.content.decode("utf-8", errors="ignore")
        urls = sorted(set(_ROW_LINK.findall(html)))
        pages = [filtered]
        for path in urls[: options.limit or len(urls)]:
            pages.append(fetcher.get(f"https://competitions.ffc.fr{path}"))
            time.sleep(self.step_delay)
        return pages


def _postback_state(content: bytes) -> dict[str, str] | None:
    html = content.decode("utf-8", errors="ignore")
    viewstate = _VIEWSTATE.search(html)
    generator = _GENERATOR.search(html)
    validation = _VALIDATION.search(html)
    if not (viewstate and generator and validation):
        return None
    return {
        "__VIEWSTATE": viewstate.group(1),
        "__VIEWSTATEGENERATOR": generator.group(1),
        "__EVENTVALIDATION": validation.group(1),
    }


def _filtered_view(
    fetcher: Fetcher,
    state: dict[str, str],
    filters: dict[str, str],
) -> FetchedDocument:

    payload = {
        "__EVENTTARGET": "ctl00$cpContent$ctl07$filtreDisciplineID",
        "__EVENTARGUMENT": "",
        **state,
        **filters,
    }
    response = fetcher.post(
        CALENDAR_URL,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return response
