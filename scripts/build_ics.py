"""
Build deadlines.ics from huggingface/ai-deadlines repo + official sites + estimates.

Strategy:
1. Fetch YAML files for confirmed conferences from huggingface/ai-deadlines.
2. For each interest conference, extract deadlines of type {abstract, paper, supplementary, submission}.
3. Only include deadlines AFTER today.
4. For the NEXT cycle (not yet in upstream), HYBRID-observe the conference's
   official page (OFFICIAL_SOURCES): a parsed date within SANITY_WINDOW_DAYS of
   the +1-cycle estimate is trusted (confirmed); otherwise fall back to the
   pattern estimate, marked "(tentative)".
5. Events are ALL-DAY (VALUE=DATE) in KST. Each deadline → one all-day event on
   its KST deadline date = (deadline in KST − 1 day): a Mar 6 15:00 KST deadline
   lands on Mar 5; an AoE 23:59 deadline lands on its AoE calendar day. The
   submission OPEN is shared across a cycle's deadlines, so it's a single
   '접수 시작' marker (date = official open, else deadline − 7d).
6. When the official page exposes an author-registration open date, a separate
   '등록 시작' marker + an "Author registration" deadline (= the abstract
   deadline day) are emitted.
"""

import hashlib
import html as htmllib
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INTEREST = {
    # conference id in ai-deadlines repo → display name
    "cvpr":     "CVPR",
    "iccv":     "ICCV",
    "eccv":     "ECCV",
    "neurips":  "NeurIPS",
    "iclr":     "ICLR",
    "icml":     "ICML",
    "siggraph": "SIGGRAPH",
    "aaai":     "AAAI",
    "wacv":     "WACV",
    # Robotics
    "icra":     "ICRA",
    "iros":     "IROS",
    "rss":      "RSS",
    "corl":     "CoRL",
}

# Conferences held only in even or odd years. Tentative next-cycle generation
# jumps by 2 years for these instead of 1.
#   ECCV: even years (2024, 2026, 2028, ...)
#   ICCV: odd  years (2025, 2027, 2029, ...)
YEAR_PARITY = {
    "eccv": 0,
    "iccv": 1,
}

# Conferences NOT covered by ai-deadlines repo — hardcoded with their cadence.
# We fetch the latest known cycle from cached defaults and roll forward.
EXTRA_CONFS = {
    "SIGGRAPH Asia": {
        # SIGGRAPH Asia 2026: paper 2026-05-12, form 2026-04 (~late Apr)
        "latest": {
            "year": 2026,
            "deadlines": [
                ("paper", "2026-05-12", "AoE"),
            ],
        },
        "annual": True,  # roll forward by 1 year
    },
    "3DV": {
        # 3DV 2027 announced: paper 2026-08-28 PT, supp Sep (estimated 2026-09-04)
        "latest": {
            "year": 2027,
            "deadlines": [
                ("paper", "2026-08-28", "America/Los_Angeles"),
                ("supplementary", "2026-09-04", "America/Los_Angeles"),  # tentative within latest
            ],
        },
        "annual": True,
    },
}

DEADLINE_TYPES = {"abstract", "paper", "submission", "supplementary", "registration"}

# ---------------------------------------------------------------------------
# Official-site sources (hybrid scraper)
# ---------------------------------------------------------------------------
# For a cycle not yet in the upstream ai-deadlines repo, observe the conference's
# own page directly. `url(year)` builds the next-cycle URL from the conf's
# pattern; the page is fetched and run through a generic heuristic parser
# (date + nearby keyword), with an optional per-conf `parser` override. A parsed
# date is only TRUSTED (emitted as confirmed) if it lands within
# SANITY_WINDOW_DAYS of the pattern-based +1-cycle estimate; otherwise we keep
# the tentative estimate. This caps the blast radius of a misparse — a wildly
# wrong scrape can never silently push a confirmed-looking date to the calendar.
#
# Keyed by conf_id (same key space as INTEREST / YEAR_PARITY).
#   url     — lambda year → official schedule URL for that cycle.
#   layout  — "label_first" (label then date, e.g. "Abstract deadline: May 4")
#             or "date_first" (date then label, AAAI's WordPress table).
#             Default "label_first". Every parsed source below is verified
#             against its live page in /tmp before being added here.
OFFICIAL_SOURCES = {
    "aaai": {
        # https://aaai.org/conference/aaai/aaai-27/  (NN = year - 2000)
        "url": lambda year: f"https://aaai.org/conference/aaai/aaai-{year % 100:02d}/",
        "layout": "date_first",
    },
    # *.cc platform — "Label: Date" call-for-papers pages.
    "neurips": {"url": lambda year: f"https://neurips.cc/Conferences/{year}/CallForPapers"},
    "icml":    {"url": lambda year: f"https://icml.cc/Conferences/{year}/CallForPapers"},
    # ICLR deliberately NOT added: its CFP lists deadlines as bare "Sep 19" with
    # no year, and the real deadline falls in the conference's PRIOR calendar
    # year — the generic parser can't infer that, so it could never pass the
    # sanity check anyway. Left to the tentative estimate.
    # thecvf / ecva — "Label Date" Dates pages.
    "cvpr":    {"url": lambda year: f"https://cvpr.thecvf.com/Conferences/{year}/Dates"},
    "iccv":    {"url": lambda year: f"https://iccv.thecvf.com/Conferences/{year}/Dates"},
    "eccv":    {"url": lambda year: f"https://eccv.ecva.net/Conferences/{year}/Dates"},
}

