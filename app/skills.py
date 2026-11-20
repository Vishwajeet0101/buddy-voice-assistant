"""Everything Buddy can do on the laptop without asking the AI.

Each skill is a regex (the whole sentence must match) + a function returning what to say.
fast=True means it is safe to run straight from the offline speech recogniser; fast=False skills contain
free text (song names, notes...) so the sentence is double-checked with online speech-to-text first.
A skill can set ctx.pending = fn(text) to ask a question ("Are you sure?", "What should the note say?").
"""
import ast
import ctypes
import ctypes.wintypes as wt
import json
import operator
import os
import random
import re
import subprocess
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import psutil

import apps
import config

NO_WINDOW = 0x08000000
user32 = ctypes.windll.user32
SKILLS = []
YES = re.compile(r"(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm|haan|ha|han|haa|kar do|please do)\b.*", re.I)
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
             "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}


def skill(pattern, fast=True):
    def deco(fn):
        SKILLS.append((re.compile(pattern, re.I), fn, fast))
        return fn
    return deco


def clean(text):
    name = re.escape((config.load().get("assistant_name") or "buddy").lower())
    t = text.strip().lower()
    t = re.sub(rf"^(?:hey|hi|ok|okay)?\s*{name}\b[,!.]?\s*", "", t)
    t = re.sub(r"^(?:please|can you|could you|will you|would you)\s+", "", t)
    t = re.sub(r"\s+(?:please|for me|now)$", "", t)
    return t.strip().rstrip("!.?,").strip()


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
_SCALE = {"hundred": 100, "thousand": 1000, "lakh": 100000, "million": 1000000}


def words_to_numbers(text):
    """'twelve times twelve' -> '12 times 12', 'one hundred and five' -> '105' (the offline recogniser writes words)."""
    out, cur, total, active = [], 0, 0, False
    tokens = text.split()

    def flush():
        nonlocal cur, total, active
        if active:
            out.append(str(total + cur))
        cur, total, active = 0, 0, False

    for i, w in enumerate(tokens):
        if w in _UNITS:
            cur += _UNITS[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w in _SCALE and active:
            cur = max(cur, 1) * _SCALE[w]
            if _SCALE[w] >= 1000:
                total, cur = total + cur, 0
        elif w == "and" and active and i + 1 < len(tokens) and (tokens[i + 1] in _UNITS or tokens[i + 1] in _TENS):
            continue
        elif w == "point" and active and i + 1 < len(tokens) and tokens[i + 1] in _UNITS:
            flush()
            out[-1] += "."
            continue
        else:
            flush()
            out.append(w)
            continue
        active = True
    flush()
    return re.sub(r"(\d+)\.\s+(\d)", r"\1.\2", " ".join(out))


def find(text):
    t = clean(text)
    for candidate in dict.fromkeys((t, words_to_numbers(t))):
        for rx, fn, fast in SKILLS:
            m = rx.fullmatch(candidate)
            if m:
                return fn, m, fast
    return None, None, None


def kind(text):
    """'fast', 'check' or None (= a question for the AI)."""
    fn, m, fast = find(text)
    if not fn:
        return None
    if fn is open_app:
        return "fast" if apps.find(m.group(1)) else "check"
    return "fast" if fast else "check"


def run(text, ctx):
    """Returns what to say, or None if the AI should answer."""
    if ctx.pending:
        fn, ctx.pending = ctx.pending, None
        return fn(text)
    fn, m, _ = find(text)
    if not fn:
        return None
    try:
        return fn(m, ctx)
    except Exception as e:
        config.log.exception("skill %s failed", fn.__name__)
        return f"Sorry, that didn't work: {e}"


# ===================== Helpers =====================

def number(s):
    s = (s or "").strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    if s in ("half", "half an", "half a"):
        return 0.5
    return NUM_WORDS.get(s)


DUR = re.compile(r"(\d+(?:\.\d+)?|half an?|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                 r"fifteen|twenty|thirty|forty five|forty|fifty|sixty|ninety)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)", re.I)


