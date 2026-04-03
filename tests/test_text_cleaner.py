"""Tests for app.utils.text_cleaner."""

from app.utils.text_cleaner import strip_ansi


class TestStripAnsi:
    """Tests for strip_ansi()."""

    def test_removes_basic_color_codes(self):
        assert strip_ansi("\x1b[32mok\x1b[0m") == "ok"

    def test_removes_bold_and_reset(self):
        assert strip_ansi("\x1b[1mBOLD\x1b[0m text") == "BOLD text"

    def test_removes_multiple_colors(self):
        s = "\x1b[31mred\x1b[0m \x1b[32mgreen\x1b[0m \x1b[34mblue\x1b[0m"
        assert strip_ansi(s) == "red green blue"

    def test_preserves_plain_text(self):
        assert strip_ansi("no escape codes here") == "no escape codes here"

    def test_handles_empty_string(self):
        assert strip_ansi("") == ""

    def test_removes_cursor_movement(self):
        assert strip_ansi("\x1b[2J\x1b[H") == ""

    def test_ansible_recap_line(self):
        line = "\x1b[0;32mok=1\x1b[0m \x1b[0;33mchanged=0\x1b[0m \x1b[0;31mfailed=0\x1b[0m"
        assert strip_ansi(line) == "ok=1 changed=0 failed=0"

    def test_preserves_newlines(self):
        s = "\x1b[32mline1\x1b[0m\n\x1b[31mline2\x1b[0m"
        assert strip_ansi(s) == "line1\nline2"
