"""Link checker helpers for the docs site (toy example code)."""
import re

LINK = re.compile(r"\]\(([^)]+)\)")


def normalize_href(href, base=""):
    # drop the fragment and a trailing slash before comparing
    href = href.split("#", 1)[0].rstrip("/")
    if href.startswith("/"):
        return href
    return (base.rstrip("/") + "/" + href).lstrip("/") if base else href


class LinkReport:
    def __init__(self):
        self.broken = []

    def add(self, page, href):
        self.broken.append((page, href))

    def failed(self):
        return bool(self.broken)


def find_broken_links(pages):
    """pages maps a page name to its markdown text."""
    known = {normalize_href(p) for p in pages}
    report = LinkReport()
    for name, text in pages.items():
        for href in LINK.findall(text):
            if href.startswith("http"):
                continue
            if normalize_href(href) not in known:
                report.add(name, href)
    return report