def parse_duration(text):
    total = 0
    for amount, unit in DUR.findall(text):
        n = number(amount)
        if n is None:
            continue
        u = unit.lower()
        total += n * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1)
    if re.search(r"\band a half\b", text) and total:
        total += 1800 if "hour" in text else 30
    return int(total) or None


def parse_clock(text):
    """'7 am', '7:30 pm', '19:00', 'tomorrow at 6' -> the next datetime it happens."""
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?|in the morning|in the evening|at night|o'?clock)?",
                  text, re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if h > 23 or mi > 59:
        return None
    if ap.startswith("p") or "evening" in ap or "night" in ap:
        h = h % 12 + 12
    elif ap.startswith("a") or "morning" in ap:
        h = h % 12
    now = datetime.now()
    when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if "tomorrow" in text:
        if not ap and 1 <= h <= 6:
            when += timedelta(hours=12)          # "tomorrow at 5" -> 5 pm, "tomorrow at 9" -> 9 am
        return when + timedelta(days=1)
    if not ap and h < 12 and when <= now and when + timedelta(hours=12) > now:
        when += timedelta(hours=12)              # "at 5" in the afternoon means 5 pm
    if when <= now:
        when += timedelta(days=1)
    return when


def say_duration(secs):
    secs = int(secs)
    h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "", f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{s} second{'s' * (s != 1)}" if s and not h else ""]
    return " and ".join(p for p in parts if p) or "0 seconds"


def say_time(dt):
    day = "today" if dt.date() == datetime.now().date() else "tomorrow" if \
        dt.date() == (datetime.now() + timedelta(days=1)).date() else dt.strftime("%A")
    return f"{dt:%I:%M %p}".lstrip("0") + f" {day}"


def press(vk, times=1):
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)


def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Buddy-Voice-Assistant"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def confirm(question, action, ctx):
    def answer(text):
        return action() if YES.fullmatch(clean(text)) else "Okay, I won't."
    ctx.pending = answer
    return question


# ===================== Basics =====================

@skill(r"(?:help|what can you do|what all can you do|what are your features|commands)")
def help_(m, ctx):
    ctx.show_text = ("Open apps & websites  ·  close Chrome\n"
                     "Set a timer for 10 minutes  ·  wake me up at 7 am  ·  remind me to call mom at 6\n"
                     "Volume up / set volume to 40  ·  mute  ·  brightness 70\n"
                     "Play Believer on YouTube  ·  pause  ·  next song\n"
                     "What's the weather  ·  what time is it  ·  battery\n"
                     "Take a note  ·  read my notes  ·  type hello\n"
                     "What's on my screen  ·  screenshot  ·  lock  ·  shut down\n"
                     "Any question - I'll answer with AI.")
    return ("I can open apps, set timers, alarms and reminders, control volume and music, tell you the weather, "
            "take notes, look at your screen, and answer any question. Here's the list.")


@skill(r"(?:who are you|what(?:'s| is) your name|tumhara naam kya hai|introduce yourself)")
def who(m, ctx):
    name = config.load().get("assistant_name") or "Buddy"
    return f"I'm {name}, your voice assistant. Just say hey {name} whenever you need me."


@skill(r"(?:how are you|how are you doing|kaise ho|how's it going)")
def how(m, ctx):
    return random.choice(["I'm doing great, thanks for asking! How can I help?", "All good here! What can I do for you?"])


@skill(r"(?:what(?:'s| is) the time(?: now)?|what time is it(?: now)?|time|tell me the time|time kya hai|kitne baje hai)")
def time_(m, ctx):
    return f"It's {datetime.now():%I:%M %p}.".replace("It's 0", "It's ")


@skill(r"(?:what(?:'s| is) (?:the |today'?s )?date(?: today)?|what day is (?:it|today)|today'?s date|date|aaj kya date hai)")
def date_(m, ctx):
    return f"Today is {datetime.now():%A, %d %B %Y}."


@skill(r"(?:(?:what(?:'s| is) (?:the |my )?)?battery(?: status| level| percentage)?|how much battery(?: is left| do i have)?"
       r"|kitni battery hai|(?:is|am) (?:the laptop|it|i) charging)")
