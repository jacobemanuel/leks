"""Shelf packer for atlas pages (toy example code)."""

PAGE_SIZE = 2048
PADDING = 2


def shelf_height(rects):
    # a shelf is as tall as its tallest rectangle
    return max((h for _, h in rects), default=0)


class AtlasPacker:
    def __init__(self, page_size=PAGE_SIZE):
        self.page_size = page_size
        self.pages = []
        self.new_page()

    def new_page(self):
        self.pages.append({"x": 0, "y": 0, "shelf": 0, "placed": []})

    def pack_rect(self, w, h):
        """Place one rectangle, return (page, x, y)."""
        w, h = w + PADDING, h + PADDING
        page = self.pages[-1]
        if page["x"] + w > self.page_size:
            # row is full, start the next shelf
            page["y"] += page["shelf"]
            page["x"], page["shelf"] = 0, 0
        if page["y"] + h > self.page_size:
            self.new_page()
            page = self.pages[-1]
        x, y = page["x"], page["y"]
        page["x"] += w
        page["shelf"] = max(page["shelf"], h)
        page["placed"].append((x, y, w, h))
        return len(self.pages) - 1, x, y
