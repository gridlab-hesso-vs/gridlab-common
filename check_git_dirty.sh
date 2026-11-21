#!/usr/bin/env bash

# Scan the directories below the current one (*/) for git repositories and
# report, for each, two independent things:
#
#   check_dirty  — does the working tree have uncommitted changes (staged,
#                  unstaged, or untracked)?
#   check_sync   — how does the branch stand against its upstream: behind (a
#                  pull is owed), ahead (a push is owed), or both (diverged)?
#
# Every repository scanned is printed, clean ones included, so the output is a
# roster rather than a list of complaints, and a summary at the end groups the
# repos that want attention: who is dirty, who is behind, who is ahead.
#
# One level only, not recursive, and the current directory itself is not
# checked. Nothing is changed unless --pull is asked for.
#
# Usage: check_git_dirty.sh [-n|--no-fetch] [-p|--pull]
#
#   -n, --no-fetch   Skip the `git fetch`. The behind count is then measured
#                    against whatever the last fetch left in the remote-tracking
#                    refs, which can be arbitrarily stale — so a repo can report
#                    "up to date" while its upstream has moved on. For offline
#                    runs, or where the network cost is not worth paying.
#
#   -p, --pull       Fast-forward the repositories where the pull is not a
#                    judgement call: purely behind, with a clean working tree.
#                    Everything else is left exactly as found — a dirty tree,
#                    because the pull would land on top of local edits, and a
#                    diverged branch, because taking those commits means
#                    choosing merge or rebase and that is not a scan's call.
#                    The pull is a `git merge --ff-only` against the ref just
#                    fetched, so it either fast-forwards or refuses; it never
#                    writes a merge commit.
#
# Each repo is fetched first by default, so the behind count reflects the
# upstream as it is now rather than as it was at the last pull.
#
# Exit status: 0 all good, 1 something dirty, 2 something behind, 3 both.
# Being ahead is reported but does not affect the exit status — unpushed work
# is a state, not a fault. A repo that --pull brought up to date is no longer
# behind and does not colour the exit status either.

do_fetch=1
do_pull=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        -n|--no-fetch) do_fetch=0 ;;
        -p|--pull)     do_pull=1 ;;
        *)             echo "usage: $(basename "$0") [-n|--no-fetch] [-p|--pull]" >&2; exit 64 ;;
    esac
    shift
done

# `git -C "$dir"` runs each command in the repo without changing the script's
# working directory — so every sibling is checked (the old `cd "$dir" ... cd ..`
# drifted the CWD and only the first repo worked).

# Returns 0 when the working tree is clean, 1 when it is dirty.
check_dirty() {
    local dir=$1

    if ! git -C "$dir" diff --quiet \
       || ! git -C "$dir" diff --cached --quiet \
       || [ -n "$(git -C "$dir" ls-files --others --exclude-standard)" ]; then
        printf '  ⚠️  uncommitted changes\n'
        return 1
    fi

    printf '  ✅ clean\n'
    return 0
}

# Prints the standing against the upstream and leaves it in three globals for
# the summary to group by:
#
#   sync_state  current | behind | ahead | diverged | unknown | no-upstream
#   sync_behind commits the upstream has that we do not  (empty if unknown)
#   sync_ahead  commits we have that the upstream does not
#
# Returns 0 when there is nothing to pull, 1 when the branch is behind. A repo
# with no upstream (detached HEAD, purely local branch) has nowhere to pull
# from, so it counts as nothing to pull.
check_sync() {
    local dir=$1
    local upstream

    sync_state=unknown sync_behind= sync_ahead= sync_upstream=

    # Test the exit status, not just emptiness: on a branch with no upstream
    # — including the unborn branch of a fresh clone of an empty repo — this
    # rev-parse exits 128 but still echoes the literal string "@{upstream}" on
    # stdout, which would otherwise sail through as an upstream name and be
    # reported as unreachable rather than absent.
    if ! upstream=$(git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) \
       || [ -z "$upstream" ]; then
        sync_state=no-upstream
        printf '  ➖ no upstream — nothing to pull\n'
        return 0
    fi

    sync_upstream=$upstream

    if [ "$do_fetch" -eq 1 ] && ! git -C "$dir" fetch --quiet 2>/dev/null; then
        printf '  ❓ fetch from %s failed — standing unknown\n' "$upstream"
        return 0
    fi

    # One rev-list, two counts: left of the ... is ours, right is theirs.
    read -r sync_ahead sync_behind < <(git -C "$dir" rev-list --left-right --count "HEAD...$upstream" 2>/dev/null)

    if [ -z "$sync_behind" ]; then
        sync_behind=
        printf '  ❓ cannot compare with %s — standing unknown\n' "$upstream"
        return 0
    fi

    if [ "$sync_behind" -eq 0 ] && [ "$sync_ahead" -eq 0 ]; then
        sync_state=current
        printf '  ✅ up to date with %s\n' "$upstream"
        return 0
    fi

    if [ "$sync_behind" -eq 0 ]; then
        sync_state=ahead
        printf '  ⬆️  %s commit(s) ahead of %s — push\n' "$sync_ahead" "$upstream"
        return 0
    fi

    if [ "$sync_ahead" -gt 0 ]; then
        sync_state=diverged
        printf '  ⚡ %s behind / %s ahead of %s — diverged\n' "$sync_behind" "$sync_ahead" "$upstream"
    else
        sync_state=behind
        printf '  ⬇️  %s commit(s) behind %s — pull\n' "$sync_behind" "$upstream"
    fi
    return 1
}

