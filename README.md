# Babble Steam Frame watcher

Watches the [Babble Mouth Tracker Pro](https://babble.diy/store/babble-tracker-pro/) (the Babble
mouth tracker made for Steam Frame) and sounds the alarm the moment orders open:

| Where | What happens |
|---|---|
| **Phone** | Loud max-priority push alarm (ntfy app) + Gmail email |
| **PC and laptop** | Volume goes up, alarm sound loops, big red window with an "Open store page" button |
| **Gmail** | Email marked high-importance, with the store link |

It runs **in the cloud on GitHub's servers, for free**, so it keeps checking while your
computers are off. Every 30 minutes it waits a random 0-25 minutes, then checks.

What counts as **"on sale" (loud alarm):** the "Order Now"/"Add to cart" text appears, the buy
button stops being greyed out, or "Orders opening soon" disappears from the product page or the
shop page. After the first alarm it rings **2 more times** (on the next two checks), then stops.

**Quieter heads-up** (normal notification, PC popup without alarm sound): any other change to
the page, like a price announcement, a new date or new shop items. You'll also get a heads-up if
the shop can't be read for ~3 hours (site down or redesigned).

---

## Setup (about 10 minutes)

Your private settings, including your alarm channel name (the "ntfy topic"), are in
**`my-settings.local.txt`**. That file is never uploaded.

### 1. Phone: the alarm app

1. Install **ntfy** (free): Google Play or the App Store.
2. Tap **+** → enter your topic from `my-settings.local.txt` → Subscribe.
3. Make it loud:
   - Android: allow ntfy notifications, let it **override Do Not Disturb**, and if your version
     has the setting to keep alerting for max-priority messages, turn it on.
   - iPhone: allow notifications and **Time Sensitive** notifications for ntfy. iOS can't make
     a third-party app ring through silent mode, so the email/ntfy banner is what you'll get.

### 2. Gmail: an app password for sending the email

The watcher sends the email from your Gmail to your Gmail. Google requires an **app password**
for this. It's not your normal password, and you can delete it anytime.

1. Turn on **2-Step Verification** for your Google account (required for app passwords).
2. Go to <https://myaccount.google.com/apppasswords>, create one named `Babble watcher`.
3. Copy the 16-letter code. You'll paste it in step 3.

### 3. GitHub: the always-on checker

You need a free GitHub account. In this folder:

```bash
git init -b main
```

```bash
git add .
```

```bash
git commit -m "Babble Steam Frame watcher"
```

(If git says "Please tell me who you are", run `git config --global user.name "Your Name"` and
`git config --global user.email "you@example.com"` once, then commit again.)

```bash
gh repo create babble-watch --public --source . --push
```

Then add your secrets. Each command asks you to paste the value, which stays hidden and encrypted:

```bash
gh secret set NTFY_TOPIC
```

```bash
gh secret set GMAIL_ADDRESS
```

```bash
gh secret set GMAIL_APP_PASSWORD
```

(Prefer the website? Repo → **Settings → Secrets and variables → Actions → New repository
secret**, with the same three names.)

> **Why public?** Public repos get unlimited free run time, and the random wait needs that.
> Nothing personal is in the code. Your topic, email and password live only in the encrypted
> secrets. If you want it **private**, change `--max-delay 1500` to `--max-delay 0` in
> `.github/workflows/babble-watch.yml` so it fits GitHub's free 2,000 minutes/month. Checks then
> run every 30 minutes without the extra random wait.

### 4. PC and laptop: the alarm

On **each** Windows computer that should ring (copy the `pc-alarm` folder over, or download it
from your GitHub repo), run the line from `my-settings.local.txt`:

```bash
powershell -ExecutionPolicy Bypass -File Install-BabbleAlarm.ps1 -Topic YOUR-TOPIC
```

It starts on its own every time you sign in (no admin needed). If a computer was off when the
alarm went out, it rings when you turn it back on, for messages up to about 12 hours old.

Hear what it sounds like (from the `pc-alarm` folder):

```bash
powershell -ExecutionPolicy Bypass -File BabbleAlarm.ps1 -Test
```

### 5. Test everything at once

```bash
gh workflow run babble-watch.yml -f test_alert=true
```

Within a minute your phone should ring, your PCs should show the red window, and Gmail should get
a "TEST" email. (Or on GitHub: **Actions → Babble Steam Frame watch → Run workflow → tick "Send
a TEST alarm"**.)

---

## Day to day

- **Is it alive?** On GitHub open **Actions**. Every run's log says `still coming soon` and shows
  the buy button text. GitHub also emails you if a run fails.
- **Pause / stop:**

  ```bash
  gh workflow disable babble-watch.yml
  ```

  Remove the PC alarm with `Uninstall-BabbleAlarm.ps1`.
- **Note:** GitHub runs scheduled jobs "best effort", so a check can occasionally start late or
  be skipped when GitHub is busy. The next one catches up.

## Why not on the FRITZ!Box?

FRITZ!OS (the software on AVM FRITZ!Box routers) doesn't let you install or run your own
programs, so the checker can't live on the router. GitHub's free servers do the same job: they
run 24/7 whether or not your computers are on.

## Files

| File | What it is |
|---|---|
| `check.py` | The checker (Python, no extra packages). `python check.py --dry-run` to try it locally. |
| `.github/workflows/babble-watch.yml` | The schedule that runs it on GitHub. |
| `state.json` | What the shop looked like last time (created by the first run). |
| `pc-alarm/` | The Windows alarm: `BabbleAlarm.ps1` plus its install/uninstall scripts. |
| `my-settings.local.txt` | Your private topic. Not uploaded. |
