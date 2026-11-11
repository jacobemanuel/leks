"""Frame graph: passes as nodes, resources as edges (toy example code)."""


class FramePass:
    def __init__(self, name, reads=(), writes=()):
        self.name = name
        self.reads = set(reads)
        self.writes = set(writes)


class FrameGraph:
    def __init__(self):
        self.passes = []

    def add_pass(self, name, reads=(), writes=()):
        self.passes.append(FramePass(name, reads, writes))

    def cull_unused(self, outputs):
        """Keep only passes that feed one of the final outputs."""
        needed = set(outputs)
        keep = []
        for p in reversed(self.passes):
            if p.writes & needed:
                keep.append(p)
                needed |= p.reads
        self.passes = list(reversed(keep))

    def topological_order(self):
        """Sort passes so every pass runs after the passes it reads from."""
        writers = {}
        for p in self.passes:
            for res in p.writes:
                writers[res] = p
        done, order = set(), []

        def visit(p):
            if p.name in done:
                return
            done.add(p.name)
            for res in sorted(p.reads):
                if res in writers:
                    visit(writers[res])
            order.append(p.name)

        for p in self.passes:
            visit(p)
        return order


def default_graph():
    g = FrameGraph()
    g.add_pass("depth_prepass", writes=["depth"])
    g.add_pass("tile_pass", reads=["depth"], writes=["color"])
    g.add_pass("label_pass", reads=["color"], writes=["labels"])
    g.add_pass("overlay_pass", reads=["labels"], writes=["final"])
    return g
