#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: install-termux.sh --ref FULL_COMMIT_SHA [--source DIR]
                         [--install-root DIR] [--config FILE]

Install one Git revision into a versioned release directory, verify it, then
atomically switch the current symlink. Private config and workspace are never
copied or modified.
EOF
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
install_root=${HOME:?HOME is required}/opt/android-telegram-agent
git_ref=
config_path=

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

if [[ $(id -u) == 0 ]]; then
    echo "Refusing root installation" >&2
    exit 1
fi
for tool in git python tar mktemp; do
    command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

source_dir=$(python - "$source_dir" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]).expanduser().resolve()
if not p.is_dir():
    raise SystemExit("Source checkout not found")
print(p)
PY
)
install_root=$(python - "$install_root" <<'PY'
from pathlib import Path
import sys
home = Path.home().resolve()
p = Path(sys.argv[1]).expanduser().resolve()
if p == home or not p.is_relative_to(home):
    raise SystemExit("Install root must be a subdirectory of HOME")
print(p)
PY
)

commit=$(git -C "$source_dir" rev-parse --verify "$git_ref^{commit}")
[[ $commit =~ ^[0-9a-f]{40}$ ]] || { echo "Git revision is not a commit" >&2; exit 1; }
for required in bot.py config.py telegram_api.py tests; do
    git -C "$source_dir" cat-file -e "$commit:$required" 2>/dev/null || {
        echo "Selected revision is not an android-telegram-agent source tree" >&2
        exit 1
    }
done

umask 077
mkdir -p "$install_root/releases"
chmod 700 "$install_root" "$install_root/releases"
release_dir="$install_root/releases/$commit"
staging=
cleanup() {
    if [[ -n ${staging:-} && -d $staging && $staging == "$install_root/releases/".staging.* ]]; then
        python - "$staging" <<'PY'
from pathlib import Path
import shutil, sys
p = Path(sys.argv[1])
if p.name.startswith(".staging."):
    shutil.rmtree(p)
PY
    fi
}
trap cleanup EXIT

if [[ -e $release_dir || -L $release_dir ]]; then
    [[ -d $release_dir && ! -L $release_dir ]] || {
        echo "Existing release path is unsafe" >&2; exit 1;
    }
    [[ -f $release_dir/.installed-commit ]] || {
        echo "Existing release is missing its version marker" >&2; exit 1;
    }
    [[ $(<"$release_dir/.installed-commit") == "$commit" ]] || {
        echo "Existing release version marker mismatch" >&2; exit 1;
    }
else
    staging=$(mktemp -d "$install_root/releases/.staging.XXXXXX")
    git -C "$source_dir" archive "$commit" | tar -x -C "$staging"
    chmod -R go-rwx "$staging"
    (
        cd "$staging"
        python -m compileall -q .
        python -m unittest discover -s tests -v
    )
    printf '%s\n' "$commit" >"$staging/.installed-commit"
    mv -- "$staging" "$release_dir"
    staging=
fi

if [[ -n $config_path ]]; then
    config_path=$(python - "$config_path" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)
    (
        cd "$release_dir"
        python -c 'from config import load_settings; import sys; load_settings(sys.argv[1])' "$config_path"
    )
fi

replace_link() {
    local name=$1 target=$2 path="$install_root/$1" temporary="$install_root/.$1.new.$$"
    if [[ -e $path && ! -L $path ]]; then
        echo "Refusing to replace non-symlink $name" >&2
        exit 1
    fi
    [[ ! -e $temporary && ! -L $temporary ]] || { echo "Temporary link exists" >&2; exit 1; }
    ln -s "$target" "$temporary"
    mv -Tf -- "$temporary" "$path"
}

new_target="releases/$commit"
old_target=
if [[ -L $install_root/current ]]; then
    old_target=$(readlink "$install_root/current")
    [[ $old_target =~ ^releases/[0-9a-f]{40}$ && -d $install_root/$old_target ]] || {
        echo "Current release link is unsafe" >&2; exit 1;
    }
fi
if [[ -n $old_target && $old_target != "$new_target" ]]; then
    replace_link previous "$old_target"
fi
replace_link current "$new_target"
echo "INSTALL_OK $commit"
