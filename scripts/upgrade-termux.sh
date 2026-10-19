#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: upgrade-termux.sh --ref FULL_COMMIT_SHA [--source DIR]
                         [--install-root DIR] [--config FILE]

Promote an already-fetched, exact commit through install-termux.sh. This tool
does not fetch, pull, restart a service, or modify private configuration.
EOF
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
install_root=${HOME:?HOME is required}/opt/android-telegram-agent
config_path=
git_ref=
while (($#)); do
    case $1 in
        --source) source_dir=${2:?missing --source value}; shift 2 ;;
        --ref) git_ref=${2:?missing --ref value}; shift 2 ;;
        --install-root) install_root=${2:?missing --install-root value}; shift 2 ;;
        --config) config_path=${2:?missing --config value}; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument" >&2; usage >&2; exit 2 ;;
    esac
done
[[ ${git_ref:-} =~ ^[0-9a-f]{40}$ ]] || {
    echo "--ref must be a full 40-character commit SHA" >&2
    exit 2
}

args=(--source "$source_dir" --ref "$git_ref" --install-root "$install_root")
if [[ -n $config_path ]]; then
    args+=(--config "$config_path")
fi
exec "$script_dir/install-termux.sh" "${args[@]}"
