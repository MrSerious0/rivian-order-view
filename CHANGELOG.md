# Changelog

## 2.0.2 — 2026-11-11

Two stale strings inside the release that shipped as 2.0.1. No behaviour change.

* `make_dist.py`'s own comment still blamed the zip container for recording the builder's platform,
  twelve lines above the `create_system = 3` that stops it doing so. A reader comparing the file to
  its behaviour would have found the code right and the comment wrong.
* The README's token-cache bullet said `mode 0600` while the bullet two below it said, correctly,
  `0600 on POSIX` and that Windows has no equivalent mode.

The version moves because the archive's contents change: the same version number with two
different SHA-256s would break the one thing `SHA256SUMS` is for.

## 2.0.1 — 2026-11-10

A packaging and console fix made before the tool went to anyone outside this machine. No change to
what it reads; the terminal summary uses `->` where it used a Unicode arrow.

**Fixed**

* **A redirected run on Windows produced nothing at all.** The summary printed a `\u2192` arrow in
  the delivery and shipment lines; Windows Python encodes stdout with the ANSI code page whenever
  stdout is not a real console (a pipe, a redirect, a wrapper process), so that character raised
  `UnicodeEncodeError` and killed the run **before** the HTML page and the JSON dump were written —
  `py rivian_order_view.py > out.txt` wrote no page and exited 1. The arrows are `->` now, and
  stdout/stderr degrade unencodable characters instead of raising, which also covers the em dash,
  en dash and the `\u2022` that `--redact` uses for masked ids on a cp437 console (the middle dot
  encodes there; those three do not, and the failure is a crash, not a cosmetic glitch).
  Reproduced on cp1252 and cp437, pinned by four new checks.
* **A missing artifact could satisfy a negative assertion.** The tolerant reader added in the same
  pass made "the VIN is not in the dump" vacuously true when the dump was never written, so a fully
  broken `--redact` would have passed. Each read now records whether the file exists, and reads
  explicitly as UTF-8 (a locale decode of the UTF-8 page could raise instead of reporting).
* **The suite reported a traceback instead of a failure** when a run did not produce its output
  files — the same class that made the defect above look like a crash rather than a result. Every
  artifact read is now tolerant of a missing file, so a systemic failure shows up as FAIL lines.
* **The two `0600` checks are skipped on Windows, not failed.** POSIX file modes do not exist there;
  a skip is reported as a skip and is never counted as a pass.
* **`--redact` made every long identifier identical.** Above 24 characters the mask collapsed the
  head as well as the middle, so two different 36-character UUIDs both rendered as ten bullets —
  which defeats the reason the tail is kept. Long identifiers now keep four characters at each end,
  and a check pins that two of them stay distinguishable.
* **A timestamp that already carried an offset was relabelled, not converted.** `replace(tzinfo=zone)`
  shows the UTC wall clock with a local zone name; within the offset of midnight that moves the day,
  and the weekday is the one thing this line exists to get right. Offset-bearing stamps are now
  converted into the appointment's zone. The sample fixture is naive, so no check could see it —
  the new one constructs an offset stamp, and skips where no zone database is installed.
* **A mistyped `--html`/`--json` path raised a traceback**; it now names the path and stops.

**Added**

* `LICENSE` — MIT, so the code can be lifted into a GPL-3.0 project (a pre-delivery feature in an
  existing logger, say) without asking. Facts in it — endpoints, header names — were never ours to
  license; the one query that is close to the community documentation's example is credited there.
* `.gitignore` and `.gitattributes` — the page and the `--json` dump this tool writes hold your VIN,
  order id and addresses; committed by accident they are in a git history forever. The tracked
  `samples/` pages are synthetic. The attributes keep `*.py`, the sample pages and the checksum lists
  at `eol=lf`, so a Windows clone and a Linux clone carry the same content.
* `make_dist.py` now packages an **explicit list** of files instead of walking the directory, so a
  run's own output page — or any other stray file — cannot end up inside the distributable zip, and
  it normalises line endings so a Windows and a Linux checkout carry the same content.
* The archive now carries **`MANIFEST.sha256`**, one hash per file inside it, because the claim it
  replaces could not be kept: the zip's own SHA-256 varies with the zlib the building Python is
  linked against, so "the same source always produces the same bytes" was false on any other toolchain.
  `SHA256SUMS` still proves the archive is the published one; the manifest is the content check —
  and it only says anything when you point the **published** manifest at your own build, since a
  build's own copy is generated from the bytes it just packaged.
