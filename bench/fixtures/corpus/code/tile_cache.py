"""Tile cache with least recently used eviction (toy example code)."""
from collections import OrderedDict


def tile_bytes(tile):
    # raw size of one decoded tile
    return len(tile)


class TileCache:
    """Holds decoded tiles up to a byte budget."""

    def __init__(self, capacity_bytes, headroom=0.10):
        self.capacity = capacity_bytes
        self.headroom = headroom
        self.used = 0
        self.tiles = OrderedDict()
        self.pinned = set()

    def get(self, key):
        tile = self.tiles.get(key)
        if tile is not None:
            self.tiles.move_to_end(key)  # most recent goes last
        return tile

    def put(self, key, tile):
        if key in self.tiles:
            self.used -= tile_bytes(self.tiles[key])
        self.tiles[key] = tile
        self.tiles.move_to_end(key)
        self.used += tile_bytes(tile)
        if self.used > self.capacity:
            self.evict_oldest()

    def pin(self, keys):
        self.pinned = set(keys)

    def evict_oldest(self):
        """Drop the least recently used tiles until the headroom is free."""
        target = self.capacity * (1.0 - self.headroom)
        for key in list(self.tiles):
            if self.used <= target:
                break
            if key in self.pinned:
                continue
            self.used -= tile_bytes(self.tiles.pop(key))