# Fast-forward a repository the caller has already established is purely behind
# and clean. The upstream ref is the one just fetched, so `merge --ff-only`
# needs no network of its own; it moves the branch pointer or it fails, and it
# cannot invent a merge commit.
#
# Returns 0 when the pull happened, 1 when it did not.
try_pull() {
    local dir=$1 upstream=$2 behind=$3 err

    if ! err=$(git -C "$dir" merge --ff-only "$upstream" 2>&1); then
        # git's complaint is usually several lines; the last one is the point.
        err=${err##*$'\n'}
        printf '  ✋ not pulled — %s\n' "${err:-merge --ff-only failed}"
        return 1
    fi

    printf '  ⤵️  pulled %s commit(s) from %s\n' "$behind" "$upstream"
    return 0
}

# One summary block: a heading with a count, then one indented line per entry.
# Entries are "name<TAB>note"; the note is optional. Prints nothing when the
# list is empty, so the summary only shows the categories that occurred.
print_section() {
    local heading=$1
    shift
    [ "$#" -gt 0 ] || return 0

    printf '\n  %s (%d)\n' "$heading" "$#"

    local entry name note
    for entry in "$@"; do
        name=${entry%%$'\t'*}
        note=${entry#*$'\t'}
        if [ "$note" = "$entry" ]; then
            printf '    %s\n' "$name"
        else
            printf '    %-26s %s\n' "$name" "$note"
        fi
    done
}

dirty_repos=()
pulled_repos=()
behind_repos=()
ahead_repos=()
diverged_repos=()
unknown_repos=()
no_upstream_repos=()
clean_current=0
repo_count=0

if [ "$do_fetch" -eq 0 ]; then
    echo "(--no-fetch: remote-tracking refs are as of the last fetch, possibly stale)"
fi

for dir in */; do
    # Only consider actual git repositories.
    [ -d "$dir/.git" ] || continue

    name=${dir%/}
    repo_count=$((repo_count + 1))
    printf '%s\n' "$name"

    is_dirty=0
    check_dirty "$dir" || is_dirty=1
    [ "$is_dirty" -eq 1 ] && dirty_repos+=("$name")

    check_sync "$dir"

    # Only the plain behind-and-clean case is pulled; sync_state is rewritten to
    # current on success so the repo is summarised, and counted, as it now is.
    if [ "$do_pull" -eq 1 ] && [ "$sync_state" = behind ]; then
        if [ "$is_dirty" -eq 1 ]; then
            printf '  ✋ not pulled — uncommitted changes\n'
        elif try_pull "$dir" "$sync_upstream" "$sync_behind"; then
            pulled_repos+=("$name"$'\t'"$sync_behind commit(s) from $sync_upstream")
            sync_state=current
        fi
    fi

    case "$sync_state" in
        behind)      behind_repos+=("$name"$'\t'"$sync_behind behind") ;;
        ahead)       ahead_repos+=("$name"$'\t'"$sync_ahead ahead") ;;
        diverged)    diverged_repos+=("$name"$'\t'"$sync_behind behind / $sync_ahead ahead") ;;
        unknown)     unknown_repos+=("$name"$'\t'"could not reach $sync_upstream") ;;
        no-upstream) no_upstream_repos+=("$name") ;;
        current)     [ "$is_dirty" -eq 0 ] && clean_current=$((clean_current + 1)) ;;
    esac
done

if [ "$repo_count" -eq 0 ]; then
    echo "no git repositories found in $PWD/*/"
    exit 0
fi

printf '\n%s\n' "────────────────────────────────────────────"
if [ "$repo_count" -eq 1 ]; then
    printf 'summary — 1 repository scanned\n'
else
    printf 'summary — %d repositories scanned\n' "$repo_count"
fi

print_section "pulled"               "${pulled_repos[@]}"
print_section "uncommitted changes"  "${dirty_repos[@]}"
print_section "behind — pull"        "${behind_repos[@]}"
print_section "ahead — push"         "${ahead_repos[@]}"
print_section "diverged"             "${diverged_repos[@]}"
print_section "standing unknown"     "${unknown_repos[@]}"
print_section "no upstream"          "${no_upstream_repos[@]}"

printf '\n  clean and up to date: %d of %d\n' "$clean_current" "$repo_count"

found_dirty=0
found_behind=0

if [ "${#dirty_repos[@]}" -gt 0 ]; then
    found_dirty=1
fi

# Diverged counts as behind: there are upstream commits to take, however the
# taking ends up being done.
if [ "${#behind_repos[@]}" -gt 0 ] || [ "${#diverged_repos[@]}" -gt 0 ]; then
    found_behind=1
fi

exit $(( found_dirty | found_behind << 1 ))
