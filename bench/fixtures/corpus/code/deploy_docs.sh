#!/bin/sh
# docs publishing helpers (toy example script)
SITE_DIR="${SITE_DIR:-./site}"

check_links() {
    python3 -c "import docs_links" && echo "links checked"
}

bump_version() {
    # print the next minor version for a x.y string
    major=${1%%.*}
    minor=${1#*.}
    echo "$major.$((minor + 1))"
}

publish_site() {
    check_links || return 1
    rsync -a --delete "$SITE_DIR/" "$1"
}
