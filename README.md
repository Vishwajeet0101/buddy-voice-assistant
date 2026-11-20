<p align="center">
  <img src="docs/icon.png" width="110" alt="Buddy icon">
</p>

<h1 align="center">Buddy — a free voice assistant for Windows</h1>

<p align="center">
  Say <b>“Hey Buddy”</b>, and talk. Like Siri, but for your Windows laptop, and 100% free.<br>
  Opens apps · timers & alarms · weather · music · notes · reads your screen · answers anything
</p>

<p align="center">
  <a href="../../releases/latest"><b>⬇ Download for Windows</b></a> ·
  <a href="#-install-2-minutes">Install</a> ·
  <a href="#-what-you-can-say">What you can say</a> ·
  <a href="#-faq">FAQ</a>
</p>

<p align="center">
  <img src="docs/demo.gif" width="720" alt="Buddy demo">
</p>

---

## ✨ Features

| | |
|---|---|
| 🎙️ **Wake word** | Say *“Hey Buddy”* from anywhere. Works **offline**, wakes up while you're still talking. |
| 💬 **Talks back** | Natural Indian-English voices (male or female). Says *“Hi there!”* when you call it. |
| ⚡ **Fast** | Laptop commands run **offline in under a second**. Answers start speaking before the AI has finished. |
| 🔁 **Conversations** | Ask a follow-up without saying the name again. *“Who invented the telephone?” … “Where was he born?”* |
| 🖥️ **Controls your laptop** | Opens **any installed app**, websites, folders · volume · brightness · music · lock · shut down … |
| ⏰ **Timers, alarms, reminders** | *“Wake me up at 7”*, *“Remind me to call mom at 6”*. Even remembered after a restart. |
| 👀 **Sees your screen** | *“What's on my screen?”*, *“Solve this question”*, *“Explain this code”* |
| 🇮🇳 **Hinglish** | *“Chrome kholo”*, *“Kesariya bajao”*, *“Battery kitni hai?”* — replies in the same style. |
| ✋ **Interrupt it** | Say *“stop”* (or *“ruko”*, *“bas”*) while it's talking, or *“Hey Buddy …”* to ask something new. |
| 🗣️ **Only your voice** | Train it once (3 sentences) and other people, the TV or YouTube can't wake it. |
| 🧠 **Understands any wording** | *“bhai bluetooth band kar de”*, *“thoda awaaz badha do”*, Hindi script: the AI turns it into the right command. |
| 💬 **WhatsApp** | *“Send WhatsApp to Rahul saying I'm coming”*, *“call Mom on WhatsApp”*. Always asks before sending. |
| 🪟 **Smart PC control** | New tab · scroll down · close this window · switch to Chrome · copy/paste/undo · open my resume PDF · open last download |
| ☀️ **Briefing & routines** | *“Good morning”* → weather, reminders, news. *“Start study mode”* → your own list of actions. |
| 🔁 **Always on** | Starts with Windows, keeps working after sleep. Nothing to click. |
| 🔒 **Private & safe** | Listening for the name happens on your laptop. API keys are encrypted with Windows. Pauses during calls and when locked. |
| 💸 **Free** | No subscription. Uses the free tiers of Groq / Google Gemini (no credit card). |
| ✏️ **Your own name** | Call it Jarvis, Nova, Friday, Max… whatever you like. |

## 📥 Install (2 minutes)

1. **Download** `Buddy-x.x.x-windows.zip` from the [**latest release**](../../releases/latest).
2. **Unzip** it somewhere you'll keep it (for example `Documents\Buddy`). Don't run it from inside the ZIP.
3. Double-click **`Start Buddy`**.
   > If Windows says *“Windows protected your PC”*, click **More info → Run anyway**. Buddy is open source, and Windows shows this for any new app that isn't signed.
4. The settings window opens. Paste a **free AI key** (see below), choose a name and voice, and click **Save**.
5. Say **“Hey Buddy”** 🎉

Buddy starts with Windows and lives in the tray (bottom-right, maybe under the `^` arrow). A **Buddy** shortcut is added to your Desktop.

