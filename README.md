# Rivian order status viewer

A single-file, read-only tool that shows what **your own** Rivian order record actually
contains — including the build information the app's order page buries — and writes a clean,
self-contained HTML page you can keep, screenshot or share.

It answers questions like: *has my car been built? what's my VIN? did it pass the factory gate?
when is my pickup, and where? how much have I paid and what exactly is in the fees?*

**Version 2.0.2** · MIT licensed · Python 3.8+ · standard library only · read-only · unofficial.

## Requirements

Python 3.8 or newer. **Nothing else** — no `pip install`, no accounts, no dependencies.
Check with `python3 --version` (Windows: `py --version`).

## Run it

1. Save `rivian_order_view.py` somewhere.
2. Open a terminal in that folder and run:

   ```
   python3 rivian_order_view.py
   ```

   On Windows that is usually `py rivian_order_view.py` (`python3` only exists if the Microsoft
   Store alias is installed).

3. It asks for your Rivian account **email**, then your **password**, and — if your account
   uses two-factor — the one-time code Rivian sends you.
4. It prints a summary and writes `rivian_order_view.html` in the **current directory** (it
   prints the full path). Open that file in any browser.

Repeat runs reuse the cached session; `--relogin` forces a fresh login.

### Try it without an account

The repo ships a synthetic payload, a deliberately-broken one and two rendered pages:

```
python3 rivian_order_view.py --from-json samples/order_payload.sample.json
```

`--from-json` renders a saved payload with **no network and no credentials**, which is also how
you re-render an old `--json` dump or hand a reproducible case to someone else.
`samples/sample_report.html` is the result; `samples/sample_report.redacted.html` is the same
page through `--redact`. Every value in the sample payload is invented (`Exampleville`, `1 Example
Way`, `example-owner@example.com`, VIN `7PD2EAAB4VN000001`). The footer of the rendered pages
carries the real time and zone name of the machine that rendered them, which is also why those two
files are not byte-reproducible across platforms.

## Options

| Flag | What it does |
| --- | --- |
| `--tokens FILE` | reuse a session cached elsewhere |
| `--email ADDRESS` | account email instead of the prompt |
| `--relogin` | ignore the cache and log in again |
| `--order ID` | inspect a specific order when the account has several |
| `--html PATH` | where to write the page (default `rivian_order_view.html`) |
| `--no-html` | text summary only |
| `--json PATH` | also dump the raw API payload (snapshots, order, delivery, checklist, VIN decode) |
| `--from-json PATH` | render from a saved payload — no network, no credentials |
| `--redact` | mask identifiers (order id, VIN, vehicle id, appointment id, fulfillment/trade-in ids, tracking numbers and URLs, email, names, addresses) in the text, the page **and** the JSON dump — including the `snapshots` block and the ids printed to the terminal |
| `--no-personal` | omit the contact block, the order email and the delivery/shipping addresses **from the text and the page** — the `--json` dump still contains them, so use `--redact` on anything you share from it |
| `--no-vin-decode` | skip the NHTSA VIN lookup (keeps all traffic to rivian.com) |
| `--self-test` | offline checks of the renderer and redaction, no credentials |
| `--version` | print the version |

A cached-session run is about **five requests** (four to Rivian, one to NHTSA), seven on a first
run, eight if a one-time code is needed, plus one extra whenever the session is refreshed.
**10–15 minutes between runs is plenty** — the status fields jump rather than tick, so polling
faster buys nothing.

## What the page shows

| Section | Contents |
| --- | --- |
| Tiles | order state, configuration status, fulfillment status, VIN, actual vs planned build, factory status, delivery status, carrier, pickup window, balance, total, pending-item count |
| The journey so far | order placed, each payment, planned/expected build dates, the actual general assembly, the scheduled pickup — sorted by date |
| Vehicle record | Rivian's internal vehicle id (**not** a VIN), the VIN, model year/make/model, all four build dates, vehicle status |
| VIN decode | an independent NHTSA vPIC decode: make, model, year, trim, drive type, plant city/state, body class, electrification level |
| Delivery | resolver status, carrier, the appointment window **with the weekday computed in code**, the raw ISO timestamps and the `timeZone` verbatim, and the delivery address. The zone renders as a short name (`EST`) when the optional `tzdata` package is installed and as the IANA key verbatim (`America/Indiana/Indianapolis`) when it is not — Windows typically has no `tzdata`, so both forms appear in the wild |
| Build configuration | every ordered item and its option pairs by group (`PAINT`, `INT`, `WHL`, `LPKG`, `TOW`, `AUTO`, …) |
| Itemized taxes & fees | every line with the contract's own code and its rate where the schema provides one, plus a split between Rivian's own money (`DELIVERY_FEE`, `DOCUMENTATION_FEE`) and state/DMV pass-through |
| Order money | subtotal, discount, tax, fees, paid, remaining, outstanding, cost after credits, total |
| Payments | each record with date, amount, status (`SUCCEEDED` / `PROCESSING` / `CANCELED`), method, intent |
| Shipments | fulfillment status, carrier, tracking number, shipped/delivered dates, delivery window |
| Pending fulfillment items | how many item records are still pending (quantities only — the build configuration above is what is on the order) |
| Contact & addresses | order email, locale, store id, billing and shipping addresses (dropped by `--no-personal`) |
| Endpoints tried | what each surface answered, and why a refusal is not the same as "nothing there" |

