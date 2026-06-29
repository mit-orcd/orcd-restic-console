#!/usr/bin/env bash
#
# deploy.sh - deploy/roll back the orcd-restic-console app on this server.
#
# SAFETY: runs in DRY-RUN by default (shows what it WOULD do, changes nothing).
#         Add --prod to actually pull/restart/reset.
#
# Usage:
#   ./deploy.sh                   Dry-run deploy: preview fetch + incoming commits, no changes.
#   ./deploy.sh --prod            Deploy for real: fast-forward pull of the branch + restart.
#   ./deploy.sh --rollback        Dry-run rollback: show the commit it would reset to.
#   ./deploy.sh --rollback --prod Roll back for real to the last deploy's commit + restart.
#   ./deploy.sh --status          Show current commit, service state, recorded rollback target.
#   ./deploy.sh --help
#
# Assumptions (matching the current, validated process):
#   * This repo is a clone on the app server with 'origin' set and the branch checked out.
#   * The change set is code/config only (no new pip deps, no migrations); a service restart
#     is enough to pick it up. If requirements.txt ever changes, install deps before restarting.
#   * The systemd unit is 'orcd-restic-console.service' (override with SERVICE=...).
#
# Env overrides: SERVICE, BRANCH, REMOTE, ALLOW_DIRTY=1 (skip the clean-tree guard).
set -euo pipefail

SERVICE="${SERVICE:-orcd-restic-console}"
REMOTE="${REMOTE:-origin}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
DRY_RUN=1   # default: preview only; set to 0 by --prod

# Operate from the repo this script lives in, regardless of caller's cwd.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${APP_DIR}"

BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

# systemctl needs root; auto-prefix sudo when not already root.
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

