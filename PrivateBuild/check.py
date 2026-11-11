#!/usr/bin/env python3
"""Every check of this fork, one at a time, with a result table.

    python3 PrivateBuild/check.py                   # portable checks, tvOS tests, strings, Release build
    python3 PrivateBuild/check.py --all             # also the iOS and Mac compile checks
    python3 PrivateBuild/check.py --quick           # only the steps that need no xcodebuild
    python3 PrivateBuild/check.py --print-commands  # the exact xcodebuild commands, for running one by hand

This file is the one source of the xcodebuild arguments (ad hoc signing, the simulator, the test
timeouts); the notes point here instead of repeating them. Logs and the result bundle go under
PrivateBuild/.build/check/<time>/ (git-ignored). Exit 1 when any step failed. "not run" (skipped by
an option) and "not checked" (its input is absent, e.g. no SwiftPM checkout yet) are not failures.
Nothing here runs on an Apple TV: playing titles on one is that check.
"""
from __future__ import annotations

import argparse
import datetime
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional

ROOT = Path(__file__).resolve().parent.parent
SIMULATOR = "platform=tvOS Simulator,name=Apple TV 4K (3rd generation)"
AD_HOC = ["CODE_SIGN_STYLE=Manual", "CODE_SIGN_IDENTITY=-", "DEVELOPMENT_TEAM=", "PROVISIONING_PROFILE_SPECIFIER="]
PROJECT = ["-project", "Rivulet.xcodeproj", "-onlyUsePackageVersionsFromResolvedFile"]


def xcodebuild_commands(out: Path) -> dict[str, list[str]]:
    """The four xcodebuild invocations. Tests run signed ad hoc: unsigned, the test host has no
    entitlements and every Keychain write fails."""
    return {
        "tvOS tests": ["xcodebuild", "test", *PROJECT, "-scheme", "Reference Player", "-destination", SIMULATOR,
                       "-parallel-testing-enabled", "NO", "-test-timeouts-enabled", "YES",
                       "-default-test-execution-time-allowance", "60", "-resultBundlePath", str(out / "tvos-tests.xcresult"), *AD_HOC],
        "tvOS Release (device)": ["xcodebuild", *PROJECT, "-scheme", "Reference Player", "-configuration", "Release",
                                  "-destination", "generic/platform=tvOS", "CODE_SIGNING_ALLOWED=NO", "build"],
        "iOS build": ["xcodebuild", *PROJECT, "-scheme", "Rivulet iOS", "-destination", "generic/platform=iOS",
                      "CODE_SIGNING_ALLOWED=NO", "build"],
        "Mac build": ["xcodebuild", *PROJECT, "-scheme", "Rivulet Mac", "-destination", "platform=macOS,arch=arm64",
                      "CODE_SIGNING_ALLOWED=NO", "build"],
    }


class Result(NamedTuple):
    name: str
    status: str   # "passed", "failed", "not run", "not checked"
    detail: str


def table(results: list[Result]) -> str:
    width = max(len(result.name) for result in results)
    return "\n".join(f"{result.name:<{width}}  {result.status:<11}  {result.detail}" for result in results)


def exit_code(results: list[Result]) -> int:
    return 1 if any(result.status == "failed" for result in results) else 0


def resolved_checkouts(root: Path) -> Optional[Path]:
    """SourcePackages/checkouts in the DerivedData folder Xcode made for this checkout's project."""
    for info in (Path.home() / "Library/Developer/Xcode/DerivedData").glob("*/info.plist"):
        try:
            workspace = plistlib.loads(info.read_bytes()).get("WorkspacePath", "")
        except (OSError, plistlib.InvalidFileException):
            continue
        checkouts = info.parent / "SourcePackages/checkouts"
        if Path(workspace) == root / "Rivulet.xcodeproj" and checkouts.is_dir():
            return checkouts
    return None


def unmerged_paths(root: Path) -> Optional[list[str]]:
    """Paths git still holds as unmerged (`git ls-files -u`), or None outside a git checkout."""
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "-u"], capture_output=True, text=True)
    if done.returncode != 0:
        return None
    return sorted({line.split("\t", 1)[1] for line in done.stdout.splitlines() if "\t" in line})


def unmerged_result(found: Optional[list[str]]) -> Result:
    if found is None:
        return Result("unmerged paths", "not checked", "not a git checkout")
    if found:
        return Result("unmerged paths", "failed", f"{len(found)} unmerged: {', '.join(found)[:120]} (resolve and `git add` each)")
    return Result("unmerged paths", "passed", "none")


def test_counts(bundle: Path) -> Optional[dict]:
    done = subprocess.run(["xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(bundle)],
                          capture_output=True, text=True)
    try:
        return json.loads(done.stdout) if done.returncode == 0 else None
    except ValueError:
        return None


Runner = Callable[[list[str], Path], tuple[int, str]]


def run_logged(command: list[str], log: Path) -> tuple[int, str]:
    with log.open("w", encoding="utf-8") as handle:
        code = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT).returncode
    text = log.read_text(encoding="utf-8", errors="replace")
    return code, text


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:140] if lines else ""