The page is dark-themed, four tiles across on a desktop and two on a phone, and it uses plain
tables everywhere else. Every value is HTML-escaped, and the page is self-contained: no fonts,
images or scripts are fetched.

## Sharing what it produces

The page contains your VIN, order id and (if present) addresses — treat it as personal data. So
does the `--json` dump, which additionally carries the order email and shipment tracking numbers.
`.gitignore` covers the default names; it cannot know a path you chose yourself, so keep both out
of any repository.
For anything you intend to publish or attach to a bug report:

```
python3 rivian_order_view.py --redact
```

That masks the order id, VIN, vehicle id, appointment id, fulfillment and trade-in ids, tracking
numbers and their URLs, email, names and addresses — in the text, the page **and** the JSON dump,
including the `snapshots` block and the order ids printed to the terminal — while leaving every
status and amount intact, and puts a banner on the page saying it is redacted. Every masked
identifier keeps its last four characters, and one longer than 24 characters keeps its first four as
well, so two records can still be told apart in a support thread. `--no-personal` additionally drops
the contact block and the addresses from the summary and the page — the `--json` dump keeps them, so
redact that one instead. `--from-json … --redact` does the same to a payload you already have,
offline.

## Session handling (the 2-hour trap)

Rivian access tokens expire after about **two hours**. The order endpoint keeps answering
anyway, so an expired session looks fine — but the **delivery resolver refuses it** with
`INTERNAL_SERVER_ERROR: User is unauthenticated`, and the pickup date, carrier and address
silently disappear. This tool checks the token's expiry before every run and renews it from the
stored refresh token (valid for months, no OTP needed), so you do not have to log in again every
two hours. If the refresh itself is rejected it says so and points at `--relogin`.

## `none` vs `err`

* `none` — the API answered, and there is genuinely nothing there yet
* `err` — the sub-query failed, so **no answer is known** (this is not an absence)

Confusing the two is how a scheduled pickup gets reported as "not scheduled". The output labels a
refused resolver explicitly.

## Reading the dates and statuses correctly

`actualGeneralAssemblyDate` is what physically happened. `plannedBuildDate` and
`expectedBuildDate` are schedules and are revised regularly — a planned date *after* the actual
build date is normal, not an inconsistency.

Status strings are quoted exactly as the API returns them, because that is what a bug report
needs. The observed ladders are `PENDING_FULFILLMENT → READY_FOR_FULFILLMENT` on the order and
`FACTORY_OK → FACTORY_GATED → STAGED_FOR_PDI → PDI_COMPLETED → VEHICLE_PREPPED` on the vehicle.
Steps can be skipped — `IN_TRANSIT_*` may never appear at all — and the published state lags the
physical car by days. Two statuses minutes apart measure a database write, not the work behind
it.

The order's own state (`ORDERED / SOFT_LOCKED / PENDING_FULFILLMENT`) can lag the physical build
by weeks. A car can be built, factory-gated, and still read `PENDING_FULFILLMENT`: do not read
that as "not built yet" when a VIN and an actual build date are present.

## What it cannot see (honest limits)

* **The 8-step delivery checklist** (`transactionStatus`: title and registration, trade-in,
  finance, delivery, insurance, documents, contracts, payment) has answered **HTTP 503 to every
  request** — GET and POST alike — since early September 2026. It is reported as unavailable.
* **Anything about the car itself.** Before Rivian provisions the vehicle on your account,
  `currentUser.vehicles` is empty and the vehicle-state surface answers with an all-null skeleton
  (observed separately; this tool does not query that surface). Live telemetry — state of charge, locks, location — is a different,
  post-provisioning API surface and out of scope here.
