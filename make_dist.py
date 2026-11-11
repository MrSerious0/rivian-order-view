#!/usr/bin/env python3
"""Build the distributable zip from the explicit PAYLOAD list below.

    python3 make_dist.py            # writes package/rivian_order_view_<version>.zip + SHA256SUMS

Three properties matter and all three are deliberate:

  * **Provenance.** The zip is built from the explicit list below, so it always matches the working
    tree it was built from — and line endings are normalised, so a CRLF checkout and an LF one carry
    the same content.
  * **A check that survives the container.** The archive carries `MANIFEST.sha256`, one line per
    file, so a recipient can verify the CONTENT on any platform: extract, then
    `sha256sum -c MANIFEST.sha256`. Byte-identity of the zip itself is only promised for the same
    toolchain — the platform id is pinned, but zlib output varies with the zlib a given Python is
    linked against — so the docs do not claim otherwise.
  * **Reproducibility within a toolchain.** Entries are sorted, timestamps and permissions fixed
    and the platform id pinned, so the same source on the same Python/zlib produces the same bytes.

Standard library only — same rule as the tool itself.
"""
import hashlib
import pathlib
import re
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
DIST = HERE / "package"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)          # zip epoch-ish, so the archive is byte-stable
PREFIX = "rivian_order_view_share"           # folder name inside the archive
MANIFEST = "MANIFEST.sha256"                # per-file hashes, generated; the cross-platform check

# What ships, by name. An explicit list, not a walk of the directory: a walk also packages
# whatever happens to be sitting here — a run's own `rivian_order_view.html` (VIN, order id,
# home address), an editor backup, an agent's scratch output. The archive is the artifact a
# stranger trusts, so its contents are auditable at a glance and nothing arrives by accident.
PAYLOAD = (
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "make_dist.py",
    "rivian_order_view.py",
    "test_rivian_order_view.py",
    "samples/order_payload.bad.json",
    "samples/order_payload.sample.json",
    "samples/sample_report.html",
    "samples/sample_report.redacted.html",
)


def version():
    text = (HERE / "rivian_order_view.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("could not find VERSION in rivian_order_view.py")
    return match.group(1)


def build():
    missing = [rel for rel in PAYLOAD if not (HERE / rel).is_file()]
    if missing:
        raise SystemExit("payload file(s) missing: {}".format(", ".join(missing)))
    DIST.mkdir(exist_ok=True)
    out = DIST / "rivian_order_view_{}.zip".format(version())

    # The payload bytes, line endings normalised so a CRLF checkout and an LF one carry the same
    # content. (That does NOT make the zip byte-identical across toolchains — the platform id is
    # pinned below, but zlib's output varies with the zlib a given Python is built against. The
    # manifest is the check that survives that, and the README says so.)
    payload = {}
    for rel in sorted(PAYLOAD):
        payload[rel] = (HERE / rel).read_bytes().replace(b"\r\n", b"\n")

    manifest = "".join("{}  {}\n".format(hashlib.sha256(payload[rel]).hexdigest(), rel)
                       for rel in sorted(payload))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(payload):
            info = zipfile.ZipInfo("{}/{}".format(PREFIX, rel), FIXED_TIME)
            info.create_system = 3          # pin: CPython otherwise records 0 on Windows, 3 elsewhere
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, payload[rel])
        info = zipfile.ZipInfo("{}/{}".format(PREFIX, MANIFEST),
                               FIXED_TIME)
        info.create_system = 3
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, manifest)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    # newline="\n" is not cosmetic: with the default translation this file is written CRLF on
    # Windows, which makes `sha256sum -c SHA256SUMS` fail on the trailing CR ("No such file or
    # directory") — i.e. the provenance check the README tells the reader to run breaks on the
    # machine that built the archive.
    with open(DIST / "SHA256SUMS", "w", encoding="utf-8", newline="\n") as sums:
        sums.write("{}  {}\n".format(digest, out.name))
    print("built {} ({:,} bytes, {} files + {})".format(
        out, out.stat().st_size, len(PAYLOAD), MANIFEST))
    print("sha256 {}  (of the zip; {} is the per-file check)".format(digest, MANIFEST))
    return out, digest


if __name__ == "__main__":
    build()
