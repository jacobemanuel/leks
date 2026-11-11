"""One short word per result: what kind of source it is. Mechanical, no model."""
from __future__ import annotations

# strongest first. rule, code, history, document and image sit outside the order on purpose
TRUST = ('frozen', 'decision', 'result', 'state', 'note')

_BY_KIND = ('state', 'rule', 'code', 'decision', 'result', 'document', 'image')


def of(kind: str, status: str, status_src: str, newer: bool) -> str:
    """Source class from kind, status, where the status came from, and whether a newer version exists."""
    if status_src == 'frozen' and not newer and status not in ('superseded', 'stale'):
        return 'frozen'
    if newer or status in ('superseded', 'stale') or (status == 'historical-record' and status_src == 'path'):
        return 'history'
    if kind in _BY_KIND:
        return kind
    return 'note'