**Needs:** Windows 10 or 11 (64-bit), a microphone, and internet for the voice and AI answers.
Nothing else to install: Python and everything else are inside the ZIP.

### 🔑 Get a free AI key (1 minute)

Laptop commands work without a key. For questions and conversations, add **one** of these (both are free, no card):

| Key | Why | Get it |
|---|---|---|
| **Groq** (recommended) | Fastest answers (~0.3 s) and the best Hinglish understanding | [console.groq.com/keys](https://console.groq.com/keys) → sign in with Google → **Create API Key** |
| **Gemini** | Can also **look at your screen** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → **Create API key** |

Add both to get Groq's speed plus Gemini's screen reading. Click **Test AI** in settings to check them.

## 🗣️ What you can say

Start with **“Hey Buddy”**, then wait for the reply, or say it all in one go: *“Hey Buddy, open Chrome”*.
**Ctrl + Alt + B** talks to Buddy without the name. **Esc** or a click on the popup stops it talking.

| Category | Try saying |
|---|---|
| Apps & web | open Chrome · open WhatsApp · open Downloads · open YouTube · close Spotify · Chrome kholo |
| Search & music | search Java streams · play Believer on YouTube · Kesariya bajao · play Shape of You on Spotify · pause · next song |
| Sound & screen | volume up · set volume to 40 · mute · brightness 70 · show desktop · take a screenshot |
| Time | set a timer for 10 minutes · wake me up at 6:30 am · remind me to drink water in 20 minutes · what are my alarms · cancel timer |
| Info | what's the time · what's the date · battery · what's the weather · weather in Mumbai tomorrow · what is 15 percent of 200 |
| Notes & typing | take a note buy milk · read my notes · type hello world · what's in my clipboard |
| Your screen | what's on my screen · explain this code · solve this question · summarize this page |
| Laptop | lock · go to sleep · shut down · restart · empty the recycle bin *(Buddy asks before doing these)* |
| Windows & tabs | new tab · close tab · next tab · scroll down · go back · refresh · zoom in · close this window · minimize · switch to Chrome · copy · paste · undo · save |
| Files | open my resume PDF · find my DBMS assignment · open last downloaded file |
| WhatsApp | send WhatsApp to Rahul saying I'm coming · Rahul ko message karo ki main aa raha hu · open chat with Mom · call Mom on WhatsApp |
| Wireless & look | turn off Bluetooth · Wi-Fi on · airplane mode on · dark mode on |
| Day & routines | good morning · what's the news · start study mode · movie mode · work mode *(edit them in settings)* |
| While it talks | stop · ruko · bas · Hey Buddy, *new question* |
| Anything else | who won the last World Cup? · explain recursion simply · give me a quick dinner idea · tell me a joke |
| Buddy | stop listening · start listening · what can you do · thank you |

## ⚡ Why it's fast

```
"Hey Buddy" ─► offline wake word ─► chime + popup + "Hi there!"   (greetings are pre-made: instant)
     │
 you talk ─► offline speech-to-text (words appear live) ─► 1 s of silence = done
     │
 laptop command? ── yes ─► done on the laptop (no internet, < 1 s)
     │ no
     └─► AI (Groq ≈ 0.3 s, Gemini backup) ─► speaks sentence 1 while sentence 2 is still being written
```

- Unsure offline text (Hinglish, song names…) is double-checked with Groq Whisper speech-to-text.
- Short replies (*“Volume up.”*, *“Opening Chrome.”*) are cached, so they play instantly the next time.
- In a quiet room the recogniser rests. Idle CPU is about 2% of one core.

## ❓ FAQ

<details><summary><b>It doesn't hear me</b></summary>

- Is the tray icon there? Right-click it → make sure **Listen for “Hey Buddy”** is ticked.
- Windows **Settings → Privacy & security → Microphone**: turn on *Microphone access* and *Let desktop apps access your microphone*.
- Check the status line at the top of Buddy's settings. It says if Buddy is paused (for example during a call).
- Say it clearly, with a tiny pause: *“Hey Buddy … what's the time”*.
</details>

<details><summary><b>It hears me but doesn't speak</b></summary>

Check your volume and output device. Click **▶ Test voice** in settings. Without internet, Buddy uses the built-in Windows voice. Right-click the tray icon → **Open log file** shows what happened.
</details>

<details><summary><b>How do WhatsApp messages work?</b></summary>

Buddy uses the WhatsApp Desktop app on your laptop (install it from the Microsoft Store and log in once).
For people you message often, add them in settings under **WhatsApp contacts** (`Mom = +91 98765 43210`). Buddy then opens the right chat directly and asks *“Send … to Mom?”* before sending.
For anyone else, Buddy searches WhatsApp for the name, types the message, and waits for you to check it's the right person and say **“send”**.
</details>

<details><summary><b>Is my API key safe?</b></summary>

Keys are stored only on your laptop, encrypted with Windows (DPAPI), so a copied settings file is useless on another computer or account. They're never uploaded anywhere except to Groq/Gemini to answer you. Keys are free (no card), so a leaked key can't cost money. You can delete and recreate one anytime on the Groq/Gemini website.
On the free Gemini tier Google may use requests to improve its products, so don't tell Buddy passwords or OTPs, or ask it to read your screen while banking.
</details>

<details><summary><b>Can I change the name?</b></summary>

Yes. In settings, type a new name (one or two common English words, like *Jarvis*, *Nova*, *Friday*, *Max*) and Save. The wake word changes immediately.
</details>

<details><summary><b>Is it always recording me?</b></summary>

The microphone is checked **on your laptop only** for the name. Nothing is sent anywhere until you say *“Hey Buddy”*. After that, the sentence you said may be sent to Groq/Gemini to understand it and answer. Buddy pauses while another app uses the mic (Zoom, Meet, Discord…) and while the laptop is locked.
</details>

<details><summary><b>Does it cost anything?</b></summary>

No. Groq and Gemini have free tiers that are more than enough for personal use. If you hit a limit, Buddy says so, and it resets after a while.
</details>

<details><summary><b>Mac / Linux?</b></summary>

Windows only for now (it uses Windows features for apps, volume, brightness and the popup).
</details>

<details><summary><b>Uninstall</b></summary>

Right-click the tray icon → Quit. Delete the Buddy folder, the Desktop shortcut, and `%APPDATA%\Buddy` (settings).
To stop it starting with Windows, turn off the switch in settings first, or delete `Buddy.lnk` from `shell:startup`.
</details>

## 🛠️ Run from source

```bash
git clone https://github.com/Vishwajeet0101/buddy-voice-assistant
cd buddy-voice-assistant
pip install -r requirements.txt
pythonw app/buddy.pyw          # the offline speech model (~40 MB) downloads on first run
```

Build the portable ZIP yourself: `python build_release.py` → `dist/Buddy-x.x.x-windows.zip`.

| File | What it does |
|---|---|
| `app/buddy.pyw` | Tray icon, hotkey, settings window, start-up |
| `app/listener.py` | Wake word, listening, conversations, timers & alarms |
| `app/skills.py` | Everything Buddy can do on the laptop (add your own here) |
| `app/brain.py` | AI answers, speech-to-text and screen reading (Groq / Gemini over plain HTTPS) |
| `app/speech.py` | Natural voice (edge-tts) with Windows-voice fallback, sentence-by-sentence |
| `app/orb.py` | The glowing popup |
| `app/apps.py` | Finds any installed app from the Start menu |

**Add a skill** in `app/skills.py`:

```python
@skill(r"(?:say|tell me) good morning")
def good_morning(m, ctx):
    return "Good morning! Have a great day."
```

## 🙏 Built with

[Vosk](https://alphacephei.com/vosk/) (offline speech recognition) ·
[edge-tts](https://github.com/rany2/edge-tts) (voices) ·
[Groq](https://groq.com) & [Google Gemini](https://ai.google.dev) (AI) ·
[Open-Meteo](https://open-meteo.com) (weather) ·
[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) · [pystray](https://github.com/moses-palmer/pystray)

## 📄 License

[MIT](LICENSE). Free to use, change and share.
Buddy is an independent project and is not affiliated with Apple or Siri.
