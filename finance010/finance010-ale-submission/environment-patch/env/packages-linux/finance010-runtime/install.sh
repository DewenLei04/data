#!/usr/bin/env bash
# finance010 task package: office suite, PDF/OCR toolchain, the four required font families,
# and the runtime Python libraries. Invoked by ALE's env/install_task_deps.sh because the task
# card names `finance010-runtime` in requiredSystemPackages.
#
# Every package name and version comes from files/packages-apt.txt and files/packages-pip.txt.
# Nothing is named inline here, and that is a deliberate discipline rather than a style: each
# pin in those manifests carries an INDEX-VERIFIED / INDEX-UNVERIFIED label, and the case's own
# manifest gate fails the build on an unlabelled one. Routing every install through them keeps
# one audited surface. A name typed directly into this script would be an install with no
# evidence behind it.
#
# This installs ON TOP of the official ALE base image. It must not replace the CUA server,
# /opt/ale-run/.venv, the Node runtime, or the desktop startup path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="$SCRIPT_DIR/files"
export DEBIAN_FRONTEND=noninteractive

required_files=(packages-apt.txt packages-pip.txt fonts.md)
for name in "${required_files[@]}"; do
  test -f "$FILES_DIR/$name" || { echo "missing package asset: $name" >&2; exit 1; }
done

# Strip comments, blank lines and the evidence label, keeping `name=version` for apt and
# `name==version` for pip. The label is the first whitespace-delimited token after the pin, so
# taking field 1 drops it; the sed is belt-and-braces for a label written without a space.
strip_manifest() {
  grep -v '^\s*#' "$1" | grep -v '^\s*$' | awk '{print $1}' \
    | sed -E 's/\[INDEX-(UN)?VERIFIED\]//'
}

apt-get update
# shellcheck disable=SC2046
apt-get install -y --no-install-recommends $(strip_manifest "$FILES_DIR/packages-apt.txt")
rm -rf /var/lib/apt/lists/*

# LibreOffice needs a virtual display for headless conversion on this base, and the case's GUI
# surface is the official image's desktop, so no X server is installed here -- only the client
# tooling. xvfb is deliberately absent from packages-apt.txt for that reason: on the official
# base the desktop is already running, and installing a second X server on top of it is how a
# display ends up being served by the wrong one.

# The corpus and all four deliverables are specified in exactly four font families. Assert that
# fontconfig resolves each one into the metric-compatible Liberation/Carlito set installed
# above, rather than silently substituting something with different metrics: a substituted face
# shifts page geometry, and the agent's own PDF export then stops matching the documents it read.
# files/fonts.md records why the real Microsoft faces are NOT installed (network fetch plus EULA
# acceptance at install time).
fc-cache -f
for family in "Times New Roman" "Arial" "Courier New" "Calibri"; do
  match="$(fc-match "$family")"
  echo "fc-match $family -> $match"
  case "$match" in
    *Liberation*|*Carlito*) ;;
    *) echo "FONT-COVERAGE: $family resolved outside Liberation/Carlito: $match" >&2; exit 1 ;;
  esac
done

# Runtime Python libraries for the sandbox interpreter. ocrmypdf is intentionally absent from
# the pip manifest: jammy ships python3 3.10.6 and PyPI's current ocrmypdf requires >= 3.11, so
# a pip install would break OCR at import time. It comes from apt above.
python3 -m pip install --no-cache-dir -r "$FILES_DIR/packages-pip.txt"

# Playwright's bundled Chromium, installed here rather than through apt: on jammy both
# `chromium` and `chromium-browser` are transitional stubs that pull the snap, and snaps do not
# run in a plain container. This step needs network at IMAGE BUILD time only; the task itself
# runs with network disabled.
python3 -m playwright install --with-deps chromium

install -d -m 0755 /opt/finance010

cat > /usr/local/bin/finance010-runtime-health <<'EOF'
#!/usr/bin/env bash
# Assert the finance010 runtime is present and correct. `--check-installed` is the build-time
# form named in meta.json's `verify`; the bare form is what the task adapter calls before the
# agent starts. Both check the same things, because the failure being guarded -- a font
# substitution or a missing converter -- is equally fatal whenever it appears.
set -euo pipefail

for binary in soffice ocrmypdf tesseract pdftotext qpdf gs python3; do
  command -v "$binary" >/dev/null || { echo "MISSING: $binary" >&2; exit 1; }
done

for family in "Times New Roman" "Arial" "Courier New" "Calibri"; do
  match="$(fc-match "$family")"
  case "$match" in
    *Liberation*|*Carlito*) ;;
    *) echo "FONT-COVERAGE: $family -> $match" >&2; exit 1 ;;
  esac
done

python3 - <<'PY'
import importlib, sys
missing = [name for name in ("openpyxl", "pypdf") if importlib.util.find_spec(name) is None]
if missing:
    sys.stderr.write("MISSING PYTHON MODULE: " + ", ".join(missing) + "\n")
    raise SystemExit(1)
PY

echo RUNTIME_OK
EOF
chmod 0755 /usr/local/bin/finance010-runtime-health

/usr/local/bin/finance010-runtime-health --check-installed