* **Where the car physically is.** No field names a location. The PDI/prep statuses are evidence
  that it reached your delivery centre; they are not a location, and the store's own answer wins.
* **Names of pending items** — that list carries quantities only. The "build configuration" table
  shows what is actually on the order.
* **The amount due at pickup if you are financing** — `outstandingBalance`/`total` are the cash
  view; the down payment is not modelled by the API.

## Field notes worth knowing

* **`fees` and `taxes` are per item, and their shapes differ.** `items[].fees` carries
  `description`, `amount`, `code`. `items[].taxes` carries `description`, `amount`, `code`,
  `rate`, `type`. Asking for `rate` on a fee item is a hard validation error (HTTP 400), which is
  why the two selections in this tool are deliberately different.
* **`feesTotal` mixes two kinds of money.** `DELIVERY_FEE` (destination) and `DOCUMENTATION_FEE`
  are Rivian's; the DMV lines are state fees collected and passed through. The tool splits them.
* **The order-level `vin` stays null for weeks.** The authoritative VIN is `order.vehicle.vin`.
* **`orderSnapshots` exists only on the orders host.** Against the gateway it answers
  `GRAPHQL_VALIDATION_FAILED`, which reads exactly like "no orders".
* **One CSRF pitfall.** An orders query carrying only the Bearer token answers HTTP 200 with
  `data: null` and `{"message": "Request a new CSRF token", "extensions": {"code":
  "CSRF_TOKEN_EXPIRED"}}` — indistinguishable from "no such order" unless you look. This tool
  detects it, re-mints the CSRF pair and retries once.
* **Headers, precisely.** It sends the app's `User-Agent`, an `Apollographql-Client-Name` per
  call, and a `Dc-Cid: m-ios-<uuid>` that is a **fresh UUID per login** rather than a copied
  constant. It does **not** send `X-Client-Id`: the orders host answers identically without it,
  and an unexplained constant in shared code is a liability, not a requirement.

## Privacy and safety

* **Read-only.** It mints a CSRF token, logs in, and reads. It cannot lock/unlock, start
  charging, or change anything on the account.
* **Your password is never stored** — not on disk, not in the HTML, not in the JSON dump.
* **Session tokens are cached** in `~/.rivian_order_view/tokens.json`, mode `0600` on POSIX, so repeat
  runs skip the login. Delete that folder to forget the session.
* **Nothing is sent anywhere except `rivian.com`** (plus the optional public NHTSA VIN decode,
  which `--no-vin-decode` removes). No telemetry, no third-party service.
* **The HTML page and the JSON dump are written `0600` on POSIX** — Windows has no equivalent file
  mode, so there they are ordinary user files. Neither contains a credential, only the order data
  above.
* **The token cache is a credential store.** `~/.rivian_order_view/tokens.json` holds five session
  tokens, including a refresh token valid for months. It is written `0600` on POSIX too, and it is
  the one file here you should never paste into a bug report, a backup or a dotfile repository.

## Troubleshooting

| Message | Meaning |
| --- | --- |
| `login failed` | wrong email/password. It deliberately does **not** retry — repeated attempts can lock the account |
| `code rejected` | the one-time code expired; run it again for a new one |
| `could not list your orders … re-run with --relogin` | no orders on the account, or the cached session expired |
| the line starting `Delivery` reads `err — the resolver refused` | the token was not live when queried; check the `token:` line, then `--relogin` |
| the line starting `Checklist` reads `unavailable — http 503` | expected — that service is down at Rivian's end |
| `note: could not refresh the session` | the refresh token is no longer valid; `--relogin` |
| VIN shows `—` | no vehicle record exists on that order yet: not assigned to a build |
| `could not read order …` | Rivian changed the API shape; keep the `--json` output and check for a newer version of this tool |

## Tests

```bash
python3 rivian_order_view.py --self-test    # renderer + redaction, offline
python3 test_rivian_order_view.py           # full suite: helpers, fees, journey, redaction,
                                            # HTML escaping, CLI, --from-json, --redact
```

Both run with no network access and no account. The suite renders
`samples/order_payload.sample.json`, whose values are all invented.

