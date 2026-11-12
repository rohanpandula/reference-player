#!/bin/bash
# Local compile only: no provisioning registration, installation, publishing,
# credentials, or automatic dependency upgrades. Run with Xcode 27.2+ on a Mac.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
mode="${1:-simulator}"
if [[ "$#" -gt 1 || ( "$mode" != simulator && "$mode" != device ) ]]; then
    echo "Usage: bash PrivateBuild/build-tvos.sh [simulator|device]" >&2
    exit 2
fi
command -v python3 >/dev/null || { echo "Python 3.9+ is required for the source checks." >&2; exit 2; }
# Keep the array nonempty: macOS ships Bash 3.2, whose nounset handling of an
# empty array differs from newer Bash releases.
verify_args=(--root "$repo_root")
if [[ "$mode" == device ]]; then verify_args+=(--require-signing); fi
python3 "$script_dir/verify.py" "${verify_args[@]}"
if [[ "$(uname -s)" != Darwin ]]; then
    echo "STOP: Apple tvOS builds require macOS and Xcode. Static checks ran; no Swift build was attempted." >&2
    exit 2
fi
command -v xcodebuild >/dev/null || { echo "Select a full Xcode installation, not only Command Line Tools." >&2; exit 2; }
xcode_version="$(xcodebuild -version | awk '/^Xcode / {print $2; exit}')"
python3 - "$xcode_version" <<'PY'
import re, sys
version = sys.argv[1]
if not re.fullmatch(r"\d+(?:\.\d+){0,2}", version):
    sys.exit("Cannot identify the selected Xcode version.")
parts = tuple(int(part) for part in version.split("."))
if (parts + (0, 0))[:2] < (27, 2):
    sys.exit("This source baseline requires Xcode 27.2 or newer. Select it explicitly on your Mac.")
if version != "27.2":
    print("WARNING: selected Xcode " + version + " differs from the upstream 27.2 baseline; record and test that change.", file=sys.stderr)
PY

cd -- "$repo_root"
packages_dir="$script_dir/.build/SourcePackages"
derived_dir="$script_dir/.build/DerivedData"
mkdir -p -- "$packages_dir" "$derived_dir"
lock_path="$repo_root/Rivulet.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved"
lock_before="$(shasum -a 256 "$lock_path" | awk '{print $1}')"
check_actual=false

finish() {
    local result=$?
    trap - EXIT
    # Never auto-restore a changed lockfile: that would hide a resolver change.
    local after
    after="$(shasum -a 256 "$lock_path" 2>/dev/null | awk '{print $1}')" || after=missing
    if [[ "$after" != "$lock_before" ]]; then
        echo "FAIL: Package.resolved changed during the build. Inspect the diff; nothing was silently restored." >&2
        result=1
    fi
    if [[ "$check_actual" == true ]]; then
        python3 "$script_dir/verify.py" "${verify_args[@]}" --resolved-checkouts "$packages_dir/checkouts" || result=1
    else
        python3 "$script_dir/verify.py" "${verify_args[@]}" || result=1
    fi
    exit "$result"
}
trap finish EXIT

python3 "$script_dir/engine.py"
verify_args+=(--patched-engine)

common=(-workspace ReferencePlayer.xcworkspace -scheme "Reference Player" -configuration Release
        -clonedSourcePackagesDirPath "$packages_dir" -onlyUsePackageVersionsFromResolvedFile)
# Xcode may download the already-pinned packages/binaries here. It is not allowed
# to choose a newer version to make a resolution failure disappear.
xcodebuild "${common[@]}" -resolvePackageDependencies CODE_SIGNING_ALLOWED=NO
check_actual=true
python3 "$script_dir/verify.py" "${verify_args[@]}" --resolved-checkouts "$packages_dir/checkouts"
if [[ "$mode" == simulator ]]; then
    xcodebuild "${common[@]}" -derivedDataPath "$derived_dir" \
        -destination 'generic/platform=tvOS Simulator' \
        CODE_SIGNING_ALLOWED=NO ARCHS=arm64 build
else
    # No -allowProvisioningUpdates: establish your account, owned App Group, and
    # development profiles in Xcode first. This never registers devices for you.
    xcodebuild "${common[@]}" -derivedDataPath "$derived_dir" \
        -destination 'generic/platform=tvOS' build
fi
echo "Build command completed. Output is under PrivateBuild/.build/DerivedData."
echo "Nothing was installed. Use the Reference Player scheme in Xcode for the paired Apple TV."
