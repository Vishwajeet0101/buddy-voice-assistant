"""The AI brain - free tiers only, plain HTTPS (no big SDKs, so Buddy starts fast).

Groq  (console.groq.com/keys)        - fastest answers (~0.3 s) and fast speech-to-text (Whisper)
Gemini (aistudio.google.com/apikey)  - backup answers, speech-to-text, and it can look at your screen
Either key is enough. With both, Groq answers and Gemini is used when Groq fails or for the screen.
"""
import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

import config

log = config.log
GROQ = "https://api.groq.com/openai/v1"
GROQ_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-4-maverick-17b-128e-instruct",
               "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
GROQ_STT = ["whisper-large-v3-turbo", "whisper-large-v3"]
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
SWITCH = (404, 408, 429, 500, 502, 503, 504)


class AIError(Exception):
    def __init__(self, friendly, code=None):
        super().__init__(friendly)
        self.friendly = friendly
        self.code = code


def _friendly(e, who):
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        log.warning("%s HTTP %s: %s", who, e.code, body)
        if e.code in (400, 401, 403) and ("key" in body.lower() or e.code != 400):
            return AIError(f"Your {who} key doesn't work. Check it in Buddy's settings.", e.code)
        if e.code == 429:
            return AIError(f"The free {who} limit is used up for now. Try again in a little while.", e.code)
        return AIError(f"{who} is busy right now. Try again in a moment.", e.code)
    if isinstance(e, AIError):
        return e
    log.warning("%s error: %r", who, e)
    return AIError("I can't reach the internet right now.")


def _post(url, body, headers, timeout=30, stream=False):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    h = {"Content-Type": "application/json", "User-Agent": "Buddy-Voice-Assistant"}
    h.update(headers)
    resp = urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout)
    return resp if stream else json.loads(resp.read().decode("utf-8"))


