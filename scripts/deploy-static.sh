set -euo pipefail

app="${1:?app required}"
source_dir="${2:?build directory required}"
case "$app" in home|history|lol-rag) ;; *) echo 'Unknown app' >&2; exit 1 ;; esac
: "${DEPLOY_HOST:?}" "${DEPLOY_USER:?}" "${DEPLOY_ROOT:?}" "${DEPLOY_KNOWN_HOSTS:?}"
deploy_port="${DEPLOY_PORT:-22}"
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]
[[ "$DEPLOY_USER" =~ ^[a-z_][a-zA-Z0-9_-]*$ ]]
[[ "$DEPLOY_ROOT" =~ ^/[a-zA-Z0-9/_-]+$ ]]
[[ "$deploy_port" =~ ^[0-9]+$ ]]
[[ -d "$source_dir" ]]
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
release="${GITHUB_SHA:-manual}-$(date -u +%Y%m%d%H%M%S)-${GITHUB_RUN_ATTEMPT:-1}"
[[ "$release" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$ ]]
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$work_dir/known_hosts"
ssh_args=(-p "$deploy_port" -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$work_dir/known_hosts")
scp_args=(-P "$deploy_port" -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$work_dir/known_hosts")
remote="$DEPLOY_USER@$DEPLOY_HOST"
incoming="$DEPLOY_ROOT/$app/incoming/$release"
tar -C "$source_dir" -czf "$work_dir/site.tar.gz" .
ssh "${ssh_args[@]}" "$remote" "mkdir -p '$incoming/build'"
scp "${scp_args[@]}" "$work_dir/site.tar.gz" "$script_dir/activate-release.py" "$remote:$incoming/"
ssh "${ssh_args[@]}" "$remote" "tar -xzf '$incoming/site.tar.gz' -C '$incoming/build' && python3 '$incoming/activate-release.py' '$app' '$release' '$incoming/build' --root '$DEPLOY_ROOT'"
