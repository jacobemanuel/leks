"""Tiny pdf writer for tests: stdlib only, a real text layer, correct xref offsets."""


def _esc(line: str) -> bytes:
    b = line.encode('cp1252', 'replace')
    return b.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


def _stream(text: str) -> bytes:
    lines = text.split('\n')
    # shrink the leading when a page has many lines, so everything stays on the sheet
    lead = min(14.0, 760.0 / max(1, len(lines)))
    out = [b'BT', b'/F1 11 Tf']
    y = 800.0
    for line in lines:
        if line:
            out.append(b'1 0 0 1 56 %.2f Tm (' % y + _esc(line) + b') Tj')
        y -= lead
    out.append(b'ET')
    return b'\n'.join(out) + b'\n'


def write_pdf(path: str, pages: list) -> None:
    """Write a pdf with one sheet per entry of pages (Helvetica, one text object per line)."""
    if not pages:
        pages = ['']
    n = len(pages)
    # object numbers: 1 catalog, 2 page tree, 3 font, then page and content per sheet
    objs = {}
    kids = []
    for i, text in enumerate(pages):
        pg, ct = 4 + 2 * i, 5 + 2 * i
        kids.append(b'%d 0 R' % pg)
        data = _stream(text)
        objs[pg] = (b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
                    b'/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % ct)
        objs[ct] = b'<< /Length %d >>\nstream\n' % len(data) + data + b'endstream'
    objs[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objs[2] = b'<< /Type /Pages /Kids [' + b' '.join(kids) + b'] /Count %d >>' % n
    objs[3] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'
    out = bytearray(b'%PDF-1.4\n%\xff\xfe\xfd\xfc\n')
    offs = {}
    for num in sorted(objs):
        offs[num] = len(out)
        out += b'%d 0 obj\n' % num + objs[num] + b'\nendobj\n'
    start = len(out)
    size = max(objs) + 1
    out += b'xref\n0 %d\n' % size
    out += b'0000000000 65535 f \n'
    for num in range(1, size):
        out += b'%010d 00000 n \n' % offs[num]
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (size, start)
    with open(path, 'wb') as f:
        f.write(bytes(out))
