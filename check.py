#!/usr/bin/env python3
"""
Babble Steam Frame release watcher.

Checks the Project Babble shop for the Steam Frame "Babble Mouth Tracker Pro"
and raises the alarm (loud ntfy push to phone + PC, and a Gmail email) as soon
as it looks orderable. Smaller changes to the page (new price, new date, new
items in the shop) send a quieter heads-up.

Runs on GitHub Actions every ~30 minutes at a random moment (see
.github/workflows/babble-watch.yml). Also runs on any computer with Python 3.8+;
no extra packages needed.

Settings come from environment variables (GitHub Secrets):
  NTFY_TOPIC          your private ntfy topic name (phone + PC alarm)
  NTFY_SERVER         optional, default https://ntfy.sh
  NTFY_TOKEN          optional, only if you use a protected ntfy server
  GMAIL_ADDRESS       the Gmail account that sends the email
  GMAIL_APP_PASSWORD  a Gmail "app password" for that account (NOT your normal password)
  ALERT_EMAIL_TO      optional, where the email goes (default: GMAIL_ADDRESS)

Usage:
  python3 check.py                     check once
  python3 check.py --max-delay 1500    wait a random 0-25 min first, then check
  python3 check.py --test              send a test alarm on every channel
  python3 check.py --dry-run           print alerts instead of sending them
"""

import argparse
import difflib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# What to watch
# --------------------------------------------------------------------------
PRO_URL = "https://babble.diy/store/babble-tracker-pro/"
SHOP_URL = "https://babble.diy/store/"

# Words on a button or the page that mean "you can buy it now".
BUY_WORDS = r"\b(order now|add to cart|buy now|pre-?order now|checkout|in stock now)\b"

PAGES = [
    {
        "id": "pro",
        "name": "Babble Mouth Tracker Pro (Steam Frame)",
        "url": PRO_URL,
        # If these are missing, the page didn't load properly (or moved): don't trust it.
        "must_contain": ["Mouth Tracker Pro"],
        # Any of these showing up = ALARM.
        "alarm_if_found": [BUY_WORDS],
        # Any of these disappearing = ALARM. (Today the buy button says
        # "Orders opening soon · Est. December 2026" and is greyed out.)
        "alarm_if_missing": ["Orders opening soon"],
        # A clickable (not greyed-out) buy/order button = ALARM.
        "watch_buttons": True,
    },
    {
        "id": "shop",
        "name": "Babble shop front page",
        "url": SHOP_URL,
        "must_contain": ["Mouth Tracker Pro"],
        "alarm_if_found": [],
        # The Pro's card in the shop also says "Orders opening soon" today.
        "alarm_if_missing": ["Orders opening soon"],
        "watch_buttons": False,
    },
]

ALARM_REMINDERS = 2          # after the first alarm, ring again on the next N checks
FAIL_ALERT_AFTER = 6         # tell you if the shop can't be read this many checks in a row (~3 h)
HEARTBEAT_DAYS = 20          # touch state.json this often so GitHub keeps the schedule alive
USER_AGENT = "Mozilla/5.0 (compatible; babble-release-watcher/1.0; personal stock check ~every 30 min)"

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Reading the page
# --------------------------------------------------------------------------
class PageText(HTMLParser):
    """Collects the visible text lines and the <button>s of a page."""

    SKIP = {"script", "style", "svg", "noscript", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main_depth = 0
        self.saw_main = False
        self.all_lines = []
        self.main_lines = []
        self.buttons = []
        self._button = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "main":
            self.main_depth += 1
            self.saw_main = True
        elif tag == "button":
            self._button = {
                "text": [],
                "disabled": any(k == "disabled" for k, _ in attrs)
                or any(k == "aria-disabled" and v == "true" for k, v in attrs),
                "in_main": self.main_depth > 0,
            }

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            if self.skip:
                self.skip -= 1
        elif tag == "main":
            if self.main_depth:
                self.main_depth -= 1
        elif tag == "button" and self._button is not None:
            self._button["text"] = " ".join(self._button["text"]).strip()
            self.buttons.append(self._button)
            self._button = None

    def handle_data(self, data):
        if self.skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.all_lines.append(text)
        if self.main_depth:
            self.main_lines.append(text)
        if self._button is not None:
            self._button["text"].append(text)


def fetch(url, attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, "replace")
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"could not download {url}: {last_error}")