def battery(m, ctx):
    b = psutil.sensors_battery()
    if not b:
        return "This computer doesn't have a battery I can read."
    state = "and charging" if b.power_plugged else "and not charging"
    left = ""
    if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        left = f", about {say_duration(b.secsleft - b.secsleft % 60)} left"
    return f"Battery is at {int(b.percent)} percent {state}{left}."


@skill(r"(?:flip|toss) a coin")
def coin(m, ctx):
    return f"It's {random.choice(['heads', 'tails'])}!"


@skill(r"roll (?:a |the )?dice?")
def dice(m, ctx):
    return f"You rolled a {random.randint(1, 6)}."


@skill(r"(stop|start|resume) listening|(?:wake word|mic|microphone) (on|off)")
def listening(m, ctx):
    on = (m.group(1) or m.group(2)).lower() in ("start", "resume", "on")
    return ctx.set_wake(on)


# ===================== Apps, websites, search =====================

@skill(r"(?:open|launch|start|run|kholo|khol do)\s+(.+?)(?:\s+(?:kholo|khol do|open karo))?", fast=False)
def open_app(m, ctx):
    name = m.group(1).strip()
    if re.match(r"(?:https?://|www\.)", name):
        webbrowser.open(name if name.startswith("http") else "https://" + name)
        return "Opening the link."
    hit = apps.find(name)
    if not hit:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(name))
        return f"I couldn't find an app called {name}, so I searched for it."
    label, target = hit
    apps.open_target(target)
    return f"Opening {label}."


@skill(r"(.+?)\s+(?:kholo|khol do|open karo)", fast=False)
def open_hinglish(m, ctx):
    return open_app(m, ctx)


@skill(r"(?:close|quit|exit|kill|band karo)\s+(.+)", fast=False)
def close_app(m, ctx):
    name = m.group(1).strip()
    n = apps.close(name)
    return f"Closed {name}." if n else f"{name.title()} doesn't seem to be open."


@skill(r"(?:search youtube for|youtube search|search on youtube for|search youtube)\s+(.+)|search\s+(?:for\s+)?(.+?)\s+on youtube",
       fast=False)
def youtube_search(m, ctx):
    q = m.group(1) or m.group(2)
    webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
    return f"Searching YouTube for {q}."


@skill(r"(?:search|google|look up|search for|search google for)\s+(?:for\s+)?(.+?)(?:\s+on google)?", fast=False)
def search(m, ctx):
    webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(m.group(1)))
    return f"Here's what I found for {m.group(1)}."


def youtube_top_video(query):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query) + "&sp=EgIQAQ%253D%253D"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
    html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "replace")
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".*?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            return m.group(1), json.loads(f'"{m.group(2)}"')
        except ValueError:
            return m.group(1), m.group(2)
    m = re.search(r'"videoId":"([\w-]{11})"', html)
    return (m.group(1), query) if m else None


@skill(r"play\s+(?!music$|songs?$|video$)(?:the\s+)?(?:song\s+)?(.+?)\s+on spotify", fast=False)
def play_spotify(m, ctx):
    os.startfile("spotify:search:" + urllib.parse.quote(m.group(1)))
    return f"Searching Spotify for {m.group(1)}. Press play on the one you want."


@skill(r"play\s+(?!music$|songs?$|video$)(?:the\s+)?(?:song\s+)?(.+?)(?:\s+(?:on|in)\s+youtube)?|(.+?)\s+(?:bajao|chalao|play karo|laga do)",
       fast=False)
def play(m, ctx):
    query = (m.group(1) or m.group(2)).strip()
    try:
        hit = youtube_top_video(query)
    except OSError:
        hit = None
    if not hit:
        webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
        return f"I opened YouTube results for {query}."
    webbrowser.open(f"https://www.youtube.com/watch?v={hit[0]}")
    return f"Playing {hit[1]} on YouTube."


# ===================== Media, volume, brightness =====================