def _sse(resp):
    """Yields the JSON objects of a server-sent-events stream."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except ValueError:
            continue


class Brain:
    def __init__(self):
        self.history = []          # [{"role": "user"/"assistant", "text": ...}]
        self.last_time = 0
        self.groq_model = 0
        self.gemini_model = 0

    def keys(self):
        s = config.load()
        return s.get("groq_key", "").strip(), s.get("gemini_key", "").strip()

    def has_ai(self):
        return any(self.keys())

    def system_prompt(self):
        s = config.load()
        me = s.get("assistant_name") or "Buddy"
        user = s.get("user_name") or "the user"
        return (f"You are {me}, a friendly voice assistant (like Siri) on {user}'s Windows laptop. "
                "Your words are spoken aloud: answer in 1-3 short, natural sentences unless asked for more detail. "
                "No markdown, lists, emojis or symbols. Reply in the same language style the user speaks "
                "(English -> English, Hinglish -> Hinglish in English letters, Hindi -> Hindi). "
                f"Now it is {datetime.now():%A %d %B %Y, %I:%M %p}. "
                "\n\nIMPORTANT - laptop actions: if the user wants the laptop to DO something (in any language or wording, "
                "including Hinglish or Hindi), reply with ONLY one line:  CMD: <the command in simple English>  - "
                "nothing else. Use one of these command styles:\n"
                "open chrome | close chrome | close this window | minimize this window | switch window | new tab | "
                "close tab | next tab | scroll down | scroll up | go back | refresh | zoom in | copy | paste | undo | "
                "select all | save | turn on bluetooth | turn off wifi | dark mode on | set volume to 40 | volume up | "
                "mute | brightness 70 | play kesariya on youtube | play music | pause | next song | "
                "set a timer for 5 minutes | wake me up at 7 am | remind me to call mom at 6 pm | take a note buy milk | "
                "send whatsapp to rahul saying i am coming | call mom on whatsapp | open whatsapp chat with rahul | "
                "open my resume pdf | open last downloaded file | what's on my screen | take a screenshot | lock | "
                "weather | battery | good morning | start study mode | search java streams on google\n"
                "Examples: 'bhai bluetooth band kar de' -> CMD: turn off bluetooth ; 'thoda awaaz badha do' -> "
                "CMD: volume up ; 'Rahul ko bolo main aa raha hu' -> CMD: send whatsapp to rahul saying main aa raha hu ; "
                "'screen thodi bright karo' -> CMD: brightness up ; 'isko band karo' -> CMD: close this window\n"
                "If it is a question or chat, just answer normally (never start with CMD then). Never pretend you "
                "did an action yourself.")

    # ----- chat -----

    def ask_stream(self, text):
        """Yields the answer in pieces as it is written."""
        if time.time() - self.last_time > 600:        # new conversation after 10 quiet minutes
            self.history = []
        self.last_time = time.time()
        groq, gemini = self.keys()
        if not (groq or gemini):
            raise AIError("To answer questions I need a free AI key. Open Buddy's settings from the tray icon and paste one.")
        self.history.append({"role": "user", "text": text})
        parts, error = [], None
        for name, fn, key in (("Groq", self._groq_stream, groq), ("Gemini", self._gemini_stream, gemini)):
            if not key:
                continue
            try:
                for piece in fn(key):
                    parts.append(piece)
                    yield piece
                error = None
                break
            except Exception as e:
                error = _friendly(e, name)
                if parts:                     # already said something - don't start over with another AI
                    break
        if parts:
            self.history.append({"role": "assistant", "text": "".join(parts)})
            del self.history[:-16]
        else:
            self.history.pop()
            raise error or AIError("I didn't get an answer. Try asking another way.")

    def ask(self, text):
        return "".join(self.ask_stream(text))

    def _groq_models(self, key):
        """Groq changes its model list now and then - ask which chat models exist (once)."""
        if getattr(self, "_groq_list", None):
            return self._groq_list
        try:
            req = urllib.request.Request(f"{GROQ}/models", headers={"Authorization": f"Bearer {key}",
                                                                     "User-Agent": "Buddy-Voice-Assistant"})
            ids = [m["id"] for m in json.loads(urllib.request.urlopen(req, timeout=10).read()).get("data", [])]
            chat = [i for i in ids if not any(x in i.lower() for x in ("whisper", "guard", "tts", "playai", "orpheus",
                                                                       "prompt", "compound", "safeguard"))]
            pick = [m for m in GROQ_MODELS if m in chat] + sorted(
                [m for m in chat if m not in GROQ_MODELS and "allam" not in m], key=lambda m: "llama" not in m)
            if pick:
                self._groq_list = pick[:4]
                log.info("groq models: %s", self._groq_list)
                return self._groq_list
        except Exception as e:
            log.warning("groq model list failed: %r", e)
        return GROQ_MODELS

    def _groq_stream(self, key):
        msgs = [{"role": "system", "content": self.system_prompt()}] + [
            {"role": h["role"], "content": h["text"]} for h in self.history]
        last = None
        models = self._groq_models(key)
        for i in range(len(models)):
            model = models[(self.groq_model + i) % len(models)]
            body = {"model": model, "messages": msgs, "stream": True, "max_tokens": 800, "temperature": 0.6}
            if "gpt-oss" in model:          # "thinking" models: keep the thinking short so answers start fast
                body.update(reasoning_effort="low", include_reasoning=False)
            elif "qwen3" in model:
                body.update(reasoning_effort="none")
            try:
                try:
                    resp = _post(f"{GROQ}/chat/completions", body, {"Authorization": f"Bearer {key}"}, stream=True)
                except urllib.error.HTTPError as e:
                    if e.code != 400 or len(body) == 5:
                        raise
                    for k in ("reasoning_effort", "include_reasoning"):     # option not supported - try plain
                        body.pop(k, None)
                    resp = _post(f"{GROQ}/chat/completions", body, {"Authorization": f"Bearer {key}"}, stream=True)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in SWITCH:
                    continue
                raise
            self.groq_model = (self.groq_model + i) % len(models)
            for obj in _sse(resp):
                piece = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if piece:
                    yield piece
            return
        raise last

    def _gemini_body(self, contents):
        return {"systemInstruction": {"parts": [{"text": self.system_prompt()}]}, "contents": contents,
                "generationConfig": {"maxOutputTokens": 700, "temperature": 0.6}}

    def _gemini_stream(self, key):
        contents = [{"role": "model" if h["role"] == "assistant" else "user", "parts": [{"text": h["text"]}]}
                    for h in self.history]
        last = None
        for i in range(len(GEMINI_MODELS)):
            model = GEMINI_MODELS[(self.gemini_model + i) % len(GEMINI_MODELS)]
            try:
                resp = _post(f"{GEMINI}/{model}:streamGenerateContent?alt=sse", self._gemini_body(contents),
                             {"x-goog-api-key": key}, stream=True)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in SWITCH:
                    continue
                raise
            self.gemini_model = (self.gemini_model + i) % len(GEMINI_MODELS)
            for obj in _sse(resp):
                for c in obj.get("candidates") or []:
                    for p in (c.get("content") or {}).get("parts") or []:
                        if p.get("text"):
                            yield p["text"]
            return
        raise last

    def _gemini_once(self, key, parts):
        last = None
        for i in range(len(GEMINI_MODELS)):
            model = GEMINI_MODELS[(self.gemini_model + i) % len(GEMINI_MODELS)]
            try:
                r = _post(f"{GEMINI}/{model}:generateContent", self._gemini_body([{"role": "user", "parts": parts}]),
                          {"x-goog-api-key": key}, timeout=45)
            except urllib.error.HTTPError as e:
                last = e
                if e.code in SWITCH:
                    continue
                raise
            return "".join(p.get("text", "") for c in r.get("candidates") or []
                           for p in (c.get("content") or {}).get("parts") or []).strip()
        raise last

    # ----- speech-to-text -----

    def transcribe(self, wav):
        """Speech -> text with an online model (better for Hindi/Hinglish and names). '' if nobody spoke."""
        groq, gemini = self.keys()
        if groq:
            try:
                return self._groq_stt(groq, wav)
            except Exception as e:
                log.warning("groq speech-to-text failed: %r", e)
        if gemini:
            try:
                prompt = ("Transcribe this voice command exactly as spoken. It may be English, Hindi or Hinglish - "
                          "write Hindi words in English letters. Reply with ONLY the transcript, or nothing if no one speaks.")
                return self._gemini_once(gemini, [{"inline_data": {"mime_type": "audio/wav",
                                                                    "data": base64.b64encode(wav).decode()}},
                                                   {"text": prompt}])
            except Exception as e:
                raise _friendly(e, "Gemini")
        raise AIError("no key")

    def _groq_stt(self, key, wav):
        boundary = uuid.uuid4().hex
        last = None
        for model in GROQ_STT:
            fields = {"model": model, "response_format": "json", "temperature": "0",
                      "prompt": "Hey Buddy, open Chrome. Bluetooth band karo. Play Kesariya on YouTube."}
            body = b""
            for k, v in fields.items():
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.wav"\r\n'
                     f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
            try:
                r = _post(f"{GROQ}/audio/transcriptions", body,
                          {"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
                          timeout=20)
                return (r.get("text") or "").strip()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in SWITCH:
                    continue
                raise
        raise last

    # ----- looking at the screen -----

    def look(self, question, jpeg):
        _, gemini = self.keys()
        if not gemini:
            return "To look at your screen I need a free Gemini key. Add one in Buddy's settings."
        prompt = (f"This is a screenshot of my laptop screen. My request: {question}. "
                  "Ignore the small round assistant popup in the bottom-right corner. Answer briefly, in plain spoken sentences.")
        try:
            return self._gemini_once(gemini, [{"inline_data": {"mime_type": "image/jpeg",
                                                                "data": base64.b64encode(jpeg).decode()}},
                                               {"text": prompt}]) or "I couldn't make sense of the screen."
        except Exception as e:
            return _friendly(e, "Gemini").friendly

    def test(self):
        """Returns a list of (provider, ok, message) for the settings page."""
        out = []
        groq, gemini = self.keys()
        saved = self.history
        for name, fn, key in (("Groq", self._groq_stream, groq), ("Gemini", self._gemini_stream, gemini)):
            if not key:
                continue
            self.history = [{"role": "user", "text": "Say hi in five words."}]
            t = time.time()
            try:
                reply = "".join(fn(key)).strip()
                out.append((name, True, f"works ({time.time() - t:.1f}s): {reply[:60]}"))
            except Exception as e:
                out.append((name, False, _friendly(e, name).friendly))
        self.history = saved
        return out