def evaluate(page, html):
    """Returns what the page says: ok?, its text lines, released?, and why."""
    parser = PageText()
    parser.feed(html)
    parser.close()
    lines = parser.main_lines if (parser.saw_main and parser.main_lines) else parser.all_lines
    text = "\n".join(lines)
    low = text.lower()

    missing = [m for m in page["must_contain"] if m.lower() not in low]
    if missing:
        return {"ok": False, "why": f"page didn't contain {missing!r} (site changed, URL moved, or an error page)"}

    reasons = []
    for pattern in page.get("alarm_if_found", []):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            reasons.append(f'the page now says "{match.group(0)}"')
    for phrase in page.get("alarm_if_missing", []):
        if phrase.lower() not in low:
            reasons.append(f'"{phrase}" is no longer on the page')
    buttons = [b for b in parser.buttons if b["in_main"] or not parser.saw_main]
    if page.get("watch_buttons"):
        for b in buttons:
            if not b["disabled"] and re.search(BUY_WORDS + r"|\b(order|buy)\b", b["text"], re.IGNORECASE):
                reasons.append(f'the "{b["text"]}" button is now clickable')

    # Unique, in order.
    reasons = list(dict.fromkeys(reasons))
    status_buttons = [f'{b["text"]} ({"greyed out" if b["disabled"] else "CLICKABLE"})' for b in buttons if b["text"]]
    return {"ok": True, "lines": lines, "released": bool(reasons), "reasons": reasons, "buttons": status_buttons}


def describe_diff(old_lines, new_lines, limit=30):
    out = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("+"):
            out.append("NEW:     " + line[1:])
        elif line.startswith("-"):
            out.append("REMOVED: " + line[1:])
    if len(out) > limit:
        out = out[:limit] + [f"... and {len(out) - limit} more changed lines"]
    return out


# --------------------------------------------------------------------------
# Sending alerts
# --------------------------------------------------------------------------
class Alert:
    def __init__(self, kind, title, body, url, priority, tags, email_subject):
        self.kind, self.title, self.body, self.url = kind, title, body, url
        self.priority, self.tags, self.email_subject = priority, tags, email_subject


