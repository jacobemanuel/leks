#!/bin/sh
# asset build helpers (toy example script)
PACK_MIB=64
CACHE_DIR="${BUILD_CACHE_DIR:-./.build-cache}"

hash_inputs() {
    # one hash for a list of files, sorted so the order cannot matter
    sort "$@" | shasum -a 256 | cut -d' ' -f1
}

bundle_assets() {
    # pack files from $1 into bundles of $PACK_MIB mebibytes in $2
    src="$1"
    out="$2"
    mkdir -p "$out"
    key=$(hash_inputs "$src"/manifest.txt)
    if [ -f "$CACHE_DIR/$key.tar" ]; then
        cp "$CACHE_DIR/$key.tar" "$out/bundle.tar"
        return 0
    fi
    tar -cf "$out/bundle.tar" -C "$src" .
}

prune_cache() {
    # remove cache entries older than 30 days
    find "$CACHE_DIR" -type f -mtime +30 -exec rm {} +
}