c_info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
c_ok()    { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
c_err()   { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
c_dry()   { printf '\033[1;36m[dry-run]\033[0m would %s\n' "$*"; }
die()     { c_err "$*"; exit 1; }

GIT_DIR="$(git rev-parse --git-dir)"
ROLLBACK_FILE="${GIT_DIR}/DEPLOY_ROLLBACK"   # lives inside .git, never tracked

require_git_repo() {
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git work tree: ${APP_DIR}"
}

restart_service() {
    if [ "${DRY_RUN}" = "1" ]; then
        c_dry "restart ${SERVICE} (systemctl restart) and verify it is active"
        return 0
    fi
    c_info "Restarting ${SERVICE}"
    ${SUDO} systemctl restart "${SERVICE}"
    sleep 2
    if ${SUDO} systemctl is-active --quiet "${SERVICE}"; then
        c_ok "${SERVICE} is active"
    else
        c_err "${SERVICE} is NOT active after restart"
        ${SUDO} systemctl status "${SERVICE}" --no-pager --lines=20 || true
        die "service failed to come up; consider: $0 --rollback"
    fi
}

verify() {
    c_info "Current commit: $(git rev-parse --short HEAD) ($(git log -1 --pretty=%s))"
    # Best-effort: confirm the configured restic cache dir exists (app creates it at startup).
    local cache_dir
    cache_dir="$(grep -E '^[[:space:]]*cache_dir:' config/app.yml 2>/dev/null | head -1 | sed -E 's/.*cache_dir:[[:space:]]*"?([^"#]+)"?.*/\1/' | xargs || true)"
    if [ -n "${cache_dir}" ]; then
        if [ -d "${cache_dir}" ]; then c_ok "restic cache dir present: ${cache_dir}"
        else c_warn "restic cache dir not created yet: ${cache_dir} (appears on first restic call)"; fi
    fi
    ${SUDO} systemctl status "${SERVICE}" --no-pager --lines=5 || true
}

cmd_status() {
    require_git_repo
    c_info "Repo:    ${APP_DIR}"
    c_info "Branch:  ${BRANCH}"
    c_info "Commit:  $(git rev-parse --short HEAD) ($(git log -1 --pretty=%s))"
    if [ -f "${ROLLBACK_FILE}" ]; then
        c_info "Rollback target (last deploy): $(cut -c1-12 "${ROLLBACK_FILE}")"
    else
        c_warn "no rollback target recorded yet (no deploy run via this script)"
    fi
    ${SUDO} systemctl status "${SERVICE}" --no-pager --lines=5 || true
}

cmd_deploy() {
    require_git_repo
    if [ "${DRY_RUN}" = "1" ]; then
        c_warn "DRY-RUN (default): previewing only, no changes. Re-run with --prod to deploy."
    else
        c_info "PROD deploy: changes WILL be applied."
    fi

    local cur_branch
    cur_branch="$(git rev-parse --abbrev-ref HEAD)"
    [ "${cur_branch}" = "${BRANCH}" ] || die "checked out '${cur_branch}', expected '${BRANCH}' (set BRANCH= to override)"

    # Guard: a dirty tree means a pull could clobber/abort on local edits (esp. config/app.yml).
    if [ "${ALLOW_DIRTY}" != "1" ]; then
        if ! git diff --quiet || ! git diff --cached --quiet; then
            c_err "working tree has local changes:"
            git status --short
            if [ "${DRY_RUN}" = "1" ]; then
                c_warn "a --prod run would REFUSE here; commit/stash them (or set ALLOW_DIRTY=1)"
            else
                die "commit/stash them, or re-run with ALLOW_DIRTY=1 if you are sure"
            fi
        fi
    fi

    c_info "Fetching ${REMOTE}/${BRANCH} (read-only)"
    git fetch "${REMOTE}" "${BRANCH}"

    local old new
    old="$(git rev-parse HEAD)"
    new="$(git rev-parse "${REMOTE}/${BRANCH}")"

    if [ "${old}" = "${new}" ]; then
        c_ok "already up to date at $(git rev-parse --short HEAD)"
        if [ "${DRY_RUN}" = "1" ]; then
            c_dry "record rollback target ${old:0:7} and restart ${SERVICE} to re-apply runtime state"
        else
            echo "${old}" > "${ROLLBACK_FILE}"
            restart_service
            verify
        fi
        return 0
    fi

    c_info "Incoming changes ${old:0:7}..${new:0:7}:"
    git --no-pager log --oneline "${old}..${new}"
    if ! git diff --quiet "${old}" "${new}" -- requirements.txt; then
        c_warn "requirements.txt changed in this update -- install deps in the venv BEFORE the service settles:"
        c_warn "    .venv/bin/pip install -r requirements.txt"
    fi

    if [ "${DRY_RUN}" = "1" ]; then
        c_dry "record rollback target ${old:0:7} -> ${ROLLBACK_FILE}"
        c_dry "fast-forward pull ${REMOTE}/${BRANCH} (${old:0:7}..${new:0:7})"
        c_dry "restart ${SERVICE} and verify"
        c_warn "DRY-RUN only. Nothing changed. Re-run with --prod to apply."
        return 0
    fi

    # Record rollback point, then fast-forward only (no surprise merge commits).
    echo "${old}" > "${ROLLBACK_FILE}"
    c_info "Recorded rollback target ${old:0:7} -> ${ROLLBACK_FILE}"
    c_info "Fast-forward pull ${REMOTE}/${BRANCH}"
    git pull --ff-only "${REMOTE}" "${BRANCH}"

    restart_service
    verify
    c_ok "Deploy complete. Roll back with: $0 --rollback --prod"
}

cmd_rollback() {
    require_git_repo
    [ -f "${ROLLBACK_FILE}" ] || die "no recorded rollback target (${ROLLBACK_FILE} missing)"
    local target
    target="$(tr -d '[:space:]' < "${ROLLBACK_FILE}")"
    git cat-file -e "${target}^{commit}" 2>/dev/null || die "recorded rollback commit ${target} not found"

    if [ "${DRY_RUN}" = "1" ]; then
        c_warn "DRY-RUN (default): previewing only, no changes. Re-run with --rollback --prod to apply."
        c_dry "git reset --hard $(git rev-parse --short HEAD) -> ${target:0:7} ($(git log -1 --pretty=%s "${target}"))"
        c_dry "restart ${SERVICE} and verify"
        return 0
    fi

    c_warn "Rolling back $(git rev-parse --short HEAD) -> ${target:0:7} (git reset --hard)"
    git reset --hard "${target}"
    restart_service
    verify
    c_ok "Rollback complete (now at $(git rev-parse --short HEAD))."
    c_warn "If you want the remote to match this rollback, handle it explicitly (e.g. git revert + push)."
}

MODE="deploy"
while [ $# -gt 0 ]; do
    case "$1" in
        --prod|--apply|--execute) DRY_RUN=0 ;;
        --dry-run|-n)             DRY_RUN=1 ;;
        deploy)                   MODE="deploy" ;;
        --rollback|rollback)      MODE="rollback" ;;
        --status|status)          MODE="status" ;;
        -h|--help|help)           MODE="help" ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
    shift
done

case "${MODE}" in
    deploy)   cmd_deploy ;;
    rollback) cmd_rollback ;;
    status)   cmd_status ;;
    help)     awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}" ;;
esac