@skill(r"(play|pause|resume|stop)(?:\s+(?:the\s+)?(?:music|song|video|media|playback))?|(?:play music|gaana (?:chalao|band karo))")
def media(m, ctx):
    word = (m.group(1) or "play").lower()
    press(0xB2 if word == "stop" else 0xB3)
    return {"pause": "Paused.", "stop": "Stopped."}.get(word, "Okay.")


@skill(r"(next|skip|previous|prev|last)(?:\s+(?:song|track|video))?|(?:skip (?:this )?(?:song|track))")
def track(m, ctx):
    word = (m.group(1) or "next").lower()
    press(0xB0 if word in ("next", "skip") else 0xB1)
    return "Next one." if word in ("next", "skip") else "Going back."


@skill(r"(?:turn (?:the )?)?(?:volume|sound)\s+(up|down)(?:\s+a (?:bit|little))?|(?:turn it|make it)\s+(louder|quieter|softer)"
       r"|(louder|quieter)|(?:awaaz|volume)\s+(badhao|kam karo)")
def volume_step(m, ctx):
    word = next(g for g in m.groups() if g).lower()
    up = word in ("up", "louder", "badhao")
    press(0xAF if up else 0xAE, 5)
    return "Volume up." if up else "Volume down."


@skill(r"(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+|at\s+)?(\d{1,3})(?:\s*(?:%|percent))?")
def volume_set(m, ctx):
    level = max(0, min(100, int(m.group(1))))
    press(0xAE, 50)
    press(0xAF, round(level / 2))
    return f"Volume set to {level} percent."


@skill(r"(mute|unmute)(?:\s+(?:the\s+)?(?:sound|volume|audio|laptop))?|(?:volume|sound) (off|on)")
def mute(m, ctx):
    press(0xAD)
    return "Done."


def _ps(script):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                       timeout=20, creationflags=NO_WINDOW)
    return r.stdout.strip()


@skill(r"(?:set\s+)?(?:the\s+)?brightness(?:\s+to)?\s*(up|down|\d{1,3})?(?:\s*(?:%|percent))?|(?:make it|turn)\s+(brighter|darker|dimmer)"
       r"|(?:screen|brightness)\s+(brighter|darker|up|down)")
def brightness(m, ctx):
    arg = next((g for g in m.groups() if g), "")
    cur = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness")
    if not cur or not cur.split()[0].isdigit():
        return "I can't change the brightness of this screen."
    cur = int(cur.split()[0])
    if not arg:
        return f"Brightness is at {cur} percent."
    level = cur + 20 if arg in ("up", "brighter") else cur - 20 if arg in ("down", "darker", "dimmer") else int(arg)
    level = max(0, min(100, level))
    _ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}} | Out-Null")
    return f"Brightness set to {level} percent."


# ===================== Timers, alarms, reminders =====================

@skill(r"(?:set\s+(?:a\s+)?)?timer(?:\s+for)?\s+(.+)|(?:set\s+)?(?:a\s+)?(.+?)\s+timer|(.+?)\s+ka timer(?: lagao| laga do)?")
def timer(m, ctx):
    secs = parse_duration(next(g for g in m.groups() if g))
    if not secs:
        return "For how long? Say something like set a timer for 5 minutes."
    ctx.schedule(datetime.now() + timedelta(seconds=secs), "timer", f"Your {say_duration(secs)} timer is done.")
    return f"Timer set for {say_duration(secs)}."


@skill(r"(?:set\s+(?:an?\s+)?alarm|wake me up|alarm laga do|alarm lagao)(?:\s+(?:for|at))?\s*(.*)")
def alarm(m, ctx):
    text = m.group(1)
    secs = parse_duration(text) if re.search(r"\bin\b|\bafter\b", text) else None
    when = datetime.now() + timedelta(seconds=secs) if secs else parse_clock(text)
    if not when:
        return "What time? Say something like wake me up at 7 am."
    ctx.schedule(when, "alarm", "Wake up! It's " + f"{when:%I:%M %p}".lstrip("0") + ".")
    return f"Alarm set for {say_time(when)}."


