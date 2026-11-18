#!/usr/bin/env python3
"""Put the committed SwiftPM package traits back into project.pbxproj.

Codemagic's `xcode-project use-profiles` re-saves the project through the
xcodeproj gem, which drops the `traits` attribute it does not know. Without
them AetherEngine builds with object audio and dual-layer Dolby Vision off.
Run it after `use-profiles`; it fails the build if a trait cannot be restored.
"""
import json
import re
import subprocess
import sys

PATH = "Rivulet.xcodeproj/project.pbxproj"
REF = re.compile(r'(\t\t(\w+) /\* XCRemoteSwiftPackageReference "([^"]+)" \*/ = \{\n.*?)(\n\t\t\};)', re.S)
TRAITS = re.compile(r'\n\t\t\ttraits = \(\n.*?\n\t\t\t\);', re.S)

committed = subprocess.run(["git", "show", f"HEAD:{PATH}"], check=True,
                           capture_output=True, text=True).stdout
wanted = {m[2]: (m[3], t[0]) for m in REF.finditer(committed) if (t := TRAITS.search(m[1]))}
with open("PrivateBuild/UPSTREAM.json") as f:
    reviewed = set(json.load(f)["engine"]["required_traits"])
engine = next((block for name, block in wanted.values() if name == "AetherEngine"), "")
if set(re.findall(r"\t\t\t\t(\w+),", engine)) != reviewed:
    sys.exit("Committed AetherEngine traits differ from the fork profile in PrivateBuild/UPSTREAM.json")

with open(PATH) as f:
    project = f.read()

def restore(m):
    if m[2] not in wanted or TRAITS.search(m[1]):
        return m[0]
    return m[1] + wanted[m[2]][1] + m[4]

restored = REF.sub(restore, project)
found = {m[2]: t[0] for m in REF.finditer(restored) if (t := TRAITS.search(m[1]))}
mismatched = [name for ref, (name, block) in wanted.items() if found.get(ref) != block]
if mismatched:
    sys.exit(f"Package traits differ from the committed project for: {', '.join(mismatched)}")
if restored != project:
    with open(PATH, "w") as f:
        f.write(restored)
for name, block in wanted.values():
    print(name, "traits:", ", ".join(re.findall(r"\t\t\t\t(\w+),", block)))
