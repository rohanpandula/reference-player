"""Resolve the recurring project.pbxproj conflict of an upstream merge: upstream bumps
MARKETING_VERSION next to the PRODUCT_BUNDLE_IDENTIFIER line this fork's identity changed.
Keep upstream's version and this checkout's identifier (PrivateBuild/identity.json). Any other conflict shape is refused, and
the file is written only when every hunk resolved. Explicit checks, not `assert`: `python -O`
must not turn the refusal off."""
import json, pathlib, sys


def refuse(message):
    sys.exit(f"refused, nothing written: {message}")


p = pathlib.Path(sys.argv[1]); lines = p.read_text().split("\n")
identity = pathlib.Path(__file__).resolve().parent / "identity.json"
try:
    ours_id = json.loads(identity.read_text())["bundle_id"]
except (OSError, ValueError, KeyError, TypeError) as exc:
    refuse(f"cannot read the bundle identifier from {identity}: {exc}")

out, i, n = [], 0, 0
while i < len(lines):
    if not lines[i].startswith("<<<<<<<"):
        out.append(lines[i]); i += 1; continue
    mid = next((k for k in range(i, len(lines)) if lines[k].startswith("=======")), None)
    end = next((k for k in range(mid, len(lines)) if lines[k].startswith(">>>>>>>")), None) if mid is not None else None
    if end is None:
        refuse(f"unterminated conflict at line {i + 1}")
    ours, theirs = lines[i + 1:mid], lines[mid + 1:end]
    def shape(block):
        """Both sides must be exactly a MARKETING_VERSION line then a PRODUCT_BUNDLE_IDENTIFIER line."""
        keys = [l.strip().split(" = ", 1)[0] for l in block]
        if keys != ["MARKETING_VERSION", "PRODUCT_BUNDLE_IDENTIFIER"] or not all(" = " in l for l in block):
            refuse(f"unexpected conflict shape at line {i + 1}: {block}")
        return block
    version, _ = shape(theirs)
    _, bundle = shape(ours)
    value = bundle.split(" = ", 1)[1].strip().rstrip(";").strip('"')
    if value != ours_id and not value.startswith(ours_id + "."):
        refuse(f"our side is not this checkout's bundle identifier ({ours_id}) at line {i + 1}: {bundle}")
    out += [version, bundle]
    n += 1; i = end + 1
if n:
    p.write_text("\n".join(out))
print(f"resolved {n} project-file conflicts")
