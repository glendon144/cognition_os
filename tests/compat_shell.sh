#!/bin/sh
# Controlled reference for the legacy shell algorithm. It is test-only: the
# production NetBSD/Linux/macOS shell entry points remain unchanged.
set -eu
manifest=$1
root=$2
while IFS="$(printf '\t')" read -r requirement name checkout url branch; do
    case "$requirement" in ''|'#'*) continue ;; esac
    target=$root/$checkout
    if [ -d "$target/.git" ]; then
        git -C "$target" pull --ff-only >/dev/null 2>&1
    elif [ ! -e "$target" ]; then
        git clone --branch "$branch" --single-branch -- "$url" "$target" >/dev/null 2>&1
    else
        exit 1
    fi
done < "$manifest"