# A parsed official date must be within this many days of the +1-cycle estimate
# to be trusted. Conferences drift a few weeks year to year; 75d is generous
# enough to allow real drift while rejecting gross misparses.
SANITY_WINDOW_DAYS = 75

# Some conf sites (aaai.org) 403 the default Python urllib User-Agent.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MONTHS = {'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
           'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
           'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
           'oct': 10, 'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12}

# "Month DD[st|nd|rd|th][, YYYY | 'YY]" — handles full/abbr months, ordinals,
# 4-digit / apostrophe-2-digit / absent years (e.g. "May 4, 2026", "Nov 07 '25",
# "March 3rd, 2025", "Sep 19").
_DATE_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*'?(\d{2,4})\b)?", re.I)

# deadline keyword (lowercased substring) → (dl_type, short_label). Ordered:
# most specific first; first matching rule per dl_type wins. Covers the label
# vocab of AAAI / NeurIPS / ICML / CVPR / ICCV / ECCV (all verified live).
_KEYWORD_RULES = [
    ("abstract",                   ("abstract", "Abstract")),
    ("paper registration",         ("abstract", "Abstract")),    # ICCV/ECCV first deadline
    ("full paper",                 ("paper", "Paper")),          # ".cc" / AAAI "full papers due"
    ("paper submission deadline",  ("paper", "Paper")),          # CVPR
    ("main conference submission", ("paper", "Paper")),          # ECCV
    ("submission and supplementary", ("paper", "Paper")),        # ICCV combined line
    ("supplementary material",     ("supplementary", "Supplementary")),
    ("supplemental material",      ("supplementary", "Supplementary")),
]

# Contexts that mean a nearby date is NOT a submission deadline.
_NEG_CONTEXT = ("opens", "open for", "notification", "feedback", "reviews",
                "rebuttal", "decision", "decisions", "camera", "acceptance",
                "early registration", "cancellation", "final paper",
                "final version", "results released", "job board", "careers")

# Max chars allowed between a keyword and the date it classifies.
_MAX_DATE_GAP = 70

# Labels that indicate auxiliary tracks (not the main paper track) — skip these.
SKIP_LABEL_PATTERNS = [
    "art ", "poster", "workshop proposal", "tutorial proposal",
    "appy hour", "student research", "student volunteers",
    "animation festival", "frontiers", "real-time live",
    "talks", "panels", "courses", "educator", "spatial storytelling",
    "immersive pavilion", "emerging technologies", "art gallery",
    "ai art", "production session",
]


def _is_main_track(label: str) -> bool:
    """Filter out auxiliary tracks; keep main paper/abstract/supp."""
    if not label:
        return True
    low = label.lower()
    for pat in SKIP_LABEL_PATTERNS:
        if pat in low:
            return False
    return True


def _shorten_label(label: str, dl_type: str) -> str:
    """Make a concise display name from the deadline label."""
    if not label:
        return dl_type.capitalize()
    l = label
    for noise in [" deadline", " Deadline", " submission", " Submission"]:
        l = l.replace(noise, "")
    return l.strip() or dl_type.capitalize()


