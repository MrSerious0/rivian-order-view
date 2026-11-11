# package — the shippable artifact

`rivian_order_view_2.0.2.zip` is the distributable form of the tool in the parent directory: the
same files, zipped reproducibly, so a person can fetch one file instead of cloning the repo.

```
rivian_order_view_2.0.2.zip     sha256 recorded in SHA256SUMS
SHA256SUMS                      one line: <sha256>  <filename>
```

## Get it

The archive is committed in the repository beside this file, so a clone already has it:

```bash
# from the root of a clone of the repository that publishes this file
git show HEAD:package/rivian_order_view_2.0.2.zip > rivian_order_view.zip
```

(If you received the file directly, skip this — you already have it.)

## Verify before trusting it

```bash
cd package
grep rivian_order_view_2.0.2.zip SHA256SUMS | sha256sum -c -
# rivian_order_view_2.0.2.zip: OK
```

(`sha256sum -c` resolves the name in `SHA256SUMS` against the current directory, which is why that
`cd` is there — the file lists a bare filename.)

That proves the file you have is the file that was published. To check the **contents**, the
archive carries a manifest listing a hash for every file in it (every file but itself):

```bash
unzip -q rivian_order_view_2.0.2.zip
cd rivian_order_view_share && sha256sum -c MANIFEST.sha256
```

The zip's own hash is not reproducible across machines: the platform id is pinned, but zlib's
output changes with the zlib a given Python is linked against. The manifest is the check that
survives that.

Then read the `README.md` inside — that is the actual documentation. The archive holds one folder,
`rivian_order_view_share/`, with the tool, its docs, its tests (including the console-encoding
checks that pin the Windows fix in 2.0.1), the packaging script, the MIT `LICENSE`, a `.gitignore`,
a `.gitattributes`, `MANIFEST.sha256`, two synthetic payloads and two pre-rendered pages.

## Rebuild it yourself

The zip is built **from an explicit list of files in the parent directory** (not a walk of it, so a
run's own output page cannot end up inside), with sorted entries, a fixed timestamp, normalised
permissions and normalised line endings:

```bash
cd .. && python3 make_dist.py     # writes the zip and package/SHA256SUMS; the manifest goes inside
```

To check a rebuild against what was published, point the **received** manifest at your build —
your own archive's copy was generated from the same bytes it packaged, so checking that one always
passes and proves nothing:

```bash
cd rivian_order_view_share && sha256sum -c <received>/MANIFEST.sha256
```

## What it is, in one paragraph

A read-only CLI that logs into Rivian's app API the way the mobile app does, reads your own order,
and prints a summary plus a self-contained HTML page: vehicle record and all four build dates,
factory status, the configuration as ordered, money with itemized tax and fee lines, payments and
fulfillments, the delivery appointment (weekday computed in code, `timeZone` verbatim) and address,
and an independent NHTSA VIN decode. It sends no vehicle commands, stores no password, and writes
its artifacts `0600` on POSIX (Windows has no equivalent file mode — there the files are ordinary
user files). `--redact` masks identifiers for sharing; `--from-json` renders a saved payload with no
network at all.

Offline proof it works: `--self-test` 31/31; the full suite is 105 checks — on Windows 103 pass and
2 are skipped with `tzdata` installed, 102 pass and 3 are skipped without it (a skip is never
counted as a pass).
