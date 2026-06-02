"""
Build deadlines.ics from huggingface/ai-deadlines repo + tentative estimates.

Strategy:
1. Fetch YAML files for confirmed conferences from huggingface/ai-deadlines.
2. For each interest conference, extract deadlines of type {abstract, paper, supplementary, submission}.
3. Only include deadlines AFTER today.
4. For each interest conference, also generate a tentative entry for the NEXT cycle
   based on the most recent previous edition's pattern (offset by 1 year), if no
   confirmed entry for that cycle exists yet.
5. Mark tentative entries with "(tentative)" suffix and include the source pattern in DESCRIPTION.
"""

import hashlib
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


def stable_uid(title: str, dt: datetime) -> str:
    h = hashlib.md5(f"{title}|{dt.isoformat()}".encode()).hexdigest()[:16]
    return f"{h}@ai-conf-deadlines"


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def make_vevent(title: str, dt_utc: datetime, description: str, dtstamp: datetime) -> str:
    uid = stable_uid(title, dt_utc)
    dtstart = dt_utc.strftime("%Y%m%dT%H%M%SZ")
    dtend = (dt_utc + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
    dtstamp_str = dtstamp.strftime("%Y%m%dT%H%M%SZ")
    return f"""BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dtstamp_str}
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:{ics_escape(title)}
DESCRIPTION:{ics_escape(description)}
BEGIN:VALARM
TRIGGER:-P7D
ACTION:DISPLAY
DESCRIPTION:{ics_escape(title)} — 7 days
END:VALARM
BEGIN:VALARM
TRIGGER:-P1D
ACTION:DISPLAY
DESCRIPTION:{ics_escape(title)} — 1 day
END:VALARM
BEGIN:VALARM
TRIGGER:-PT1H
ACTION:DISPLAY
DESCRIPTION:{ics_escape(title)} — 1 hour
END:VALARM
END:VEVENT"""


# ---------------------------------------------------------------------------
# Build events
# ---------------------------------------------------------------------------

def collect_from_ai_deadlines(now_utc: datetime):
    """Returns list of (title, dt_utc, description, is_tentative)."""
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

        # Add confirmed future deadlines from all cycles
        confirmed_keys = set()  # (year, dl_type, label) we already emitted
        for cycle in data:
            year = cycle.get("year")
            link = cycle.get("link", "")
            for dl in cycle.get("deadlines", []) or []:
                dl_type = (dl.get("type") or "").lower()
                label = dl.get("label", "") or ""
                if dl_type not in DEADLINE_TYPES:
                    continue
                if not _is_main_track(label):
                    continue
                date_str = dl.get("date")
                tz = dl.get("timezone", "AoE")
                if not date_str:
                    continue
                try:
                    dt_utc = parse_deadline_dt(date_str, tz)
                except Exception as e:
                    print(f"  [warn] {display} {year} {dl_type}: parse fail ({e})", file=sys.stderr)
                    continue
                short = _shorten_label(label, dl_type)
                confirmed_keys.add((year, dl_type, short))
                if dt_utc <= now_utc:
                    continue
                title = f"{display} {year} {short}"
                desc = f"{label}. Source: huggingface/ai-deadlines ({link})"
                events.append((title, dt_utc, desc, False))
                confirmed_keys.add((year, dl_type, short))
                if dt_utc <= now_utc:
                    continue
                title = f"{display} {year} {short}"
                desc = f"{label}. Source: huggingface/ai-deadlines ({link})"
                events.append((title, dt_utc, desc, False))

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
            for dl in pattern_cycle.get("deadlines", []) or []:
                dl_type = (dl.get("type") or "").lower()
                label = dl.get("label", "") or ""
                if dl_type not in DEADLINE_TYPES:
                    continue
                if not _is_main_track(label):
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
                if tentative_dt <= now_utc:
                    continue
                short = _shorten_label(label, dl_type)
                # Skip if confirmed already
                if (target_year, dl_type, short) in confirmed_keys:
                    continue
                title = f"{display} {target_year} {short} (tentative)"
                desc = (
                    f"Tentative — based on {display} {pattern_year} pattern "
                    f"({label or dl_type}: {date_str} {tz}). "
                    f"Verify on official site when announced."
                )
                events.append((title, tentative_dt, desc, True))

    return events


def collect_from_extras(now_utc: datetime):
    """Handle conferences not in ai-deadlines repo."""
    events = []
    for display, conf in EXTRA_CONFS.items():
        latest = conf["latest"]
        latest_year = latest["year"]
        # confirmed entries from latest
        for dl_type, date_str, tz in latest["deadlines"]:
            try:
                dt_utc = parse_deadline_dt(date_str, tz)
            except Exception:
                continue
            if dt_utc <= now_utc:
                continue
            title = f"{display} {latest_year} {dl_type.capitalize()}"
            # Mark supplementary as tentative if it was a guess
            is_tent = (display == "3DV" and dl_type == "supplementary")
            if is_tent:
                title += " (tentative)"
                desc = f"Tentative — exact date not yet on 3dvconf.github.io. Estimated from announcement (Sep)."
            else:
                desc = f"{dl_type.capitalize()} deadline. Hardcoded; ai-deadlines repo doesn't track {display}."
            events.append((title, dt_utc, desc, is_tent))

        # tentative next cycle
        if not conf.get("annual"):
            continue
        next_year = latest_year + 1
        for dl_type, date_str, tz in latest["deadlines"]:
            try:
                pattern_dt = parse_deadline_dt(date_str, tz)
            except Exception:
                continue
            tentative_dt = pattern_dt + timedelta(days=365)
            if tentative_dt <= now_utc:
                continue
            title = f"{display} {next_year} {dl_type.capitalize()} (tentative)"
            desc = (
                f"Tentative — based on {display} {latest_year} pattern "
                f"({dl_type}: {date_str} {tz}). Verify on official site."
            )
            events.append((title, tentative_dt, desc, True))
    return events


def build_ics():
    now_utc = datetime.now(timezone.utc)
    events = collect_from_ai_deadlines(now_utc) + collect_from_extras(now_utc)
    # de-dup by (title) - if both confirmed and tentative emitted, prefer confirmed
    by_key = {}
    for title, dt_utc, desc, is_tent in events:
        # normalize key: same conf + same year + same type → keep confirmed
        base_key = title.replace(" (tentative)", "")
        existing = by_key.get(base_key)
        if existing is None:
            by_key[base_key] = (title, dt_utc, desc, is_tent)
        else:
            ex_title, ex_dt, ex_desc, ex_tent = existing
            # confirmed beats tentative
            if ex_tent and not is_tent:
                by_key[base_key] = (title, dt_utc, desc, is_tent)
    events = sorted(by_key.values(), key=lambda e: e[1])

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mj//ai-conf-deadlines//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Conference Deadlines",
        "X-WR-TIMEZONE:Asia/Seoul",
        f"X-WR-CALDESC:AI/CV/Graphics deadlines. Auto-updated {now_utc.strftime('%Y-%m-%d %H:%M UTC')}. (tentative) = estimated from previous cycle.",
    ]
    for title, dt_utc, desc, _ in events:
        lines.append(make_vevent(title, dt_utc, desc, now_utc))
    lines.append("END:VCALENDAR")
    content = "\n".join(lines) + "\n"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {len(events)} events to {OUTPUT_PATH}")
    for title, dt_utc, _, is_tent in events:
        marker = "T" if is_tent else " "
        print(f"  [{marker}] {dt_utc.strftime('%Y-%m-%d %H:%MZ')}  {title}")


if __name__ == "__main__":
    build_ics()