RAW_BASE = "https://raw.githubusercontent.com/huggingface/ai-deadlines/main/src/data/conferences"

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "deadlines.ics"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_yaml(conf_id: str):
    url = f"{RAW_BASE}/{conf_id}.yml"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return yaml.safe_load(r.read())
    except urllib.error.HTTPError as e:
        print(f"  [warn] {conf_id}: HTTP {e.code}", file=sys.stderr)
        return None


def parse_deadline_dt(date_str: str, tz: str) -> datetime:
    """Parse a deadline string + timezone into a UTC datetime."""
    # Handle '2025-11-13 23:59:59' and '2025-11-13'
    if " " in date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    else:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    tz = (tz or "AoE").strip()

    if tz == "AoE":
        # AoE = UTC-12, so add 12h to get UTC
        return d.replace(tzinfo=timezone.utc) + timedelta(hours=12)
    if tz.startswith("UTC"):
        # e.g. "UTC+0", "UTC-7"
        offset_str = tz[3:] or "+0"
        sign = 1 if offset_str[0] == "+" else -1
        hours = int(offset_str[1:])
        return d.replace(tzinfo=timezone.utc) - timedelta(hours=sign * hours)
    if tz.startswith("GMT"):
        offset_str = tz[3:] or "+0"
        sign = 1 if offset_str[0] == "+" else -1
        hours = int(offset_str[1:])
        return d.replace(tzinfo=timezone.utc) - timedelta(hours=sign * hours)
    if tz == "America/Los_Angeles":
        # crude: PDT for Mar-Nov, else PST
        if 3 <= d.month <= 10:
            return d.replace(tzinfo=timezone.utc) + timedelta(hours=7)
        return d.replace(tzinfo=timezone.utc) + timedelta(hours=8)
    if tz == "Asia/Seoul":
        return d.replace(tzinfo=timezone.utc) - timedelta(hours=9)
    # Fallback: treat as UTC
    return d.replace(tzinfo=timezone.utc)


def _html_to_text(html: str) -> str:
    """Strip scripts/styles/tags and collapse whitespace (incl. &nbsp;)."""
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    return re.sub(r"[\s ]+", " ", text).strip()


def _detect_tz(window: str) -> str:
    """Infer the timezone for a deadline from text near it. Default AoE."""
    low = window.lower()
    if "utc-12" in low or "aoe" in low or "anywhere on earth" in low:
        return "AoE"
    m = re.search(r"utc\s*([+-]\d{1,2})", low)
    if m:
        return "UTC" + m.group(1)
    return "AoE"  # conference deadlines default to AoE


def _all_dates(text: str, year):
    """Every 'Month DD[, year]' in `text` as (start, end, year, month, day).
    A bare date with no year on the page is assumed to be `year` (the target
    cycle); dropped if `year` is None."""
    out = []
    for m in _DATE_RE.finditer(text):
        mon = _MONTHS.get(m.group(1).lower())
        if not mon:
            continue
        day = int(m.group(2))
        if not 1 <= day <= 31:
            continue
        yr = m.group(3)
        if yr:
            yr = int(yr)
            yr += 2000 if yr < 100 else 0
        elif year:
            yr = year
        else:
            continue
        out.append((m.start(), m.end(), yr, mon, day))
    return out


def _pick_date(dates, ks, ke, layout):
    """Date nearest to a keyword span [ks, ke], on the layout-preferred side
    (label_first → date AFTER the keyword; date_first → date BEFORE it). Falls
    back to the other side only if no same-side date is within _MAX_DATE_GAP."""
    fwd = bwd = None
    fd = bd = _MAX_DATE_GAP + 1
    for d in dates:
        ds, de = d[0], d[1]
        if ds >= ke:
            if ds - ke < fd:
                fwd, fd = d, ds - ke
        elif de <= ks:
            if ks - de < bd:
                bwd, bd = d, ks - de
    if layout == "date_first":
        primary, alt, pd, ad = bwd, fwd, bd, fd
    else:
        primary, alt, pd, ad = fwd, bwd, fd, bd
    if primary is not None and pd <= _MAX_DATE_GAP:
        return primary
    if alt is not None and ad <= _MAX_DATE_GAP:
        return alt
    return None