def verdict(code: int, text: str) -> str:
    """The first PASS line on success; on failure the first FAIL line and how many there are."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fails = [line for line in lines if line.startswith("FAIL")]
    if code == 0:
        return next((line for line in lines if line.startswith("PASS")), last_line(text))[:140]
    return (f"{len(fails)} FAIL lines, first: {fails[0]}" if fails else last_line(text))[:160]


def run_checks(root: Path, out: Path, quick: bool, everything: bool, runner: Runner = run_logged,
               counts: Callable[[Path], Optional[dict]] = test_counts,
               checkouts: Callable[[Path], Optional[Path]] = resolved_checkouts,
               unmerged: Callable[[Path], Optional[list[str]]] = unmerged_paths) -> list[Result]:
    py = sys.executable
    results: list[Result] = []
    commands = xcodebuild_commands(out)

    def step(name: str, command: list[str], log: str, interpret: Callable[[int, str], Result]) -> None:
        print(f"... {name}", flush=True)
        code, text = runner(command, out / log)
        result = interpret(code, text)
        results.append(result)
        print(f"    {result.status}: {result.detail}", flush=True)

    def simple(name: str) -> Callable[[int, str], Result]:
        return lambda code, text: Result(name, "passed" if code == 0 else "failed", verdict(code, text))

    results.append(unmerged_result(unmerged(root)))
    print(f"... unmerged paths\n    {results[-1].status}: {results[-1].detail}", flush=True)
    step("verify.py", [py, str(root / "PrivateBuild/verify.py")], "verify.log", simple("verify.py"))

    def resolved(note: str) -> None:
        """After the step that resolves packages, so a pin bump is checked against its new checkout."""
        found = checkouts(root)
        if found is None:
            results.append(Result("verify.py --resolved-checkouts", "not checked",
                                  "no SwiftPM checkout in DerivedData for this project yet (build once)"))
            return
        step("verify.py --resolved-checkouts", [py, str(root / "PrivateBuild/verify.py"), "--resolved-checkouts", str(found)],
             "verify-checkouts.log", lambda code, text: Result("verify.py --resolved-checkouts",
                                                              "passed" if code == 0 else "failed", f"{verdict(code, text)} ({found}{note})"))

    def python_tests(code: int, text: str) -> Result:
        ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", text, re.M)
        tail = re.search(r"^(OK.*|FAILED.*)$", text, re.M)
        detail = f"{ran.group(1)} tests in {ran.group(2)}s, {tail.group(1)}" if ran and tail else last_line(text)
        return Result("Python tests", "passed" if code == 0 else "failed", detail)
    step("Python tests", [py, "-m", "unittest", "discover", "-s", str(root / "PrivateBuild/tests")], "python-tests.log", python_tests)

    def footprint(code: int, text: str) -> Result:
        status = "failed" if code else ("not checked" if text.startswith("NOT CHECKED") else "passed")
        return Result("footprint.py", status, (text.splitlines() or [""])[0][:140] if status == "not checked" else verdict(code, text))
    step("footprint.py", [py, str(root / "PrivateBuild/footprint.py")], "footprint.log", footprint)

    def xcode(name: str, key: str, log: str, interpret: Optional[Callable[[int, str], Result]] = None,
              skip: Optional[str] = None) -> None:
        if skip:
            results.append(Result(name, "not run", skip))
            return
        step(name, commands[key], log, interpret or (lambda code, text: Result(
            name, "passed" if code == 0 else "failed", f"{'BUILD SUCCEEDED' if code == 0 else last_line(text)} (log {out / log})")))

    compiled = []   # set when this run's tvOS build compiled (the result bundle has a test summary)

    def tests(code: int, text: str) -> Result:
        bundle = out / "tvos-tests.xcresult"
        summary = counts(bundle) if bundle.exists() else None
        if summary is not None:
            compiled.append(True)
        if summary is None:
            return Result("tvOS tests", "failed", f"no result bundle summary (exit {code}); log {out / 'tvos-tests.log'}")
        passed, failed, skipped = (summary.get(key, 0) for key in ("passedTests", "failedTests", "skippedTests"))
        total = passed + failed + skipped + summary.get("expectedFailures", 0)
        ok = code == 0 and failed == 0 and summary.get("result") == "Passed"
        return Result("tvOS tests", "passed" if ok else "failed",
                      f"{passed} of {total} passed, {failed} failed, {skipped} skipped ({bundle})")

    xcode("tvOS tests", "tvOS tests", "tvos-tests.log", tests, "--quick" if quick else None)
    resolved("; --quick: the checkout as it already is, not re-resolved by this run" if quick else "")
    if quick:
        results.append(Result("check-private-strings.py", "not run", "--quick (it reads the string data of a build)"))
    elif not compiled:
        results.append(Result("check-private-strings.py", "not checked",
                              "this run's tvOS build did not compile, so the string data on disk is an older build's"))
    else:
        step("check-private-strings.py", [py, str(root / "PrivateBuild/check-private-strings.py")], "private-strings.log",
             simple("check-private-strings.py"))
    xcode("tvOS Release (device)", "tvOS Release (device)", "release.log", skip="--quick" if quick else None)
    for name, log in (("iOS build", "ios.log"), ("Mac build", "mac.log")):
        xcode(name, name, log, skip="--quick" if quick else (None if everything else "needs --all"))
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="also compile the iOS and Mac apps")
    parser.add_argument("--quick", action="store_true", help="only the steps that need no xcodebuild")
    parser.add_argument("--print-commands", action="store_true", help="print the xcodebuild commands and exit")
    args = parser.parse_args(argv)
    out = ROOT / "PrivateBuild/.build/check" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.print_commands:
        import shlex
        for name, command in xcodebuild_commands(Path("PrivateBuild/.build/check/<time>")).items():
            print(f"# {name}\n{shlex.join(command)}\n")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    print(f"Logs and result bundles: {out}")
    results = run_checks(ROOT, out, args.quick, args.all)
    print("\n" + table(results))
    print(f"\nLogs and result bundles: {out}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