def settings():
    env = os.environ.get
    gmail = (env("GMAIL_ADDRESS") or "").strip()
    return {
        "ntfy_topic": (env("NTFY_TOPIC") or "").strip(),
        "ntfy_server": (env("NTFY_SERVER") or "https://ntfy.sh").strip().rstrip("/"),
        "ntfy_token": (env("NTFY_TOKEN") or "").strip(),
        "gmail": gmail,
        "gmail_password": (env("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip(),
        "email_to": (env("ALERT_EMAIL_TO") or "").strip() or gmail,
    }


def send_ntfy(cfg, alert):
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": alert.title,
        "message": alert.body[:3900],
        "priority": alert.priority,
        "tags": alert.tags,
    }
    if alert.url:
        payload["click"] = alert.url
        payload["actions"] = [{"action": "view", "label": "Open store page", "url": alert.url}]
    # ntfy.sh only emails for signed-in accounts, so this needs NTFY_TOKEN.
    if cfg["email_to"] and cfg["ntfy_token"] and not cfg["gmail_password"]:
        payload["email"] = cfg["email_to"]
    req = urllib.request.Request(
        cfg["ntfy_server"] + "/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    if cfg["ntfy_token"]:
        req.add_header("Authorization", "Bearer " + cfg["ntfy_token"])
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def send_email(cfg, alert):
    msg = EmailMessage()
    msg["Subject"] = alert.email_subject
    msg["From"] = f"Babble watcher <{cfg['gmail']}>"
    msg["To"] = cfg["email_to"]
    if alert.priority >= 5:
        msg["X-Priority"] = "1"
        msg["Importance"] = "high"
    body = alert.body
    if alert.url:
        body += f"\n\nStore page: {alert.url}"
    body += "\n\n-- sent by your Babble watcher (GitHub Actions)"
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(cfg["gmail"], cfg["gmail_password"])
        smtp.send_message(msg)


def deliver(cfg, alert, dry_run=False):
    """Sends on every configured channel. Returns True if at least one worked."""
    if dry_run:
        print(f"\n[dry-run] would send {alert.kind.upper()} (priority {alert.priority})")
        print(f"  title  : {alert.title}")
        print(f"  email  : {alert.email_subject}")
        print("  body   : " + alert.body.replace("\n", "\n           "))
        return True
    channels = []
    if cfg["ntfy_topic"]:
        via_email = " + email" if (cfg["email_to"] and cfg["ntfy_token"] and not cfg["gmail_password"]) else ""
        channels.append((f"ntfy (phone + PC alarm{via_email})", send_ntfy))
    if cfg["gmail"] and cfg["gmail_password"]:
        channels.append(("Gmail", send_email))
    if not channels:
        print("ERROR: no alert channel is set up. Add the NTFY_TOPIC and/or GMAIL_* secrets.")
        return False
    worked = False
    for name, send in channels:
        try:
            send(cfg, alert)
            print(f"  sent via {name}")
            worked = True
        except Exception as exc:  # keep going with the other channel
            print(f"  FAILED to send via {name}: {exc}")
    return worked


def alarm_alert(reasons, reminder=False):
    title = ("Reminder: " if reminder else "") + "Babble Steam Frame tracker looks AVAILABLE - go order!"
    body = (
        "The Babble Mouth Tracker Pro (made for Steam Frame) looks like it can be ordered now.\n\n"
        "Why I think so:\n- " + "\n- ".join(reasons)
    )
    return Alert(
        "alarm", title, body, PRO_URL, 5, ["rotating_light", "shopping_cart"],
        ("[REMINDER] " if reminder else "") + "BABBLE ALERT: Steam Frame tracker (Pro) looks available!",
    )


# --------------------------------------------------------------------------
# State (what the shop looked like last time)
# --------------------------------------------------------------------------
def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(path, state):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run_check(args, cfg):
    state = load_state(args.state)
    old_pages = state.get("pages", {})
    new_pages = dict(old_pages)
    alarm_reasons, notices, problems = [], [], []

    for page in PAGES:
        try:
            if args.html_dir:
                with open(os.path.join(args.html_dir, page["id"] + ".html"), encoding="utf-8") as f:
                    html = f.read()
            else:
                html = fetch(page["url"])
            result = evaluate(page, html)
        except Exception as exc:
            problems.append(f"{page['name']}: {exc}")
            print(f"[{page['id']}] PROBLEM: {exc}")
            continue
        if not result["ok"]:
            problems.append(f"{page['name']}: {result['why']}")
            print(f"[{page['id']}] PROBLEM: {result['why']}")
            continue

        old = old_pages.get(page["id"], {})
        was_released = old.get("released", False)
        print(f"[{page['id']}] {'LOOKS AVAILABLE' if result['released'] else 'still coming soon'}"
              + (f"  | buttons: {'; '.join(result['buttons'])}" if result["buttons"] else ""))

        if result["released"] and not was_released:
            alarm_reasons += [f"{page['name']}: {r}" for r in result["reasons"]]
        elif not result["released"] and was_released:
            notices.append(f"{page['name']}: no longer looks orderable (sold out or paused?). "
                           "I'll ring again if it comes back.")
        if old.get("lines") and old["lines"] != result["lines"]:
            changes = describe_diff(old["lines"], result["lines"])
            if changes:
                notices.append(f"{page['name']} changed:\n" + "\n".join(changes))
        new_pages[page["id"]] = {"released": result["released"], "lines": result["lines"]}

    first_run = not old_pages
    alerts = []
    reminders_left = state.get("reminders_left", 0)
    any_released = any(p.get("released") for p in new_pages.values())

    if alarm_reasons:
        alarm = alarm_alert(alarm_reasons)
        if notices:
            alarm.body += "\n\nWhat changed on the pages:\n\n" + "\n\n".join(notices)
        alerts.append(alarm)
        reminders_left = ALARM_REMINDERS
    else:
        if any_released and reminders_left > 0:
            alerts.append(alarm_alert(["it still looks available (this is a reminder)"], reminder=True))
            reminders_left -= 1
        if notices and not first_run:
            alerts.append(Alert(
                "notice", "Babble shop page changed (not on sale yet)",
                "Something changed on the Babble shop, but it doesn't look orderable yet.\n\n"
                + "\n\n".join(notices),
                PRO_URL, 4, ["eyes"], "Babble watcher: the shop page changed",
            ))
    if not any_released:
        reminders_left = 0

    fail_streak = state.get("fail_streak", 0)
    fail_notified = state.get("fail_notified", False)
    if problems:
        fail_streak += 1
        if fail_streak >= FAIL_ALERT_AFTER and not fail_notified:
            alerts.append(Alert(
                "problem", "Babble watcher can't read the shop",
                f"The last {fail_streak} checks couldn't read the Babble shop properly:\n- "
                + "\n- ".join(problems)
                + "\n\nThe site may be down or redesigned. Please look at the store yourself.",
                PRO_URL, 3, ["warning"], "Babble watcher: can't read the shop",
            ))
            fail_notified = True
    else:
        fail_streak, fail_notified = 0, False

    if first_run and not alarm_reasons:
        print("First run: saved what the shop looks like now. You'll hear from me when it changes.")

    delivered_all = True
    for alert in alerts:
        print(f"Sending {alert.kind}: {alert.title}")
        if not deliver(cfg, alert, args.dry_run):
            delivered_all = False

    if not delivered_all:
        # Don't save, so the next check notices the same thing and tries again.
        print("ERROR: at least one alert could not be delivered; state not saved, will retry next run.")
        return 1

    today = datetime.now(timezone.utc).date()
    heartbeat = state.get("heartbeat")
    try:
        stale = (today - datetime.strptime(heartbeat, "%Y-%m-%d").date()).days >= HEARTBEAT_DAYS
    except (TypeError, ValueError):
        stale = True
    new_state = {
        "pages": new_pages,
        "reminders_left": reminders_left,
        "fail_streak": fail_streak,
        "fail_notified": fail_notified,
        "heartbeat": today.isoformat() if stale else heartbeat,
    }
    if new_state != state:
        save_state(args.state, new_state)
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Watch the Babble shop for the Steam Frame tracker.")
    ap.add_argument("--state", default=os.path.join(HERE, "state.json"), help="where to remember the last check")
    ap.add_argument("--max-delay", type=int, default=0, help="wait a random 0..N seconds before checking")
    ap.add_argument("--test", action="store_true", help="send a test alarm on every channel and exit")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    ap.add_argument("--html-dir", help="(testing) read <page id>.html files from here instead of the web")
    args = ap.parse_args()
    cfg = settings()

    if args.test:
        alert = Alert(
            "test", "TEST: Babble watcher alarm",
            "This is a test. If your phone rang, your PC showed a red alarm window, and you got this "
            "email, everything is set up. The real alarm looks exactly like this.",
            PRO_URL, 5, ["test_tube", "rotating_light"], "TEST: Babble watcher alarm",
        )
        return 0 if deliver(cfg, alert, args.dry_run) else 1

    if args.max_delay > 0:
        wait = random.randint(0, args.max_delay)
        print(f"Waiting a random {wait // 60} min {wait % 60} s before checking...")
        sys.stdout.flush()
        time.sleep(wait)

    print(f"Checking at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    return run_check(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