**What passes, precisely:** `--self-test` is 31/31. The full suite is 105 checks. Measured on the
Windows box this was built on: **103 pass, 0 fail, 2 skipped** with an interpreter that has the
optional `tzdata` package, and **102 pass, 3 skipped** without it — the two `0600` assertions skip
because POSIX file modes do not exist on Windows, and one skips when no time zone can be resolved.
A skip is reported as a skip and is never counted as a pass. It has not been re-run on Linux/macOS
since the 2.0.1 changes; run it on your platform and you will get a number rather than a claim. No check needs the network or an account. The fixture
set is four files: `samples/order_payload.sample.json` (a full synthetic order),
`samples/order_payload.bad.json` (a payload with no order, which must be refused) and the two
pre-rendered pages (nothing reads those; they are there to be looked at).

## License and reuse

**MIT** — see `LICENSE`. That is deliberate: it is compatible with a GPL-3.0 project, so the
endpoints, the query text and the header recipes here can be lifted into one directly. Credit in
your own README is welcome but not required; keep this `LICENSE` with substantial copies. The one
exception is the third-party material described under *Where this comes from* below — that
documentation is not ours and is not licensed by this file.

## If you are porting this to another language

The parts worth copying live at the top of `rivian_order_view.py`, in this order:

| What | Where |
| --- | --- |
| Endpoints + the app's `User-Agent` | `GATEWAY`, `ORDERS`, `T2D`, `VPIC`, `UA` |
| The four named queries (the four auth mutations live inline in the next row) | `SNAPSHOT_QUERY`, `ORDER_QUERY`, `DELIVERY_QUERY`, `T2D_QUERY` |
| Header recipe (CSRF, `A-Sess`, `U-Sess`) | `base_headers`, `api_headers` |
| The fresh `Dc-Cid` per login | inline in `login()` — not one of the two header helpers |
| Auth sequence: CSRF mint → login → refresh | `create_csrf`, `login`, `refresh_access_token` |
| The 2-hour token rule and the renewal margin | `refresh_access_token` |
| Which response blocks the page reads | `order`, `delivery`, `checklist`, `vin_decode` in the `--json` payload |
| A pre-delivery fixture for tests and screenshots | `samples/order_payload.sample.json` (synthetic; every value invented) |

The behavioural traps are the ones in **Field notes worth knowing** above — the per-item `fees`
vs `taxes` shapes, `orderSnapshots` existing only on the orders host, the CSRF expiry that answers
HTTP 200 with `data: null`, and the delivery resolver refusing an expired token. Those are the
things a re-implementation gets wrong silently.

## Packaging and provenance

`make_dist.py` builds `package/rivian_order_view_<version>.zip` from an explicit list of files in
this directory — sorted entries, fixed timestamps, normalised permissions and line endings — and
writes `package/SHA256SUMS` beside it. The list is explicit rather than a directory walk on purpose:
a walk also sweeps up whatever happens to be sitting in the folder, and this tool's own output page
carries a VIN, an order id and addresses.

Two checks, for two different questions:

* **`SHA256SUMS`** hashes the archive itself: the file you received is the one that was published.
  Byte-for-byte reproduction is only promised on the same toolchain — the platform id is pinned, but
  zlib's output changes with the zlib a given Python is linked against — so a mismatch on a
  different machine is not evidence of anything.
* **`MANIFEST.sha256`, inside the archive,** lists a hash for every file in it (every file but
  itself). Extract the archive you received, then point **that** manifest at your build:

  ```bash
  unzip -q <received>.zip            # Windows: expand it, or use `tar -xf`
  cd <your build>/rivian_order_view_share && sha256sum -c <received>/MANIFEST.sha256
  ```

  The copy inside your own build was generated from the bytes it just packaged, so checking that one
  always passes — it proves nothing about whether your content matches the published content.

In the repository, `package/README.md` has the fetch recipe and the rebuild steps.

## Where this comes from

The endpoint map, the field names, the header recipes and the starting shape of the queries come
from the community documentation at <https://rivian-api.kaedenb.org/app/> (published as
[kaedenbrinkman/rivian-api](https://github.com/kaedenbrinkman/rivian-api) — that is where the credit
belongs), plus live verification against a real account: the `order` selection here is not the
site's example, having been reduced and extended after the live schema disagreed, and the
`delivery` one is theirs minus the introspection fields.

**That documentation carries no license of its own, so the MIT license in this repository covers
this tool's code alone** — not the API knowledge it was built from. If you reuse this tool, credit
that project for the API map; the bugs and the corrections here are ours.

Field names are undocumented and can change without notice; the tool fails with an explicit
message rather than showing wrong values.

Not affiliated with, endorsed by, or supported by Rivian. Use it on your own account, at your own
risk, and do not hammer the endpoints.
