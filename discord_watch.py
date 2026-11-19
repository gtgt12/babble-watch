#!/usr/bin/env python3
"""
Babble Discord announcement watcher.

Babble's announcement channel is "followed" into a channel on YOUR Discord server.
Your own bot reads that channel every couple of minutes:
  - a new announcement about the Pro / Steam Frame that talks about orders/launch = ALARM
  - any other new announcement = heads-up
Alerts go out the same way as check.py (ntfy phone + PC alarm, Gmail if set up).

Settings (environment, e.g. ~/babble-watch/env on the Pi):
  DISCORD_BOT_TOKEN    your bot's token (Discord Developer Portal -> your app -> Bot)
  DISCORD_CHANNEL_ID   the channel on your server that receives Babble's announcements
  + the NTFY_* / GMAIL_* settings check.py uses

Usage:
  python3 discord_watch.py               check once (cron runs this every 2 minutes)
  python3 discord_watch.py --check-setup make sure the token and channel work
  python3 discord_watch.py --dry-run     print alerts instead of sending
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check  # noqa: E402  (same folder: alert sending + settings)

API = "https://discord.com/api/v10"
TOPIC_WORDS = check.FEED_TOPIC_WORDS     # Pro / Steam Frame
BUY_WORDS = check.FEED_ALARM_WORDS       # order / available / launch / ...


def api_get(path, token):
    req = urllib.request.Request(API + path, headers={
        "Authorization": "Bot " + token,
        "User-Agent": "DiscordBot (https://github.com/gtgt12/babble-watch, 1.0)",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def message_text(msg):
    parts = [msg.get("content") or ""]
    for emb in msg.get("embeds") or []:
        parts += [emb.get("title") or "", emb.get("description") or ""]
        parts += [(f.get("name") or "") + " " + (f.get("value") or "") for f in emb.get("fields") or []]
    return " ".join(p for p in parts if p).strip()


def classify(messages):
    """messages: oldest first. Returns (alarm_lines, notice_lines)."""
    alarms, notices = [], []
    for msg in messages:
        text = message_text(msg)
        who = (msg.get("author") or {}).get("username", "?")
        short = re.sub(r"\s+", " ", text)[:600] or "(no text, maybe an image)"
        line = f"Discord ({who}): {short}"
        if re.search(TOPIC_WORDS, text, re.I) and re.search(BUY_WORDS, text, re.I):
            alarms.append(line)
        else:
            notices.append(line)
    return alarms, notices


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(HERE, "discord_state.json"))
    ap.add_argument("--check-setup", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake", help="(testing) JSON file of messages instead of asking Discord")
    args = ap.parse_args()

    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    channel = (os.environ.get("DISCORD_CHANNEL_ID") or "").strip()
    if not args.fake and (not token or not channel):
        if args.check_setup:
            print("Not set up yet: DISCORD_BOT_TOKEN and/or DISCORD_CHANNEL_ID missing.")
            return 1
        return 0  # quietly do nothing until Discord is set up

    if args.check_setup:
        try:
            ch = api_get(f"/channels/{channel}", token)
            msgs = api_get(f"/channels/{channel}/messages?limit=5", token)
        except urllib.error.HTTPError as exc:
            hints = {401: "the bot token is wrong", 403: "the bot can't see that channel (permissions)",
                     404: "no channel with that ID (or the bot isn't on that server)"}
            print(f"Discord said {exc.code}: {hints.get(exc.code, exc.reason)}")
            return 1
        print(f"OK: bot can read #{ch.get('name')} ({len(msgs)} recent messages).")
        empty = [m for m in msgs if not message_text(m)]
        if msgs and len(empty) == len(msgs):
            print("WARNING: messages have no text. Turn on 'Message Content Intent' for the bot.")
        return 0

    state = {}
    try:
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        pass

    last_id = state.get("last_id")
    try:
        if args.fake:
            with open(args.fake, encoding="utf-8") as f:
                msgs = json.load(f)
        else:
            q = f"?limit=50&after={last_id}" if last_id else "?limit=1"
            msgs = api_get(f"/channels/{channel}/messages{q}", token)
    except Exception as exc:
        print(f"Discord check failed: {exc}")
        return 0
    msgs = sorted(msgs, key=lambda m: int(m["id"]))
    if last_id:
        msgs = [m for m in msgs if int(m["id"]) > int(last_id)]

    if not last_id:
        state["last_id"] = msgs[-1]["id"] if msgs else "0"
        print("First Discord check: remembered where the channel is. New announcements will alert you.")
    elif msgs:
        alarms, notices = classify(msgs)
        cfg = check.settings()
        ok = True
        if alarms:
            a = check.alarm_alert(alarms + ([""] + notices if notices else []))
            ok = check.deliver(cfg, a, args.dry_run) and ok
        elif notices:
            a = check.Alert("notice", "New Babble announcement on Discord",
                            "\n\n".join(notices), check.PRO_URL, 4, ["speech_balloon"],
                            "Babble watcher: new Discord announcement")
            ok = check.deliver(cfg, a, args.dry_run) and ok
        if not ok:
            print("Alert not delivered; will retry next time.")
            return 1
        state["last_id"] = msgs[-1]["id"]
        print(f"{len(msgs)} new Discord message(s): {len(alarms)} alarm, {len(notices)} heads-up.")
    with open(args.state, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
