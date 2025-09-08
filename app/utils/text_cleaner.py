import re

ANSI_RE = re.compile(r'(?:\x1B[@-_][0-?]*[ -/]*[@-~])')
# ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def strip_ansi(s: str) -> str:
    """ delete ansi sequences (colors ect) from string """
    return ANSI_RE.sub("", s)

