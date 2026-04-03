"""Text cleaning utilities.

Provides :func:`strip_ansi` for removing ANSI escape sequences (colors,
cursor movement, etc.) from strings returned by Ansible runner output.
"""

import re

ANSI_RE = re.compile(r"(?:\x1B[@-_][0-?]*[ -/]*[@-~])")


def strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences from a string.

    Strips terminal color codes, cursor positioning, and other control
    sequences so that log output is safe for plain-text storage and
    JSON serialization.

    :param s: Input string potentially containing ANSI escape sequences.
    :type s: str
    :returns: The input string with all ANSI sequences removed.
    :rtype: str
    """
    return ANSI_RE.sub("", s)
