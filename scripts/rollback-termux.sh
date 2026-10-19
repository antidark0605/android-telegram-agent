#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: rollback-termux.sh [--install-root DIR] [--config FILE]

Swap current and previous verified releases. The caller must restart the
service after reviewing the selected version.
EOF
}

install_root=${HOME:?HOME is required}/opt/android-telegram-agent
config_path=
while (($#)); do
    case $1 in
        --install-root) install_root=${2:?missing --install-root value}; shift 2 ;;
        --config) config_path=${2:?missing --config value}; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument" >&2; usage >&2; exit 2 ;;
    esac
done
[[ $(id -u) != 0 ]] || { echo "Refusing root rollback" >&2; exit 1; }
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

for name in current previous; do
    [[ -L $install_root/$name ]] || { echo "$name release is unavailable" >&2; exit 1; }
done
current_target=$(readlink "$install_root/current")
previous_target=$(readlink "$install_root/previous")
for target in "$current_target" "$previous_target"; do
    [[ $target =~ ^releases/[0-9a-f]{40}$ && -d $install_root/$target ]] || {
        echo "Release link is unsafe" >&2; exit 1;
    }
    expected=${target#releases/}
    [[ -f $install_root/$target/.installed-commit ]] || {
        echo "Release version marker is missing" >&2; exit 1;
    }
    [[ $(<"$install_root/$target/.installed-commit") == "$expected" ]] || {
        echo "Release version marker mismatch" >&2; exit 1;
    }
done

python -m compileall -q "$install_root/$previous_target"
if [[ -n $config_path ]]; then
    (
        cd "$install_root/$previous_target"
        python -c 'from config import load_settings; import sys; load_settings(sys.argv[1])' "$config_path"
    )
fi

umask 077
new_current="$install_root/.current.new.$$"
new_previous="$install_root/.previous.new.$$"
[[ ! -e $new_current && ! -L $new_current && ! -e $new_previous && ! -L $new_previous ]] || {
    echo "Temporary link exists" >&2; exit 1;
}
ln -s "$previous_target" "$new_current"
ln -s "$current_target" "$new_previous"
mv -Tf -- "$new_previous" "$install_root/previous"
mv -Tf -- "$new_current" "$install_root/current"
echo "ROLLBACK_OK ${previous_target#releases/}"