@skill(r"remind me (?:to\s+|that\s+|about\s+)?(.+)", fast=False)
def remind(m, ctx):
    text = m.group(1)
    when, what = None, text
    dm = re.search(r"\s+(?:in|after)\s+(.+?)$", text)
    cm = re.search(r"\s+(?:at|by)\s+(.+?)$", text)
    tm = re.search(r"\s+tomorrow(?:\s+(?:at|by)\s+(.+?))?$", text)
    if dm and parse_duration(dm.group(1)):
        when, what = datetime.now() + timedelta(seconds=parse_duration(dm.group(1))), text[:dm.start()]
    elif tm:
        when = parse_clock("tomorrow " + (tm.group(1) or "9 am"))
        what = text[:tm.start()]
    elif cm and parse_clock(cm.group(1)):
        when, what = parse_clock(cm.group(1)), text[:cm.start()]
    if not when:
        def got_time(answer):
            t = clean(answer)
            w = (datetime.now() + timedelta(seconds=parse_duration(t))) if parse_duration(t) else parse_clock(t)
            if not w:
                return "Sorry, I didn't get the time. Try again, like remind me to drink water in 20 minutes."
            ctx.schedule(w, "reminder", f"Reminder: {what}")
            return f"Okay, I'll remind you at {say_time(w)}."
        ctx.pending = got_time
        return "When should I remind you?"
    what = re.sub(r"^(?:to|that)\s+", "", what.strip())
    ctx.schedule(when, "reminder", f"Reminder: {what}")
    return f"Okay, I'll remind you to {what} at {say_time(when)}."


@skill(r"(?:cancel|stop|delete|remove)\s+(?:the\s+|my\s+|all\s+)?(timers?|alarms?|reminders?)")
def cancel_jobs(m, ctx):
    kind_ = m.group(1).rstrip("s")
    n = ctx.cancel_jobs(kind_)
    return f"Cancelled {n} {kind_}{'s' * (n != 1)}." if n else f"You don't have any {kind_}s set."


@skill(r"(?:how much time is left|time left|how long is left)(?: on (?:the|my) timer)?|(?:what are my|list my|show my|any) (alarms|timers|reminders)")
def list_jobs(m, ctx):
    jobs = ctx.list_jobs()
    if not jobs:
        return "You don't have any timers, alarms or reminders."
    out = []
    for j in jobs[:5]:
        left = j["at"] - datetime.now()
        if j["kind"] == "timer":
            out.append(f"a timer with {say_duration(max(1, left.total_seconds()))} left")
        else:
            out.append(f"{'an alarm' if j['kind'] == 'alarm' else j['text'].replace('Reminder: ', 'a reminder to ')} "
                       f"at {say_time(j['at'])}")
    return "You have " + ", ".join(out) + "."


# ===================== Notes, typing, clipboard =====================

def _add_note(text):
    config.NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.NOTES_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%d %b %Y %I:%M %p}] {text.strip()}\n")
    return "Noted."


@skill(r"(?:take|make|write|add)\s+(?:a\s+)?note(?:\s+(?:that|saying|:))?\s*(.*)|note (?:that|down)\s+(.+)|(?:note)\s*", fast=False)
def note(m, ctx):
    text = next((g for g in m.groups() if g), "").strip()
    if text:
        return _add_note(text)
    ctx.pending = lambda answer: _add_note(answer) if clean(answer) not in ("nothing", "cancel", "never mind") else "Okay."
    return "What should the note say?"


