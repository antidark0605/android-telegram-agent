"""Parse /tts actions without executing an AGY task."""
def parse_tts(raw):
    text = raw.strip()
    if not text:
        return "on", None
    if text.lower() in ("off", "status"):
        return text.lower(), None
    if len(text) > 500 or any(ord(c) < 32 and c not in "\n\t" for c in text):
        return "invalid", None
    return "speak", text
