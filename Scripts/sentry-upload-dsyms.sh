#!/bin/sh
#
# Upload dSYMs to Sentry so release crash / App Hang stack traces symbolicate
# (see RIVULET-41). Invoked as an Xcode "Run Script" build phase on the Rivulet
# app target.
#
# Behaviour:
#   * Runs ONLY for Release builds / Archives (skips Debug + simulator).
#   * No-ops quietly (exit 0) if sentry-cli or .sentryclirc is missing, or if
#     the token is still the placeholder — so a fresh checkout without the
#     local config never fails the build.
#   * Reads org/project/token from .sentryclirc at the repo root (gitignored).
#
# Manual one-off upload (e.g. for a build that shipped before this phase
# existed) is the same command this script runs; see SENTRY_DSYM_UPLOAD notes.

set -u

# This fork never uploads symbols or source code to upstream's Sentry account, even
# if the building Mac has sentry-cli or an old .sentryclirc. Monitoring with your
# own account is a separate, reviewed change.
echo "sentry: disabled in this fork (no symbols or sources uploaded)"
exit 0

# Only upload for non-debug builds. Debug uses plain DWARF (no dSYM) anyway.
if [ "${CONFIGURATION:-}" = "Debug" ]; then
  echo "sentry: skipping dSYM upload for Debug configuration"
  exit 0
fi

# Skip simulator builds — those dSYMs aren't what ships.
case "${PLATFORM_NAME:-}" in
  *simulator*)
    echo "sentry: skipping dSYM upload for simulator (${PLATFORM_NAME})"
    exit 0
    ;;
esac

REPO_ROOT="${SRCROOT:-$(pwd)}"
RC="${REPO_ROOT}/.sentryclirc"

# Locate sentry-cli (Homebrew installs to /usr/local/bin or /opt/homebrew/bin,
# which aren't always on Xcode's PATH).
SENTRY_CLI="$(command -v sentry-cli || true)"
for candidate in /opt/homebrew/bin/sentry-cli /usr/local/bin/sentry-cli; do
  if [ -z "${SENTRY_CLI}" ] && [ -x "${candidate}" ]; then
    SENTRY_CLI="${candidate}"
  fi
done

if [ -z "${SENTRY_CLI}" ]; then
  echo "sentry: sentry-cli not found on PATH — skipping dSYM upload. Install with 'brew install getsentry/tools/sentry-cli'."
  exit 0
fi

if [ ! -f "${RC}" ]; then
  echo "sentry: no .sentryclirc at repo root — skipping dSYM upload. Copy .sentryclirc.template and add your token."
  exit 0
fi

# Bail (without failing the build) if the token is still the placeholder.
if grep -q "YOUR_SENTRY_AUTH_TOKEN_HERE" "${RC}"; then
  echo "sentry: .sentryclirc still has the placeholder token — skipping dSYM upload."
  exit 0
fi

echo "sentry: uploading dSYMs (config=${CONFIGURATION:-?}, platform=${PLATFORM_NAME:-?})"

# Point sentry-cli at our config and upload every dSYM Xcode produced for this
# build. DWARF_DSYM_FOLDER_PATH is the per-build dSYM output; it covers the app
# and the embedded frameworks/extension.
export SENTRY_PROPERTIES="${RC}"

# Upload the dSYMs Xcode produced for OUR code (Rivulet.app + TopShelfExtension).
#
# The embedded third-party frameworks (FFmpeg Libav*/Libsw*/Libzimg, Libdav1d,
# Sentry) ship as STRIPPED prebuilt binaries from their SPM packages — they have
# no DWARF debug_info, so no dSYM exists to upload for them. That's expected and
# harmless here: RIVULET-41 and our crashes are in Swift on the main thread,
# which Rivulet.app's dSYM covers. Apple's "Upload Symbols Failed" warnings in
# Organizer are about those same stripped frameworks for APPLE's crash reporting
# (which we don't use) — uncheck "Upload your app's symbols" at archive time to
# silence them. There is nothing to fix on our side.
"${SENTRY_CLI}" debug-files upload \
  --include-sources \
  "${DWARF_DSYM_FOLDER_PATH:-${BUILT_PRODUCTS_DIR}}" \
  || echo "sentry: dSYM upload reported a non-zero status (build not failed)."

exit 0