@skill(r"(?:read|show|what are)\s+(?:me\s+)?my\s+(?:last\s+)?notes?|open (?:my )?notes")
def read_notes(m, ctx):
    if not config.NOTES_PATH.exists():
        return "You don't have any notes yet. Say take a note."
    lines = [ln for ln in config.NOTES_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if m.group(0).startswith("open"):
        os.startfile(config.NOTES_PATH)
        return "Opening your notes."
    last = [re.sub(r"^\[.*?\]\s*", "", ln) for ln in lines[-3:]]
    ctx.show_text = "\n".join(lines[-8:])
    return "Your latest notes: " + ". ".join(last) + "."


@skill(r"type\s+(.+)", fast=False)
def type_(m, ctx):
    from ctypes import Structure, Union, c_size_t, sizeof

    class KI(Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("extra", c_size_t)]

    class MI(Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("data", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                    ("extra", c_size_t)]

    class U(Union):
        _fields_ = [("ki", KI), ("mi", MI)]

    class INPUT(Structure):
        _fields_ = [("type", wt.DWORD), ("u", U)]
    for ch in m.group(1):
        code = ord(ch)
        arr = (INPUT * 2)(INPUT(1, U(ki=KI(0, code, 4, 0, 0))), INPUT(1, U(ki=KI(0, code, 6, 0, 0))))
        user32.SendInput(2, arr, sizeof(INPUT))
    return "Typed it."


@skill(r"(?:what(?:'s| is) (?:in|on) (?:my |the )?clipboard|read (?:my |the )?clipboard)")
def clipboard(m, ctx):
    text = _ps("Get-Clipboard -Raw")
    if not text:
        return "Your clipboard is empty."
    ctx.show_text = text[:900]
    return "Your clipboard says: " + text[:300]


# ===================== Screen, PC =====================

@skill(r"(?!.*screen\s?(?:shot|time))(?:(?:what(?:'s| is)|explain|read|check|look at|describe|summari[sz]e|solve|see|dekho|batao)\b.*\b"
       r"(?:my|the|this|on)\s+screen\b.*|(?:explain|solve|summari[sz]e|translate|read) this.*|.*screen (?:pe|par) kya hai.*)")
def screen(m, ctx):
    from PIL import Image, ImageGrab
    import io
    img = ImageGrab.grab().convert("RGB")
    if img.width > 1600:
        img = img.resize((1600, round(img.height * 1600 / img.width)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    ctx.thinking()
    return ctx.brain.look(m.group(0), buf.getvalue())


@skill(r"(?:take\s+(?:a\s+)?)?screen\s?shot|screenshot (?:lo|le lo)")
def screenshot(m, ctx):
    from PIL import ImageGrab
    folder = Path.home() / "Pictures" / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Buddy {datetime.now():%Y-%m-%d %H-%M-%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return "Screenshot saved in your Pictures, Screenshots folder."


@skill(r"lock(?:\s+(?:the\s+|my\s+)?(?:pc|laptop|computer|screen))?|lock (?:it|kar do)")
def lock(m, ctx):
    ctx.after_speech(user32.LockWorkStation)
    return "Locking."


@skill(r"(?:go to sleep|sleep mode|put (?:the |my )?(?:pc|laptop|computer) to sleep)")
def sleep(m, ctx):
    return confirm("Should I put the laptop to sleep?", lambda: (ctx.after_speech(lambda: subprocess.Popen(
        ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=NO_WINDOW)), "Good night.")[1], ctx)


@skill(r"(shut ?down|turn off|power off|restart|reboot)(?:\s+(?:the\s+|my\s+)?(?:pc|laptop|computer|system))?(?:\s+now)?")
def power(m, ctx):
    restart = m.group(1).lower() in ("restart", "reboot")
    word = "restart" if restart else "shut down"

    def do():
        subprocess.run(["shutdown", "/r" if restart else "/s", "/t", "10"], creationflags=NO_WINDOW)
        return f"Okay, {'restarting' if restart else 'shutting down'} in 10 seconds. Say cancel shutdown to stop it."
    return confirm(f"Are you sure you want to {word} the laptop?", do, ctx)


@skill(r"(?:cancel|abort|stop)\s+(?:the\s+)?(?:shut ?down|restart)")
def cancel_power(m, ctx):
    r = subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW, capture_output=True)
    return "Cancelled." if r.returncode == 0 else "Nothing was scheduled."


@skill(r"(?:empty|clear|clean)\s+(?:the\s+)?recycle bin")
def recycle(m, ctx):
    def do():
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        return "Recycle bin is empty."
    return confirm("Empty the recycle bin? Those files will be gone for good.", do, ctx)


@skill(r"minimi[sz]e (?:all|everything|all windows)|show (?:the )?desktop")
def desktop(m, ctx):
    user32.keybd_event(0x5B, 0, 0, 0)
    press(0x44)
    user32.keybd_event(0x5B, 0, 2, 0)
    return "Done."


# ===================== Weather =====================

WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "heavy showers",
       95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _place(city):
    if city:
        r = http_json("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(city))
        if r.get("results"):
            p = r["results"][0]
            return p["latitude"], p["longitude"], p["name"]
        return None
    for url in ("https://ipapi.co/json/", "http://ip-api.com/json/"):
        try:
            r = http_json(url, timeout=5)
            lat, lon = r.get("latitude", r.get("lat")), r.get("longitude", r.get("lon"))
            if lat is not None:
                return lat, lon, r.get("city") or "your area"
        except Exception:
            continue
    return None


@skill(r"(?:what(?:'s| is) the |how(?:'s| is) the )?(?:weather|temperature)(?:\s+(?:like|today|now|outside|forecast))?"
       r"(?:\s+(?:in|at|for)\s+(.+?))?(?:\s+(today|tomorrow))?|will it rain(?: today| tomorrow)?(?:\s+in\s+(.+))?"
       r"|(?:mausam|weather) (?:kaisa hai|batao)")
def weather(m, ctx):
    city = next((g for g in (m.groups()[0], m.groups()[2]) if g), None) or config.load().get("city") or None
    tomorrow = "tomorrow" in m.group(0)
    try:
        place = _place(city)
        if not place:
            return f"I couldn't find {city}."
        lat, lon, name = place
        w = http_json(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                      "&current=temperature_2m,apparent_temperature,weather_code"
                      "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"
                      "&timezone=auto&forecast_days=2")
    except Exception:
        return "I can't get the weather right now. Check your internet."
    d = w["daily"]
    i = 1 if tomorrow else 0
    hi, lo, rain = round(d["temperature_2m_max"][i]), round(d["temperature_2m_min"][i]), d["precipitation_probability_max"][i]
    rain_txt = f" Chance of rain is {rain} percent." if rain is not None else ""
    if tomorrow:
        return f"Tomorrow in {name}: {WMO.get(d['weather_code'][1], 'mixed weather')}, between {lo} and {hi} degrees.{rain_txt}"
    c = w["current"]
    return (f"It's {round(c['temperature_2m'])} degrees and {WMO.get(c['weather_code'], 'mixed weather')} in {name}, "
            f"feels like {round(c['apparent_temperature'])}. Today's high is {hi}.{rain_txt}")


# ===================== Maths =====================

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        if isinstance(node.op, ast.Pow) and abs(_eval(node.right)) > 100:
            raise ValueError
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError


@skill(r"(?:what(?:'s| is)|calculate|how much is|compute)\s+([\d\s.+\-*/x()%^]+|.*\d.*(?:plus|minus|times|into|multiplied|divided|over|"
       r"percent|squared|cubed|square root|power).*)")
def maths(m, ctx):
    e = m.group(1).lower()
    sq = re.fullmatch(r"(?:the\s+)?square root of\s+([\d.]+)", e.strip())
    if sq:
        v = float(sq.group(1)) ** 0.5
    else:
        e = re.sub(r"([\d.]+)\s*(?:%|percent)\s+of\s+([\d.]+)", r"(\1/100*\2)", e)
        for a, b in [("multiplied by", "*"), ("divided by", "/"), ("to the power of", "**"), ("plus", "+"),
                     ("minus", "-"), ("times", "*"), ("into", "*"), ("over", "/"), (" x ", " * "), ("^", "**"),
                     ("squared", "**2"), ("cubed", "**3")]:
            e = e.replace(a, b)
        if not re.fullmatch(r"[\d\s.+\-*/()]+", e) or not re.search(r"\d", e):
            return None
        try:
            v = _eval(ast.parse(e.strip(), mode="eval").body)
        except Exception:
            return None
    v = round(v, 4)
    v = int(v) if float(v).is_integer() else v
    return f"It's {v:,}." if isinstance(v, int) else f"It's {v}."
