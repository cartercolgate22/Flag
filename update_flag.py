#!/usr/bin/env python3
"""
Checks Idaho's official flag status and writes status.json next to index.html.

Design rule: never guess. If a field can't be read with confidence, it is
written as null and the app falls back to the fixed federal calendar.
A wrong half-staff alert is worse than no alert.
"""

import json, re, sys, urllib.request, urllib.error
from datetime import datetime, timezone, date

STATUS_PAGE = "https://gov.idaho.gov/flag-status/"
FEED_CANDIDATES = [
    "https://gov.idaho.gov/wp-json/wp/v2/posts?per_page=20&search=half-staff",
    "https://gov.idaho.gov/pressrelease/feed/",
    "https://gov.idaho.gov/feed/",
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; HalfStaffBot/1.0; personal flagpole reminder)"}


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#8217;", "'")
           .replace("&#8220;", '"').replace("&#8221;", '"').replace("&#8211;", "-"))
    return re.sub(r"\s+", " ", h).strip()


# ---------------------------------------------------------------- status page
def read_status_page():
    """
    The page renders two blocks — 'USA Flag Status' and 'Idaho Flag Status' —
    each followed by a heading like 'Flag at full staff'. Read both.
    """
    try:
        html = get(STATUS_PAGE)
    except Exception as e:
        return {"error": f"status page unreachable: {e}"}

    text = strip_tags(html)
    out = {"us": None, "idaho": None}

    for label, key in (("USA Flag Status", "us"), ("Idaho Flag Status", "idaho")):
        i = text.find(label)
        if i == -1:
            continue
        window = text[i:i + 220].lower()
        m = re.search(r"flag at (half|full)[\s-]?staff", window)
        if m:
            out[key] = "half" if m.group(1) == "half" else "full"

    # fallback: if the labels moved, use the whole page — but only when unambiguous
    if out["us"] is None and out["idaho"] is None:
        low = text.lower()
        half = len(re.findall(r"flag at half[\s-]?staff", low))
        full = len(re.findall(r"flag at full[\s-]?staff", low))
        if half and not full:
            out["us"] = out["idaho"] = "half"
        elif full and not half:
            out["us"] = out["idaho"] = "full"

    return out


# ------------------------------------------------------------------- releases
MONTHS = ("january february march april may june july august september "
          "october november december").split()


def parse_date(s, default_year):
    m = re.search(r"(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2})(?:\s*,\s*(\d{4}))?", s, re.I)
    if not m:
        return None
    mon = MONTHS.index(m.group(1).lower()) + 1
    day = int(m.group(2))
    yr = int(m.group(3)) if m.group(3) else default_year
    try:
        return date(yr, mon, day).isoformat()
    except ValueError:
        return None


def read_releases():
    """Most recent half-staff directive: reason, start, end. Any field may be None."""
    raw, kind = None, None
    for url in FEED_CANDIDATES:
        try:
            raw = get(url)
            kind = "json" if "wp-json" in url else "rss"
            break
        except Exception:
            continue
    if raw is None:
        return []

    items = []
    if kind == "json":
        try:
            for p in json.loads(raw):
                items.append({
                    "title": strip_tags(p.get("title", {}).get("rendered", "")),
                    "body": strip_tags(p.get("content", {}).get("rendered", "")
                                       or p.get("excerpt", {}).get("rendered", "")),
                    "url": p.get("link", ""),
                    "at": p.get("date", ""),
                })
        except Exception:
            return []
    else:
        for chunk in re.findall(r"(?s)<item>(.*?)</item>", raw)[:25]:
            def tag(t):
                m = re.search(r"(?s)<%s>(.*?)</%s>" % (t, t), chunk)
                return strip_tags(m.group(1)) if m else ""
            items.append({"title": tag("title"),
                          "body": tag("description") + " " + tag("content:encoded"),
                          "url": tag("link"), "at": tag("pubDate")})

    orders = []
    for it in items:
        blob = (it["title"] + " " + it["body"])
        if not re.search(r"half[\s-]?staff", blob, re.I):
            continue
        year = datetime.now(timezone.utc).year
        m = re.search(r"(?:pubDate|)(\d{4})", it.get("at", ""))
        if m:
            year = int(m.group(1))

        end = None
        me = re.search(r"until\s+(?:sunrise|sunset|sundown)?\s*(?:on\s+)?"
                       r"(?:\w+day,?\s*)?([A-Z][a-z]+\.?\s+\d{1,2}(?:,\s*\d{4})?)", blob)
        if me:
            end = parse_date(me.group(1), year)

        start = None
        ms = re.search(r"(?:starting|beginning|immediately starting)\s+"
                       r"(?:\w+day,?\s*)?([A-Z][a-z]+\.?\s+\d{1,2}(?:,\s*\d{4})?)", blob)
        if ms:
            start = parse_date(ms.group(1), year)

        indefinite = bool(re.search(r"day (?:following|after) the memorial service", blob, re.I))

        orders.append({
            "reason": it["title"].replace("Half-staff Flag Directive:", "").strip(" :-–"),
            "start": start,
            "end": end,
            "end_known": end is not None and not indefinite,
            "url": it["url"],
        })
    return orders[:5]


# ----------------------------------------------------------------------- main
def main():
    status = read_status_page()
    orders = read_releases()

    us = status.get("us")
    idaho = status.get("idaho")

    doc = {
        "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "us": us,
        "idaho": idaho,
        "orders": orders,
        "source": STATUS_PAGE,
        "ok": us is not None,
    }
    if "error" in status:
        doc["error"] = status["error"]

    with open("status.json", "w") as f:
        json.dump(doc, f, indent=2)

    print(json.dumps(doc, indent=2))
    # never fail the workflow on a scrape miss — a stale file beats a broken one
    return 0


if __name__ == "__main__":
    sys.exit(main())