def heuristic_parse_deadlines(text: str, year=None, layout="label_first"):
    """Generic extractor for conference schedule pages. For each deadline
    keyword (_KEYWORD_RULES), classify the nearest date on the layout-preferred
    side. Handles both "Label: Date" pages (label_first — NeurIPS/ICML/CVPR/
    ICCV/ECCV) and AAAI's "Date Label" table (date_first). Also captures
    submission-/registration-window OPEN dates under '__sub_open__' /
    '__reg_open__'. Returns dl_type -> (short, "YYYY-MM-DD 23:59:59", tz), plus
    '__*_open__' as bare 'YYYY-MM-DD'. Verified against live pages (see
    /tmp parser_test harness / commit notes)."""
    low = text.lower()
    dates = _all_dates(text, year)
    results = {}

    def emit(dl_type, short, d):
        if dl_type in results:
            return
        tz = _detect_tz(text[d[0]:d[0] + 80])
        results[dl_type] = (short, f"{d[2]:04d}-{d[3]:02d}-{d[4]:02d} 23:59:59", tz)

    for kw, (dl_type, short) in _KEYWORD_RULES:
        if dl_type in results:
            continue
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx == -1:
                break
            start = idx + len(kw)
            ctx = low[max(0, idx - 30):idx + len(kw) + 30]
            # "paper registration" legitimately contains the _NEG word
            # "registration" — exempt it.
            if any(neg in ctx for neg in _NEG_CONTEXT) and "paper registration" not in ctx:
                continue
            d = _pick_date(dates, idx, idx + len(kw), layout)
            if d:
                emit(dl_type, short, d)
                break

    # A supplementary deadline bundled into the paper line (same date, e.g.
    # NeurIPS "...including all supplementary materials") is not a separate event.
    if "supplementary" in results and "paper" in results:
        if results["supplementary"][1] == results["paper"][1]:
            del results["supplementary"]

    # Submission-/registration-window OPEN dates. 'opens' (verb) only — avoids
    # matching the "open" in the "OpenReview"/"Open Review" platform name.
    for m in re.finditer(r"\bopens\b", low):
        if layout == "date_first":
            ctx = low[m.end():m.end() + 45]
        else:
            ctx = low[max(0, m.start() - 45):m.start()]
        d = _pick_date(dates, m.start(), m.end(), layout)
        if not d:
            continue
        ds = f"{d[2]:04d}-{d[3]:02d}-{d[4]:02d}"
        if "registration" in ctx:
            results.setdefault("__reg_open__", ds)
        elif "submission" in ctx or "paper" in ctx:
            results.setdefault("__sub_open__", ds)
    return results


def fetch_official_deadlines(conf_id: str, year: int):
    """Fetch a conference's official page for `year` and extract deadlines.
    Returns dl_type -> (short, date_str, tz), or {} on any failure (caller
    falls back to the tentative estimate). Cached per (conf_id, year)."""
    src = OFFICIAL_SOURCES.get(conf_id)
    if not src:
        return {}
    cache = fetch_official_deadlines._cache
    key = (conf_id, year)
    if key in cache:
        return cache[key]

    result = {}
    url = src["url"](year)
    layout = src.get("layout", "label_first")
    try:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        text = _html_to_text(html)
        parser = src.get("parser")
        if parser:
            result = parser(text) or {}
        else:
            result = heuristic_parse_deadlines(text, year=year, layout=layout) or {}
        result["__link__"] = url
        print(f"  [official] {conf_id} {year}: {[k for k in result if k != '__link__']} from {url}",
              file=sys.stderr)
    except Exception as e:
        print(f"  [official] {conf_id} {year}: fetch/parse failed ({type(e).__name__}: {e})",
              file=sys.stderr)

    cache[key] = result
    return result


fetch_official_deadlines._cache = {}


def stable_uid(title: str, dt: datetime) -> str:
    h = hashlib.md5(f"{title}|{dt.isoformat()}".encode()).hexdigest()[:16]
    return f"{h}@ai-conf-deadlines"


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


KST = timezone(timedelta(hours=9))


def deadline_kst_date(end_utc: datetime):
    """The all-day date for a deadline: the last KST day whose closing midnight
    (24:00) is ≤ the deadline — i.e. (deadline in KST − 1 day).date(). So a
    Mar 6 15:00 KST deadline lands on Mar 5; an AoE 23:59 deadline lands on its
    AoE calendar day."""
    return (end_utc.astimezone(KST) - timedelta(days=1)).date()