* README: what actually passes per platform, a **porting** table pointing at the reusable
  constants, the auth sequence and the synthetic fixture, and an explicit scope line — the API
  documentation this was built from (`kaedenbrinkman/rivian-api`) carries no license, so MIT here
  covers this tool's code only.

## 2.0.0 — 2026-11-06

Everything below comes from running 1.x against a real order from reservation to delivery, and
from reading the server's own answers rather than assuming them.

**New**

* `--from-json PATH` — render a saved payload with no network and no credentials. Makes the tool
  demonstrable (and bug reports reproducible) without anyone handing over an account.
* `--redact` — masks identifiers (order id, VIN, vehicle id, appointment id, fulfillment and
  trade-in ids, tracking numbers and URLs, email, names, addresses) in the text, the HTML and the
  JSON dump, **including the `snapshots` block and the order ids printed to the terminal**, and
  banners the page as redacted. Masking falls back to value shapes (17-character VINs, UUIDs,
  tracking URLs) so a renamed field cannot leak, and masks non-string values too.
* `--self-test` — offline checks of the renderer, the date handling and the redaction.
* **Everything it writes is `0600`**, including the HTML page and the JSON dump, and the token
  cache is created `0600` rather than chmod-ed afterwards.
* `--version`.
* `samples/` — a synthetic payload plus two rendered pages (normal and redacted).
* `test_rivian_order_view.py` — 94 checks covering helpers, per-item fees, the journey, redaction,
  HTML escaping, and the CLI surface.

**HTML page**

* New **Delivery** section: resolver status, carrier, appointment window with the weekday
  **computed in code**, the raw ISO timestamps, the `timeZone` verbatim, and the delivery address.
* New **Itemized taxes & fees** table: every line with its contract code and rate where the
  schema provides one, plus a split between Rivian's own money (destination, documentation) and
  state/DMV pass-through.
* New notes: how to read the status ladders, and what the page cannot show (pre-provisioning
  empty account, no location field, record-level timestamps).
* Tiles now include the computed pickup window and the pending-item count.
* Shipments show shipped and delivered dates, not just the window.
* Responsive: four tiles across on a desktop, two on a phone.

**Correctness**

* **The delivery resolver works — the 1.x README was wrong.** It does not refuse these sessions
  as a matter of policy; it refuses an **expired** token (`User is unauthenticated`). Since the
  order endpoint tolerates an expired token, the appointment used to vanish silently. 2.0
  refreshes before querying and distinguishes `err` from `none`.
* Handles `CSRF_TOKEN_EXPIRED`: an orders query carrying only a Bearer token answers HTTP 200
  with `data: null` and a message that is indistinguishable from "no such order". The tool
  re-mints the CSRF pair and retries once.
* Correct per-item `fees` / `taxes` selection: `OrderItemFeeItem` has no `rate`/`type`, and
  asking for them is a hard HTTP 400.
* `--from-json` refuses a file that is not a payload, with a message that says what is missing.
* Every value is HTML-escaped (tested with a `</script>` payload in the order id).
* **Tolerant of a changed payload**: a null `items`, a decimal-string amount, a string where an
  object belongs, a null entry in a list, or a `tracking` field that is not an object all render
  as a dash instead of a traceback.
* The text summary no longer prints bare Python `None`, the itemized table labels its column
  `tax` (it used to say `taxe`), and the summary's "paid" figure is the API's `paidTotal` with the
  sum of recorded payments shown separately (it used to count a `PROCESSING` ACH as paid).
* `--no-personal` now really omits postal addresses, including the delivery address.

**Docs**

* README rewritten around what the API will and will not tell you: build-date semantics, status
  ladders, the pre-provisioning empty account, the dead checklist service, the cash-vs-financing
  distinction, and rate-limit guidance.

## 1.2.0

* Automatic access-token refresh (`refreshAccessToken`, apollo-ios client name required),
  because the delivery resolver stops answering with an expired token.
* Delivery data in the summary and the page: status, carrier, appointment window.
* `pretty_dt` / `pretty_time` for human-readable timestamps.
* `err` vs `none` wording for the delivery resolver.

## 1.1.0

* HTML page output alongside the text summary.
* Per-item build configuration with option pairs.

## 1.0.0

* First release: order, vehicle record, build dates, factory status, payments, fulfillments,
  NHTSA VIN decode, endpoint report.
