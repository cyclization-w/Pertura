"""A report as its reader opens it: report.md and, beside it, the evidence file it links into (docs/adr/0047, B7)."""
import os


def report_text(path):
    """The report's text, then its evidence file's."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    beside = os.path.join(os.path.dirname(path), "evidence.md")
    if os.path.exists(beside):
        with open(beside, encoding="utf-8") as fh:
            text += "\n" + fh.read()
    return text