def make_allday_vevent(title: str, day, description: str, dtstamp: datetime,
                       reminders: bool) -> str:
    """An all-day (VALUE=DATE) event on `day` (a date). DTEND is the next day
    (exclusive), per RFC 5545. Optional 7-day / 1-day popup reminders."""
    uid = stable_uid(title, datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    dtstart = day.strftime("%Y%m%d")
    dtend = (day + timedelta(days=1)).strftime("%Y%m%d")
    dtstamp_str = dtstamp.strftime("%Y%m%dT%H%M%SZ")
    alarms = ""
    if reminders:
        for trig, lbl in (("-P7D", "7 days"), ("-P1D", "1 day")):
            alarms += (f"\nBEGIN:VALARM\nTRIGGER:{trig}\nACTION:DISPLAY\n"
                       f"DESCRIPTION:{ics_escape(title)} — {lbl}\nEND:VALARM")
    return (f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{dtstamp_str}\n"
            f"DTSTART;VALUE=DATE:{dtstart}\nDTEND;VALUE=DATE:{dtend}\n"
            f"SUMMARY:{ics_escape(title)}\nDESCRIPTION:{ics_escape(description)}"
            f"{alarms}\nEND:VEVENT")


def _open_to_dt(date_str: str) -> datetime:
    """A bare 'YYYY-MM-DD' open date → 00:00:00 UTC that day."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return d.replace(tzinfo=timezone.utc)


def period_start(end_dt: datetime, open_dt):
    """Return (start_dt, start_estimated). With a known open date, that's the
    start. Otherwise fall back to deadline − 7 days and flag it estimated."""
    if open_dt is not None:
        return open_dt, False
    return end_dt - timedelta(days=7), True


# ---------------------------------------------------------------------------
# Build events
# ---------------------------------------------------------------------------

def collect_from_ai_deadlines(now_utc: datetime):
    """Returns list of (title, end_dt, start_dt, description, is_tent, start_est)."""
    events = []
    for conf_id, display in INTEREST.items():
        data = fetch_yaml(conf_id)
        if not data:
            continue

        # Sort by year ascending
        data = sorted(data, key=lambda c: c.get("year", 0))
        years_present = {c.get("year"): c for c in data}

        # Find a "pattern cycle" — the most recent cycle that has non-empty deadlines.
        pattern_cycle = None
        for c in reversed(data):
            if c.get("deadlines"):
                pattern_cycle = c
                break

        # Confirmed future deadlines from cycles already in upstream. These have
        # no scraped open date, so the window start falls back to deadline − 7d.
        confirmed_keys = set()  # (year, dl_type, short) already emitted
        for cycle in data:
            year = cycle.get("year")
            link = cycle.get("link", "")
            for dl in cycle.get("deadlines", []) or []:
                dl_type = (dl.get("type") or "").lower()
                label = dl.get("label", "") or ""
                if dl_type not in DEADLINE_TYPES or not _is_main_track(label):
                    continue
                date_str = dl.get("date")
                tz = dl.get("timezone", "AoE")
                if not date_str:
                    continue
                try:
                    end_dt = parse_deadline_dt(date_str, tz)
                except Exception as e:
                    print(f"  [warn] {display} {year} {dl_type}: parse fail ({e})", file=sys.stderr)
                    continue
                short = _shorten_label(label, dl_type)
                confirmed_keys.add((year, dl_type, short))
                if end_dt <= now_utc:
                    continue
                start_dt, start_est = period_start(end_dt, None)
                desc = f"{label}. Source: huggingface/ai-deadlines ({link})"
                if start_est:
                    desc += " Window start estimated (deadline − 7d); no official open date."
                events.append((f"{display} {year} {short}", end_dt, start_dt,
                               desc, False, start_est))

        # For each cycle that exists in years_present but has empty deadlines, fill
        # from pattern. Also generate one cycle ahead if no entry exists yet.
        if pattern_cycle is None:
            continue
        pattern_year = pattern_cycle.get("year")

        parity = YEAR_PARITY.get(conf_id)
        cycle_step = 2 if parity is not None else 1  # ECCV/ICCV: biennial

        # Candidate cycles to generate tentative entries for: existing-but-empty + next cycle
        candidate_years = set()
        for y, c in years_present.items():
            if not c.get("deadlines"):
                candidate_years.add(y)
        # Always also try the next cycle after the latest known one
        latest_year = data[-1].get("year")
        candidate_years.add(latest_year + cycle_step)
        # Filter: only future
        candidate_years = {y for y in candidate_years if y > pattern_year}
        # Filter: respect year parity for biennial confs
        if parity is not None:
            candidate_years = {y for y in candidate_years if y % 2 == parity}

        for target_year in sorted(candidate_years):
            year_offset = target_year - pattern_year
            # Observe the official site for this cycle (if a source is registered).
            official = fetch_official_deadlines(conf_id, target_year)
            # Real submission-window open date (used even when the deadline is
            # only an estimate). None → window start falls back to deadline − 7d.
            sub_open_dt = None
            if official.get("__sub_open__"):
                try:
                    sub_open_dt = _open_to_dt(official["__sub_open__"])
                except Exception:
                    sub_open_dt = None
            # Remember the abstract deadline → end of the registration window.
            abstract_end, abstract_tent = None, True

            for dl in pattern_cycle.get("deadlines", []) or []:
                dl_type = (dl.get("type") or "").lower()
                label = dl.get("label", "") or ""
                if dl_type not in DEADLINE_TYPES or not _is_main_track(label):
                    continue
                date_str = dl.get("date")
                tz = dl.get("timezone", "AoE")
                if not date_str:
                    continue
                try:
                    pattern_dt = parse_deadline_dt(date_str, tz)
                except Exception:
                    continue
                tentative_dt = pattern_dt + timedelta(days=365 * year_offset)
                short = _shorten_label(label, dl_type)
                if (target_year, dl_type, short) in confirmed_keys:
                    continue

                # Default: pattern-based estimate (tentative deadline).
                end_dt, is_tent, use_short = tentative_dt, True, short
                src_desc = (
                    f"Tentative — based on {display} {pattern_year} pattern "
                    f"({label or dl_type}: {date_str} {tz}). "
                    f"Verify on official site when announced."
                )
                # Hybrid: trust the official date if it's within the sanity window.
                off = official.get(dl_type)
                if off:
                    off_short, off_date, off_tz = off
                    try:
                        off_dt = parse_deadline_dt(off_date, off_tz)
                    except Exception:
                        off_dt = None
                    if off_dt and abs((off_dt - tentative_dt).days) <= SANITY_WINDOW_DAYS:
                        end_dt, is_tent, use_short = off_dt, False, off_short
                        src_desc = (f"{off_short} deadline. Source: official site "
                                    f"({official.get('__link__', '')})")
                        confirmed_keys.add((target_year, dl_type, off_short))
                    else:
                        why = "out of sanity window" if off_dt else "unparseable"
                        print(f"  [official] {display} {target_year} {dl_type}: "
                              f"rejected ({why}) → keeping tentative", file=sys.stderr)

                if dl_type == "abstract":
                    abstract_end, abstract_tent = end_dt, is_tent

                if end_dt <= now_utc:
                    continue

                start_dt, start_est = period_start(end_dt, sub_open_dt)
                desc = src_desc
                if start_est:
                    desc += " Window start estimated (deadline − 7d); official open date unknown."
                else:
                    desc += f" Submission opens {official['__sub_open__']}."
                tent = " (tentative)" if is_tent else ""
                events.append((f"{display} {target_year} {use_short}{tent}",
                               end_dt, start_dt, desc, is_tent, start_est))

            # Author-registration window: registration opens → abstract deadline.
            reg_open = official.get("__reg_open__")
            if reg_open and abstract_end is not None and abstract_end > now_utc:
                try:
                    reg_start = _open_to_dt(reg_open)
                except Exception:
                    reg_start = None
                if reg_start is not None:
                    tent = " (tentative)" if abstract_tent else ""
                    desc = (f"Author registration window. Opens {reg_open}; closes at "
                            f"the abstract deadline. Source: official site "
                            f"({official.get('__link__', '')})")
                    events.append((f"{display} {target_year} Author registration{tent}",
                                   abstract_end, reg_start, desc, abstract_tent, False))

    return events


def collect_from_extras(now_utc: datetime):
    """Handle conferences not in ai-deadlines repo. No official open dates, so
    every window start falls back to deadline − 7d (estimated)."""
    events = []
    for display, conf in EXTRA_CONFS.items():
        latest = conf["latest"]
        latest_year = latest["year"]
        # confirmed entries from latest
        for dl_type, date_str, tz in latest["deadlines"]:
            try:
                end_dt = parse_deadline_dt(date_str, tz)
            except Exception:
                continue
            if end_dt <= now_utc:
                continue
            title = f"{display} {latest_year} {dl_type.capitalize()}"
            # Mark supplementary as tentative if it was a guess
            is_tent = (display == "3DV" and dl_type == "supplementary")
            if is_tent:
                title += " (tentative)"
                desc = "Tentative — exact date not yet on 3dvconf.github.io. Estimated from announcement (Sep)."
            else:
                desc = f"{dl_type.capitalize()} deadline. Hardcoded; ai-deadlines repo doesn't track {display}."
            start_dt, start_est = period_start(end_dt, None)
            if start_est:
                desc += " Window start estimated (deadline − 7d); no official open date."
            events.append((title, end_dt, start_dt, desc, is_tent, start_est))

        # tentative next cycle
        if not conf.get("annual"):
            continue
        next_year = latest_year + 1
        for dl_type, date_str, tz in latest["deadlines"]:
            try:
                pattern_dt = parse_deadline_dt(date_str, tz)
            except Exception:
                continue
            end_dt = pattern_dt + timedelta(days=365)
            if end_dt <= now_utc:
                continue
            start_dt, start_est = period_start(end_dt, None)
            desc = (
                f"Tentative — based on {display} {latest_year} pattern "
                f"({dl_type}: {date_str} {tz}). Verify on official site."
            )
            if start_est:
                desc += " Window start estimated (deadline − 7d); no official open date."
            events.append((f"{display} {next_year} {dl_type.capitalize()} (tentative)",
                           end_dt, start_dt, desc, True, start_est))
    return events


def build_ics():
    now_utc = datetime.now(timezone.utc)
    events = collect_from_ai_deadlines(now_utc) + collect_from_extras(now_utc)
    # de-dup by (title) - if both confirmed and tentative emitted, prefer confirmed
    by_key = {}
    for ev in events:
        title, end_dt, start_dt, desc, is_tent, start_est = ev
        # normalize key: same conf + same year + same type → keep confirmed
        base_key = title.replace(" (tentative)", "")
        existing = by_key.get(base_key)
        if existing is None:
            by_key[base_key] = ev
        elif existing[4] and not is_tent:  # confirmed beats tentative
            by_key[base_key] = ev
    events = sorted(by_key.values(), key=lambda e: e[1])

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mj//ai-conf-deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Conference Deadlines",
        "X-WR-TIMEZONE:Asia/Seoul",
        f"X-WR-CALDESC:AI/CV/Graphics deadlines. Auto-updated {now_utc.strftime('%Y-%m-%d %H:%M UTC')}. All-day events in KST: a '시작' (open) marker per cycle + a deadline marker per type. (tentative) = estimated from previous cycle.",
    ]

    # Each deadline → one all-day event on its KST deadline date. The submission
    # /registration OPEN is shared across a cycle's deadlines, so it's collapsed
    # to one '시작' marker per (conf-year, kind) instead of repeating per type.
    open_seen = set()
    summary = []
    for title, end_dt, start_dt, desc, is_tent, start_est in events:
        ddate = deadline_kst_date(end_dt)
        lines.append(make_allday_vevent(title, ddate, desc, now_utc, reminders=True))
        summary.append((ddate, "T" if is_tent else " ", title))

        m = re.match(r"^(.*?\b(?:19|20)\d{2})\b", title)
        prefix = m.group(1) if m else title
        is_reg = "Author registration" in title
        # Open date: real open (KST date) when known, else 7 days before the
        # deadline day (estimated).
        odate = (ddate - timedelta(days=7)) if start_est else start_dt.astimezone(KST).date()
        kind = "등록" if is_reg else "접수"
        okey = (prefix, kind)
        if okey not in open_seen:
            open_seen.add(okey)
            otitle = f"{prefix} {kind} 시작" + (" (tentative)" if (is_tent or start_est) else "")
            odesc = ("Estimated open (deadline − 7d); no official open date."
                     if start_est else "Submission window opens.")
            lines.append(make_allday_vevent(otitle, odate, odesc, now_utc, reminders=False))
            summary.append((odate, "o", otitle))

    lines.append("END:VCALENDAR")
    content = "\n".join(lines) + "\n"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    n_vevents = sum(1 for ln in lines if ln.startswith("BEGIN:VEVENT"))
    print(f"Wrote {n_vevents} all-day events from {len(events)} deadlines to {OUTPUT_PATH}")
    for ddate, marker, title in sorted(summary):
        print(f"  [{marker}] {ddate.isoformat()}  {title}")


if __name__ == "__main__":
    build_ics()
