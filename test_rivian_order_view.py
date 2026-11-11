#!/usr/bin/env python3
"""Offline test suite for rivian_order_view.py — no network, no credentials, no Rivian account.

    python3 test_rivian_order_view.py

Covers the pure presentation helpers, redaction, HTML/summary rendering from the synthetic
fixture in samples/, HTML escaping (a shared tool renders untrusted-ish strings), the CLI surface
(--from-json, --no-html, --redact, --version, --self-test), and console encoding: the same render
is run under cp1252 and cp437, which is where a redirected Windows run used to die.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE / "rivian_order_view.py"
FIXTURE = HERE / "samples" / "order_payload.sample.json"

spec = importlib.util.spec_from_file_location("rov", TOOL)
rov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rov)

PAYLOAD = json.loads(FIXTURE.read_text())
ORDER = PAYLOAD["order"]
DELIV = PAYLOAD["delivery"]
T2D = PAYLOAD["checklist"]

results = []


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, detail))


def skip(name, reason):
    results.append(("SKIP", name, reason))


def check_posix(name, ok, detail=""):
    """A POSIX-only assertion is SKIPPED on Windows, never faked into a pass."""
    if os.name == "nt":
        skip(name, "POSIX file modes do not exist on Windows")
    else:
        check(name, ok, detail)


# ---------------------------------------------------------------- helpers
check("money formats a float", rov.money(1234.5) == "$1,234.50")
check("money is tolerant of decimal strings", rov.money("12") == "$12.00" and rov.money("$1,495.00") == "$1,495.00")
check("money refuses junk", rov.money(None) == "\u2014" and rov.money("abc") == "\u2014")
check("pretty_dt computes the weekday", rov.pretty_dt("2030-01-05T14:00:00") == "Sat 05 Jan 2030, 2:00 PM")
check("pretty_time drops the leading zero", rov.pretty_time("2030-01-05T09:05:00") == "9:05 AM")
check("pretty_dt survives junk", rov.pretty_dt("not-a-date") == "not-a-date")

APPT = DELIV["data"]["appointmentDetails"]
line = rov.appt_local(APPT)
check("appointment line: weekday + window computed",
      line.startswith("Sat 06 Feb 2027, 10:30 AM \u2013 10:45 AM"), line)
check("appointment line carries a zone",
      line.endswith("EST") or line.endswith("America/Indiana/Indianapolis"), line)
check("appointment line empty without a start", rov.appt_local({"endDateTime": "x"}) == "")
check("appointment line tolerates a bogus zone",
      rov.appt_local({"startDateTime": "2030-01-05T14:00:00", "timeZone": "Not/AZone"}).startswith("Sat 05 Jan 2030"))

# A stamp that already carries an offset must be converted INTO the appointment's zone. Using
# replace(tzinfo=...) on it relabels the UTC wall clock instead, which shifts the day — and the
# computed weekday, the whole point of this line — near midnight.
try:
    from zoneinfo import ZoneInfo
    ZoneInfo("America/New_York")
    HAVE_TZDATA = True
except Exception:
    HAVE_TZDATA = False
if HAVE_TZDATA:
    check("an offset timestamp is converted into the appointment zone",
          rov.appt_local({"startDateTime": "2030-01-05T15:30:00Z", "endDateTime": "2030-01-05T15:45:00Z",
                          "timeZone": "America/New_York"}).startswith("Sat 05 Jan 2030, 10:30 AM"),
          rov.appt_local({"startDateTime": "2030-01-05T15:30:00Z", "timeZone": "America/New_York"}))
else:
    skip("an offset timestamp is converted into the appointment zone",
         "no tzdata on this machine, so no zone can be resolved")

# ---------------------------------------------------------------- fees
rows = rov.fee_rows(ORDER)
codes = {r[2] for r in rows}
check("fee_rows picks up both blocks", {"DELIVERY_FEE", "TITLE_FEE", "IN_SALES_TAX_USE_TAX"} <= codes)
check("fee_rows carries the tax rate", any(r[5] == 0.07 for r in rows))
check("fee rows are labelled fee/tax, never 'taxe'",
      {r[1] for r in rows} == {"fee", "tax"} and all("taxe" != r[1] for r in rows))
check("fee_rows tolerates a null fees block",
      all(r[3] is not None for r in rows) and len([r for r in rows if r[0] == "R2 Cargo Cover"]) == 1)
rivian, passthrough = rov.fee_split(ORDER)
check("fee_split separates Rivian money", (rivian, passthrough) == (1872.0, 1301.6), (rivian, passthrough))

# ---------------------------------------------------------------- journey
road = rov.journey(ORDER, DELIV)
titles = [r[2] for r in road]
check("journey has the order, a payment, the build and the pickup",
      "Order placed" in titles and "BUILT \u2014 general assembly" in titles
      and "PICKUP SCHEDULED" in titles and any(t.startswith("Payment") for t in titles))
check("journey pickup detail is human-readable",
      any("Sat 06 Feb 2027, 10:30 AM" in r[3] for r in road if r[2] == "PICKUP SCHEDULED"))
NO_SHIP = {**ORDER, "fulfillmentInfo": {"fulfillments": [], "pendingFulfillmentItems": []}}
check("journey notes a missing shipment",
      any(r[2] == "Shipping / tracking" for r in rov.journey(NO_SHIP, {"data": {}})))
check("journey notes a refused resolver",
      any("resolver refused" in r[3] for r in rov.journey(NO_SHIP, {"error": "User is unauthenticated"})))

# ---------------------------------------------------------------- redaction
red = rov.redact_data(PAYLOAD)
blob = json.dumps(red)
for secret, label in (("7PD2EAAB4VN000001", "VIN"), ("1000000001", "order id"),
                      ("example-owner@example.com", "email"), ("1 Example Way", "street"),
                      ("Example", "first name"), ("EXAMPLE0000000001", "tracking number"),
                      ("100 Example Delivery Rd", "delivery street")):
    check("redaction hides the {}".format(label), secret not in blob)
check("redaction keeps the amounts", red["order"]["total"] == ORDER["total"])
check("redaction keeps the statuses", red["order"]["vehicle"]["status"] == ORDER["vehicle"]["status"])
_long_a = rov.redact_data({"appointmentId": "3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b"})["appointmentId"]
_long_b = rov.redact_data({"appointmentId": "9c8b7a65-4321-0fed-cba9-87654321fedc"})["appointmentId"]
check("two long identifiers stay distinguishable after redaction", _long_a != _long_b, (_long_a, _long_b))
check("a long identifier keeps a head and a tail", _long_a.startswith("3f2a") and _long_a.endswith("6a7b"),
      _long_a)
check("redaction keeps a VIN tail for support", any(
    v.endswith("0001") and "\u2022" in v for v in [red["order"]["vehicle"]["vin"]]))

# ---------------------------------------------------------------- hardening
WEIRD = {
    "items": [None, {"title": "x", "fees": {"total": "532.00",
                                            "items": [{"amount": "532.00", "code": "TITLE_FEE"}]},
                     "taxes": {"items": [{"amount": "14.00", "code": "T", "rate": 0.07}]},
                     "configuration": {"options": ["PAINT"]}}],
    "payments": {"nope": 1},
    "vehicle": "not-a-dict",
    "fulfillmentInfo": {"fulfillments": [None, {"tracking": "UPS"}],
                        "pendingFulfillmentItems": ["x"]},
}
doc_w = rov.render_html(WEIRD, {"data": {"appointmentDetails": "soon", "deliveryAddress": "x"}},
                        {"data": {"delivery": "str"}}, {"x": 1}, personal=True)
check("render survives a malformed payload", "Rivian order" in doc_w)
check("render tolerates decimal-string fee amounts", "$532.00" in doc_w)
check("summary survives a malformed payload", "Order " in rov.summary(WEIRD, {"data": "x"}, None, None))
check("no bare Python None leaks into the text summary",
      "None" not in rov.summary({"id": "1", "vehicle": {"vin": None, "vehicleId": None},
                                 "fulfillmentInfo": {"fulfillments": [{"tracking": None}]}},
                                {"data": {"status": None, "carrier": None}}))

SHAPES = rov.redact_data({
    "phone": 3175550142, "vehicleId": ["01-000000123"], "licensePlate": "XJX1234",
    "trackingNumber": "1Z999AA10123456784", "fulfillmentId": "ful-8b7a6c5d",
    "tradeInReferenceId": "TRADE-8891-EXTERNAL",
    "tracking": {"url": "https://example.test/track?tracknum=1Z999AA10123456784"},
    "note_with_shape": "the VIN 7PD2EAAB4VN000001 and uuid 3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b",
})
blob2 = json.dumps(SHAPES)
for secret, label in (("3175550142", "int phone"), ("XJX1234", "licence plate"),
                      ("1Z999AA10123456784", "tracking number"), ("ful-8b7a6c5d", "fulfillment id"),
                      ("TRADE-8891-EXTERNAL", "trade-in id"), ("7PD2EAAB4VN000001", "VIN in free text"),
                      ("3f2a1b4c-5d6e-7f80-9a1b-2c3d4e5f6a7b", "uuid in free text"),
                      ("01-000000123", "list-valued vehicle id")):
    check("redaction hides a {}".format(label), secret not in blob2)

# ---------------------------------------------------------------- html / summary
doc = rov.render_html(ORDER, DELIV, T2D, PAYLOAD["vin_decode"], personal=False)
for needle in ("The journey so far", "Vehicle record", "VIN decode", "Delivery",
               "Build configuration", "Itemized taxes", "Order money", "Payments",
               "Shipments", "Endpoints tried", "What this page cannot show",
               "DELIVERY_FEE", "PRIOR_YEAR_VEHICLE_EXCISE_TAX", "DELIVERY_ACCEPTED",
               "Sat 06 Feb 2027, 10:30 AM", "v" + rov.VERSION):
    check("html contains {!r}".format(needle), needle in doc)
check("html reports the checklist as unavailable", "http 503" in doc)
check("html is self-contained (no external assets)",
      "<img" not in doc and "http://" not in doc and "https://" not in doc and "<script" not in doc)
check("html escapes identifiers rather than trusting them",
      rov.render_html({**ORDER, "id": '<script>alert(1)</script>'}, None, None, None,
                      personal=False).count("<script>") == 0)

text = rov.summary(ORDER, DELIV, T2D, PAYLOAD["vin_decode"], personal=False)
for needle in ("Vehicle      : 2027 Rivian R2", "Itemized taxes and fees", "TITLE_FEE",
               "appointment:", "Checklist    : unavailable", "of which: Rivian's own"):
    check("summary contains {!r}".format(needle), needle in text)
check("summary hides personal data when asked", "example-owner@example.com" not in text)
check("summary shows personal data by default",
      "example-owner@example.com" in rov.summary(ORDER, DELIV, T2D, None, personal=True))

# ---------------------------------------------------------------- CLI
def run_cli(args, expect_rc=0):
    proc = subprocess.run([sys.executable, str(TOOL)] + args, capture_output=True, text=True)
    check("CLI {} exits {}".format(" ".join(args) or "(no args)", expect_rc),
          proc.returncode == expect_rc, proc.stdout[-300:] + proc.stderr[-300:])
    return proc


def read(path):
    """Read a produced artifact as UTF-8, or "" if the run that should have produced it died."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):        # missing file, or an undecodable one
        return ""


def artifact(path):
    """Read a produced artifact AND record whether it exists.

    Every use of this text is a negative assertion ("the VIN is not in the dump"), and a negative
    assertion about a file that was never written is vacuously true — so the existence has to be a
    check in its own right, or a broken --redact passes silently on Windows.
    """
    text = read(path)
    check("{} was produced and readable".format(pathlib.Path(path).name), bool(text))
    return text


with tempfile.TemporaryDirectory() as tmp:
    tmpdir = pathlib.Path(tmp)

    # A piped or redirected Windows console encodes stdout with the ANSI code page, not UTF-8.
    # This exact run used to die on the arrow in the delivery line and write no page at all, so
    # a Windows user redirecting output got nothing (rivian_order_view 2.0.0). cp437 is in the
    # pair because, unlike cp1252, it cannot encode the em dash, the en dash or the bullet that
    # --redact masks ids with — so the cp437 leg runs WITH --redact, which is the only way all
    # three reach that stream (the unredacted summary has no bullet).
    for codec, extra in (("cp1252", ()), ("cp437", ("--redact",))):
        enc_html = tmpdir / "console-{}.html".format(codec)
        enc = subprocess.run([sys.executable, str(TOOL), "--from-json", str(FIXTURE)] + list(extra) +
                             ["--html", str(enc_html)],
                             capture_output=True, text=True, errors="replace",
                             env=dict(os.environ, PYTHONIOENCODING=codec))
        check("a {} console does not crash the run".format(codec), enc.returncode == 0,
              enc.stderr[-200:])
        check("a {} run still writes the page".format(codec),
              enc_html.exists() and enc_html.stat().st_size > 5000)

    out_html, out_json = tmpdir / "report.html", tmpdir / "report.json"
    run_cli(["--from-json", str(FIXTURE), "--html", str(out_html), "--json", str(out_json)])
    out_size = out_html.stat().st_size if out_html.exists() else "missing"
    check("--from-json wrote the HTML page", out_size != "missing" and out_size > 5000, out_size)
    out_dump = read(out_json)
    try:
        dumped_id = json.loads(out_dump)["order"]["id"] if out_dump else None
    except ValueError:                       # present but malformed: a FAIL, not a traceback
        dumped_id = None
    check("--from-json wrote the JSON dump", dumped_id == ORDER["id"], dumped_id)
    check("the rendered page renders the fixture's pickup line",
          "Sat 06 Feb 2027, 10:30 AM" in artifact(out_html))

    red_html, red_json = tmpdir / "redacted.html", tmpdir / "redacted.json"
    run_cli(["--from-json", str(FIXTURE), "--redact", "--html", str(red_html), "--json", str(red_json)])
    body = artifact(red_html)
    check("--redact page hides the VIN", "7PD2EAAB4VN000001" not in body)
    check("--redact page carries the banner", "Redacted for sharing" in body)
    check("--redact page keeps the money", "$58,802.90" in body)
    dump = artifact(red_json)
    for secret, label in (("1000000001", "order id"), ("7PD2EAAB4VN000001", "VIN"),
                          ("EXAMPLE0000000001", "tracking number"), ("1000000001", "snapshots order id")):
        check("--redact hides the {} in the JSON dump".format(label), secret not in dump)
    red_json_mode = oct(red_json.stat().st_mode)[-3:] if red_json.exists() else "missing"
    red_html_mode = oct(red_html.stat().st_mode)[-3:] if red_html.exists() else "missing"
    check_posix("the JSON dump is written 0600", red_json_mode == "600", red_json_mode)
    check_posix("the HTML page is written 0600", red_html_mode == "600", red_html_mode)

    nop = tmpdir / "nopersonal.html"
    run_cli(["--from-json", str(FIXTURE), "--no-personal", "--html", str(nop)])
    nop_body = artifact(nop)
    check("--no-personal drops the delivery address", "100 Example Delivery Rd" not in nop_body)
    check("--no-personal drops the order email", "example-owner@example.com" not in nop_body)

    silent = run_cli(["--from-json", str(FIXTURE), "--no-html", "--html", str(tmpdir / "no.html")])
    check("--no-html writes no page", not (tmpdir / "no.html").exists())
    check("--no-html still prints the summary", "Order 1000000001" in silent.stdout)

    ver = run_cli(["--version"], expect_rc=0)
    check("--version prints the version", rov.VERSION in ver.stdout, ver.stdout.strip())
    st = run_cli(["--self-test"], expect_rc=0)
    check("--self-test reports its own count", "PASS" in st.stdout and "/" in st.stdout)

bad = subprocess.run([sys.executable, str(TOOL), "--from-json", str(HERE / "README.md")],
                     capture_output=True, text=True)
check("a non-payload file is refused with a clear message",
      bad.returncode != 0 and "could not parse" in (bad.stderr + bad.stdout), bad.stderr[-200:])
bad2 = subprocess.run([sys.executable, str(TOOL), "--from-json", str(FIXTURE.with_name("order_payload.bad.json"))],
                      capture_output=True, text=True) if (FIXTURE.with_name("order_payload.bad.json")).exists() else None
check("a payload with no order is refused",
      bad2 is not None and bad2.returncode != 0 and "order" in (bad2.stderr + bad2.stdout))

# ---------------------------------------------------------------- report
print("=" * 78)
bad = 0
skipped = 0
for status, name, detail in results:
    print("{}  {}".format(status, name))
    if status == "FAIL":
        bad += 1
    elif status == "SKIP":
        skipped += 1
    if status != "PASS" and detail:
        print("      {}".format(str(detail)[:300]).replace("\n", "\n      "))
print("=" * 78)
print("{} passed, {} failed, {} skipped ({} checks)".format(
    len(results) - bad - skipped, bad, skipped, len(results)))
sys.exit(1 if bad else 0)
