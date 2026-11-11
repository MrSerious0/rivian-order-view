#!/usr/bin/env python3
"""rivian_order_view.py — see the real status of your own Rivian order. v2.0.2

What it shows
-------------
Everything Rivian's app API will tell you about an order — including the parts the app UI
buries:

  * the vehicle record: internal vehicle id, VIN, model year/make/model
  * build dates: expected, planned, and the ACTUAL general-assembly date
  * factory status (FACTORY_OK, FACTORY_GATED, STAGED_FOR_PDI, PDI_COMPLETED, …)
  * the build configuration as ordered (paint, interior, wheels, packages, …)
  * line items with prices, plus the ITEMISED tax and fee lines, with their codes
  * order money: subtotal, tax, fees, paid, remaining, outstanding, total
  * every payment record, with its status (a scheduled ACH shows as PROCESSING)
  * fulfillments with carrier, tracking number, shipped/delivered dates and windows
  * the delivery resolver: status, carrier, appointment window AND the delivery address
  * an independent VIN decode (NHTSA vPIC) confirming make/model/year/plant/trim
  * a self-contained HTML page with all of the above

What it deliberately does NOT pretend to know
---------------------------------------------
Rivian's own surfaces are uneven, and this tool reports what each one ANSWERS rather than
smoothing it over:

  * the delivery resolver needs a LIVE access token. With an expired one it answers
    `INTERNAL_SERVER_ERROR: User is unauthenticated` and the appointment disappears — so the
    tool refreshes first and distinguishes `err` (refused) from `none` (nothing scheduled).
  * the 8-step delivery checklist service (`transactionStatus`) has answered HTTP 503 to
    every request since 2026-09-03, GET and POST alike. It is reported as unavailable.
  * before the car is provisioned on your account, `currentUser.vehicles` is EMPTY and the
    vehicle-state surface answers with an all-null skeleton. An order tool cannot show live
    telemetry, location or state of charge for a car your account does not contain yet.
  * no field anywhere names where the car physically is. PDI/prep statuses are evidence,
    not a location.

Safety
------
* READ-ONLY: it logs in, then reads order / delivery / checklist data. It cannot send a
  vehicle command, lock, unlock, start charging, or change your account.
* Your password is never stored or printed. Only session tokens are cached, at
  `~/.rivian_order_view/tokens.json`, mode 0600 on POSIX, so repeat runs skip the login.
* Nothing is uploaded anywhere except to Rivian's own API (plus one optional public NHTSA
  decode of your VIN).
* `--redact` masks every identifier (order id, VIN, vehicle id, appointment id, tracking
  numbers, email, names and addresses) so a run can be shared or attached to a bug report.

Requirements: Python 3.8+. Standard library only — no pip installs.

Session handling
----------------
Rivian access tokens last about TWO HOURS. The order endpoint keeps answering with an
expired token, so everything looks fine — but the delivery resolver refuses it and the
pickup appointment silently vanishes. The tool checks the token's expiry before every run and
renews it in place with the stored refresh token (valid for months, no OTP needed).

Rate limits
-----------
A run with a cached session is about five requests (four to Rivian, one to NHTSA), seven on a
first run, eight if a one-time code is needed, and one more whenever the session is refreshed.
10-15 minutes between runs is plenty; the status fields jump rather than tick, so polling faster
buys nothing.

Usage
-----
    python3 rivian_order_view.py                      # log in (OTP if your account uses it)
    python3 rivian_order_view.py --tokens FILE        # reuse tokens cached elsewhere
    python3 rivian_order_view.py --relogin            # force a fresh login
    python3 rivian_order_view.py --json out.json      # save the raw payload
    python3 rivian_order_view.py --from-json out.json # re-render offline, no network
    python3 rivian_order_view.py --redact             # mask identifiers for sharing
    python3 rivian_order_view.py --no-personal        # omit email and addresses
    python3 rivian_order_view.py --no-vin-decode      # skip the NHTSA lookup
    python3 rivian_order_view.py --no-html            # text summary only
    python3 rivian_order_view.py --self-test          # offline checks, no credentials

Then open the HTML path it prints.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import getpass
import html
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "2.0.2"

GATEWAY = "https://rivian.com/api/gql/gateway/graphql"
ORDERS = "https://rivian.com/api/gql/orders/graphql/"
T2D = "https://rivian.com/api/gql/t2d/graphql/"
VPIC = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"
UA = "RivianApp/240702.0.0 CFNetwork/1474 Darwin/23.0.0"
TOKEN_DIR = pathlib.Path.home() / ".rivian_order_view"
TOKEN_FILE = TOKEN_DIR / "tokens.json"

SNAPSHOT_QUERY = ("query { user { orderSnapshots(filterTypes: [PRE_ORDER, VEHICLE, RETAIL]) { "
                  "id state configurationStatus fulfillmentSummaryStatus vehicleId vin } } }")

# `fees` and `taxes` are per ORDER ITEM, not per order. The two line-item types have
# different shapes, verified against the live schema on 2026-11-06:
#   OrderItemFeeItem -> description, amount, code           (NO rate, NO type)
#   OrderItemTaxItem -> description, amount, code, rate, type
# Asking for `rate` on a fee is a hard validation error (HTTP 400), so the two selections
# below are deliberately different.
ORDER_QUERY = (
    "query order($id: String!) { order(id: $id) { id orderDate state type currency total "
    "subtotal discountTotal taxTotal feesTotal paidTotal remainingTotal outstandingBalance "
    "costAfterCredits orderCancelDate vin vehicleId configurationStatus fulfillmentSummaryStatus "
    "orderEmail locale storeId "
    "billingAddress { firstName lastName line1 line2 city state country postalCode } "
    "shippingAddress { firstName lastName line1 line2 city state country postalCode } "
    "vehicle { vehicleId vin modelYear model make plannedBuildDate expectedBuildDate "
    "actualGeneralAssemblyDate expectedGeneralAssemblyStartDate status } "
    "tradeIns { tradeInReferenceId amount } "
    "payments { id amount status method type intent date } "
    "items { id title type sku quantity unitPrice state "
    "configuration { options { optionId optionName groupId } } "
    "fees { total items { description amount code } } "
    "taxes { total items { description amount code rate type } } } "
    "fulfillmentInfo { fulfillments { fulfillmentId fulfillmentStatus fulfillmentMethod "
    "fulfillmentVendor tracking { status carrier number url shipDate deliveredDate serviceType } "
    "estimatedDeliveryWindow { startDate endDate } } pendingFulfillmentItems { quantity } } } }"
)

DELIVERY_QUERY = ("query delivery($orderId: ID!) { delivery(orderId: $orderId) { status carrier "
                  "vehicleVIN appointmentDetails { appointmentId startDateTime endDateTime "
                  "timeZone } deliveryAddress { addressLine1 addressLine2 city state zipcode } } }")

T2D_STEPS = ("titleAndReg", "tradeIn", "finance", "delivery", "insurance",
             "documentUpload", "contracts", "payment")
T2D_QUERY = ("query GetTransactionStatus($orderId: ID!) { transactionStatus(orderId: $orderId) { "
             + " ".join(f"{step} {{ sourceStatus {{ status }} }}" for step in T2D_STEPS) + " } }")

# Fee codes Rivian keeps, as opposed to state fees it merely collects and passes through.
# Only DELIVERY_FEE and DOCUMENTATION_FEE are Rivian's own money.
RIVIAN_FEE_CODES = {"DELIVERY_FEE", "DOCUMENTATION_FEE"}

# Endpoint outcomes that are worth explaining rather than just displaying.
ENDPOINT_NOTES = {
    "transactionStatus": "HTTP 503 for every request since 2026-09-03 (server side — not an "
                         "auth problem, and nothing a client can fix)",
    "delivery": "needs a live access token; an expired one answers 'User is unauthenticated'",
}


# --------------------------------------------------------------------------- transport

def post(url, payload, headers, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as exc:  # network / DNS / timeout
        return -1, {"transport_error": "{}: {}".format(type(exc).__name__, str(exc)[:120])}
    return status, body if isinstance(body, dict) else {"unexpected_body_type": type(body).__name__}


def base_headers(client_name="com.rivian.ios.consumer-apollo-ios"):
    return {"Content-Type": "application/json", "Accept": "*/*", "User-Agent": UA,
            "Apollographql-Client-Name": client_name}


def api_headers(tokens):
    headers = base_headers("com.rivian.ios.consumer")
    headers["Authorization"] = "Bearer " + str(tokens.get("access_token", ""))
    for header, key in (("Csrf-Token", "csrf_token"), ("A-Sess", "app_session_token"),
                        ("U-Sess", "user_session_token")):
        if tokens.get(key):
            headers[header] = tokens[key]
    return headers


def gql_errors(data):
    errs = (data or {}).get("errors")
    if not errs:
        return None
    first = errs[0] if isinstance(errs, list) and errs else errs
    return first.get("message") if isinstance(first, dict) else str(first)


def is_csrf_expiry(data):
    """An orders query carrying only a Bearer token answers HTTP 200 with data: null and
    {'message': 'Request a new CSRF token', 'extensions': {'code': 'CSRF_TOKEN_EXPIRED'}} —
    which reads exactly like 'this order does not exist'. Detect it, re-mint, retry once."""
    blob = json.dumps(data or {})[:600]
    return "CSRF_TOKEN_EXPIRED" in blob or "Request a new CSRF token" in blob


# --------------------------------------------------------------------------- auth

def create_csrf():
    status, data = post(GATEWAY, {"operationName": "CreateCSRFToken",
                                  "query": "mutation CreateCSRFToken {createCsrfToken "
                                           "{__typename csrfToken appSessionToken}}",
                                  "variables": None}, base_headers())
    node = ((data or {}).get("data") or {}).get("createCsrfToken") or {}
    if not node.get("csrfToken"):
        raise SystemExit("could not get a CSRF token from Rivian (http {}): {}".format(
            status, gql_errors(data) or "no token in response"))
    return node["csrfToken"], node["appSessionToken"]


def login(email, password, token_path=None):
    """CSRF -> Login; a MobileMFALoginResponse means an OTP is required -> LoginWithOTP."""
    csrf, app_sess = create_csrf()
    headers = dict(base_headers(), **{"Csrf-Token": csrf, "A-Sess": app_sess,
                                      "Dc-Cid": "m-ios-" + str(uuid.uuid4())})
    query = ("mutation Login($email: String!, $password: String!) { login(email: $email, "
             "password: $password) { __typename ... on MobileLoginResponse { accessToken "
             "refreshToken userSessionToken } ... on MobileMFALoginResponse { otpToken } } }")
    status, data = post(GATEWAY, {"operationName": "Login", "query": query,
                                  "variables": {"email": email, "password": password}}, headers)
    node = ((data or {}).get("data") or {}).get("login") or {}
    if status != 200 or gql_errors(data) or not node:
        raise SystemExit("login failed (http {}): {} — check the email/password. This tool never "
                         "retries: repeated attempts can lock the account.".format(
                             status, gql_errors(data) or "no login payload"))
    if node.get("otpToken"):
        print("\nRivian sent a one-time code to your email or phone.")
        code = input("Enter the code: ").strip()
        if not code:
            raise SystemExit("no code entered")
        query2 = ("mutation LoginWithOTP($email: String!, $otpCode: String!, $otpToken: String!) { "
                  "loginWithOTP(email: $email, otpCode: $otpCode, otpToken: $otpToken) { __typename "
                  "... on MobileLoginResponse { accessToken refreshToken userSessionToken } } }")
        status, data = post(GATEWAY, {"operationName": "LoginWithOTP", "query": query2,
                                      "variables": {"email": email, "otpCode": code,
                                                    "otpToken": node["otpToken"]}}, headers)
        node = ((data or {}).get("data") or {}).get("loginWithOTP") or {}
        if status != 200 or gql_errors(data) or not node.get("accessToken"):
            raise SystemExit("code rejected (http {}): {} — run again for a new code".format(
                status, gql_errors(data) or "no token returned"))
    tokens = {"access_token": node.get("accessToken", ""),
              "refresh_token": node.get("refreshToken", ""),
              "user_session_token": node.get("userSessionToken", ""),
              "csrf_token": csrf, "app_session_token": app_sess}
    save_tokens(tokens, token_path)
    return tokens


def write_private(path, text):
    """Write a file 0600 from creation: the HTML page and the JSON dump hold personal data."""
    target = pathlib.Path(str(path)).expanduser()
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError as exc:
        raise SystemExit("could not write {}: {}".format(target, exc.strerror or exc))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(target, 0o600)
    except Exception:
        print("note: could not set 0600 on {}".format(target))
    return target


def save_tokens(tokens, path=None):
    """Write the token cache 0600 from creation — no world-readable window, ever."""
    target = pathlib.Path(str(path)).expanduser() if path else TOKEN_FILE
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except Exception:
        pass
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(tokens, indent=1))
    finally:
        try:
            os.chmod(target, 0o600)
        except Exception:
            print("note: could not set 0600 on {} — check the filesystem".format(target))
    print("session tokens cached at {} (password never stored)".format(target))


def load_tokens(path):
    p = pathlib.Path(str(path)).expanduser()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def jwt_exp(token):
    """Epoch expiry of a JWT, or 0 when it is not decodable."""
    try:
        part = str(token).split(".")[1]
        part += "=" * (-len(part) % 4)
        return int(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0)
    except Exception:
        return 0


def refresh_access_token(tokens, margin_minutes=10, token_path=None):
    """Renew the access token in place.

    Rivian access tokens last only ~2 HOURS. The order endpoint keeps answering with an
    expired token, so everything looks fine — but the gateway's `delivery` resolver refuses
    it ("User is unauthenticated") and the appointment silently disappears. The apollo-ios
    client name is REQUIRED here; the plain consumer name returns INVALID_CLIENT.
    """
    exp = jwt_exp(tokens.get("access_token"))
    if exp and exp - time.time() > margin_minutes * 60:
        return None                                  # still healthy
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    status, data = post(GATEWAY, {"operationName": "RefreshToken",
                                  "variables": {"t": refresh_token},
                                  "query": "mutation RefreshToken($t: String!) "
                                           "{ refreshAccessToken(refreshToken: $t) { accessToken } }"},
                        base_headers("com.rivian.ios.consumer-apollo-ios"))
    node = ((data or {}).get("data") or {}).get("refreshAccessToken") or {}
    token = node.get("accessToken")
    if status != 200 or not token:
        errs = (data or {}).get("errors") or [{}]
        reason = ((errs[0] or {}).get("extensions") or {}).get("reason") if isinstance(errs[0], dict) else None
        print("note: could not refresh the session ({}); delivery/checklist data may be "
              "unavailable until you run with --relogin".format(reason or "http " + str(status)))
        return None
    tokens["access_token"] = token
    tokens["refreshed_at"] = datetime.datetime.now().astimezone().isoformat()
    save_tokens(tokens, token_path)
    return token


def token_state(tokens):
    exp = jwt_exp(tokens.get("access_token"))
    if not exp:
        return "unknown"
    left = exp - time.time()
    if left <= 0:
        return "expired (refreshing failed — delivery data will be missing; use --relogin)"
    return "valid for {} more minutes".format(int(left // 60))


def renew_csrf(tokens):
    """Re-mint the CSRF pair and keep it on the token dict."""
    csrf, app_sess = create_csrf()
    tokens["csrf_token"] = csrf
    tokens["app_session_token"] = app_sess
    return tokens


# --------------------------------------------------------------------------- data

def my_orders(tokens):
    # orderSnapshots lives only on the ORDERS host; the gateway answers
    # GRAPHQL_VALIDATION_FAILED, which reads like "no orders".
    status, data = post(ORDERS, {"query": SNAPSHOT_QUERY}, api_headers(tokens))
    if is_csrf_expiry(data):
        renew_csrf(tokens)
        status, data = post(ORDERS, {"query": SNAPSHOT_QUERY}, api_headers(tokens))
    snaps = (((data or {}).get("data") or {}).get("user") or {}).get("orderSnapshots") or []
    if isinstance(snaps, dict):
        snaps = list(snaps.values())
    if not isinstance(snaps, list):
        raise SystemExit("could not list your orders: the response was not a list (Rivian may have "
                         "changed the shape) — re-run with --json to capture it")
    snaps = [x for x in snaps if isinstance(x, dict)]
    if gql_errors(data) or not snaps:
        msg = str(gql_errors(data) or "no orders on this account")
        hint = ""
        if any(w in msg.lower() for w in ("csrf", "session", "unauthenticated", "unauthorized")):
            hint = " — the cached session expired; re-run with --relogin"
        raise SystemExit("could not list your orders (http {}): {}{}".format(status, msg, hint))
    return snaps


def order_detail(tokens, order_id):
    payload = {"operationName": "order", "variables": {"id": str(order_id)}, "query": ORDER_QUERY}
    status, data = post(ORDERS, payload, api_headers(tokens))
    if is_csrf_expiry(data):
        renew_csrf(tokens)
        status, data = post(ORDERS, payload, api_headers(tokens))
    order = ((data or {}).get("data") or {}).get("order")
    if gql_errors(data) or not isinstance(order, dict):
        raise SystemExit("could not read order {} (http {}): {}".format(
            order_id, status, gql_errors(data) or "empty payload"))
    return order


def delivery_probe(tokens, order_id):
    status, data = post(GATEWAY, {"operationName": "delivery",
                                  "variables": {"orderId": str(order_id)},
                                  "query": DELIVERY_QUERY}, api_headers(tokens))
    return {"http": status, "error": gql_errors(data),
            "data": (((data or {}).get("data") or {}).get("delivery"))}


def t2d_probe(tokens, order_id):
    status, data = post(T2D, {"operationName": "GetTransactionStatus",
                              "variables": {"orderId": str(order_id)},
                              "query": T2D_QUERY}, api_headers(tokens))
    return {"http": status, "error": gql_errors(data),
            "data": (((data or {}).get("data") or {}).get("transactionStatus"))}


def vin_decode(vin):
    """Independent confirmation from the public NHTSA vPIC database."""
    if not vin or len(str(vin)) != 17:
        return None
    try:
        with urllib.request.urlopen(VPIC.format(vin=urllib.parse.quote(str(vin), safe="")), timeout=20) as resp:
            row = ((json.loads(resp.read().decode("utf-8")).get("Results")) or [{}])[0]
    except Exception:
        return None
    keys = ("Make", "Model", "ModelYear", "Trim", "Series", "DriveType", "PlantCity", "PlantState",
            "BodyClass", "ElectrificationLevel", "GVWR", "Manufacturer")
    return {k: row[k] for k in keys if row.get(k) not in (None, "")}


# --------------------------------------------------------------------------- redaction

MASK_CHAR = "\u2022"
_PERSONAL_KEYS = {"firstName", "lastName", "line1", "line2", "phone", "phoneNumber",
                  "customerName", "recipientEmail"}
_MASKED_KEYS = {"id", "orderId", "order_id", "vehicleId", "vehicle_id", "appointmentId",
                "fulfillmentId", "tradeInReferenceId", "storeId", "orderEmail", "email",
                "vin", "vehicleVIN", "number", "trackingNumber", "licensePlate"}
_ADDRESS_KEYS = {"billingAddress", "shippingAddress", "deliveryAddress"}
# Value-shape fallbacks: an identifier is still an identifier if the key name is new.
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def mask_value(value, keep=4):
    text = str(value)
    if not text:
        return text
    if keep <= 0 or len(text) <= keep:
        return MASK_CHAR * len(text)
    return MASK_CHAR * (len(text) - keep) + text[-keep:]


def mask_email(value):
    s = str(value)
    if "@" not in s:
        return mask_value(s, keep=0)
    local, _, domain = s.partition("@")
    return "{}{}@{}".format(local[:1], MASK_CHAR * max(0, len(local) - 1), MASK_CHAR * len(domain))


def _redact_scalar(value):
    """Mask a scalar whatever its type, and any URL that probably embeds an identifier.

    Above 24 characters the head AND the tail are kept. Keeping only the tail of a 36-character
    UUID turned every one of them into the same ten bullets, which defeats the reason the tail is
    kept at all: telling two records apart in a support thread.
    """
    text = str(value)
    if len(text) <= 24:
        return mask_value(text)
    return text[:4] + MASK_CHAR * 6 + text[-4:]


def redact_data(obj, _key=""):
    """Deep copy with identifiers masked.

    Two passes, because a fixed key list is not a guarantee: key names catch the fields we
    know, and value shapes (17-character VINs, UUIDs, tracking URLs) catch the ones we do not.
    Anything under an address object, and any key ending in `url` beneath tracking data, is
    masked outright.
    """
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            lowered = str(key).lower()
            if key in _PERSONAL_KEYS and val not in (None, ""):
                out[key] = "[redacted]"
            elif key == "orderEmail" and isinstance(val, str):
                out[key] = mask_email(val)
            elif key in _MASKED_KEYS and val not in (None, ""):
                out[key] = _redact_scalar(val)
            elif key in _ADDRESS_KEYS and isinstance(val, dict):
                out[key] = {k: ("[redacted]" if v else v) for k, v in val.items()}
            elif key == "zipcode" and val not in (None, ""):
                out[key] = _redact_scalar(val)
            elif lowered.endswith("url") and isinstance(val, str) and val:
                out[key] = mask_value(val, keep=0)
            else:
                out[key] = redact_data(val, key)
        return out
    if isinstance(obj, list):
        return [redact_data(x, _key) for x in obj]
    if isinstance(obj, str):
        masked = _VIN_RE.sub(lambda m: mask_value(m.group(0)), obj)
        return _UUID_RE.sub(lambda m: mask_value(m.group(0)), masked)
    return obj


# --------------------------------------------------------------------------- presentation

def as_dict(value):
    """The API is not guaranteed to keep a shape: treat anything unexpected as empty."""
    return value if isinstance(value, dict) else {}


def as_list(value):
    """A list of dicts, ignoring nulls and non-dict entries."""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def as_num(value, default=0.0):
    """Tolerant number: accepts ints, floats and decimal strings ('532.00', '$1,495')."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return default


def dash(value):
    """Text-mode equivalent of the HTML em-dash: never print a bare Python None."""
    return "\u2014" if value in (None, "") else str(value)


def money(value):
    number = as_num(value, default=None)
    return "${:,.2f}".format(number) if number is not None else "\u2014"


def items_of(order):
    """(title, type, sku, quantity, unitPrice, [options]) per ordered item."""
    out = []
    for item in as_list(as_dict(order).get("items")):
        options = as_list(as_dict(item.get("configuration")).get("options"))
        out.append((item.get("title"), item.get("type"), item.get("sku"), item.get("quantity"),
                    item.get("unitPrice"), options))
    return out


def parse_stamp(value):
    """Parse an ISO timestamp on 3.8+ — fromisoformat only learned 'Z' in 3.11."""
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(text)


def pretty_dt(value):
    """'2030-01-05T14:00:00' -> 'Sat 05 Jan 2030, 2:00 PM' (weekday computed, never assumed)."""
    try:
        stamp = parse_stamp(value)
        return stamp.strftime("%a %d %b %Y, ") + stamp.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value or "\u2014")


def pretty_time(value):
    try:
        return parse_stamp(value).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value or "\u2014")


def appt_local(appt):
    """One line for a delivery appointment: 'Sat 31 Oct 2026, 2:00 PM - 2:15 PM EDT'.

    The weekday is computed, never written by hand — a hand-written weekday is the single
    most common way a status report ends up lying. The zone abbreviation comes from the
    payload's own timeZone field when zoneinfo can resolve it.

    A stamp that already carries an offset is CONVERTED into the appointment's zone. Using
    `replace(tzinfo=...)` on it would relabel the UTC wall clock instead of converting, which
    silently shifts the day — and therefore the computed weekday — near midnight.
    """
    if not isinstance(appt, dict) or not appt.get("startDateTime"):
        return ""
    zone = str(appt.get("timeZone") or "").strip()
    zoneinfo = None
    if zone:
        try:
            from zoneinfo import ZoneInfo          # Python 3.9+
            zoneinfo = ZoneInfo(zone)
        except Exception:
            zoneinfo = None

    def local(value):
        stamp = parse_stamp(value)
        if stamp.tzinfo is not None and zoneinfo is not None:
            stamp = stamp.astimezone(zoneinfo)
        return stamp

    try:
        stamp = local(appt["startDateTime"])
        text = stamp.strftime("%a %d %b %Y, ") + stamp.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(appt["startDateTime"])
    if appt.get("endDateTime"):
        try:
            end = local(appt["endDateTime"])
            text += " \u2013 " + end.strftime("%I:%M %p").lstrip("0")
        except Exception:
            text += " \u2013 " + str(appt["endDateTime"])
    if zone:
        abbr = ""
        if zoneinfo is not None:
            try:
                abbr = (stamp if stamp.tzinfo else stamp.replace(tzinfo=zoneinfo)).tzname() or ""
            except Exception:
                abbr = ""
        text += " " + (abbr or zone)
    return text


FEE_KIND_LABEL = {"fees": "fee", "taxes": "tax"}


def fee_rows(order):
    """(item, kind, code, description, amount, rate) for every itemized fee and tax line."""
    rows = []
    for item in as_list(as_dict(order).get("items")):
        for kind in ("fees", "taxes"):
            block = as_dict(item.get(kind))
            for line in as_list(block.get("items")):
                rows.append((item.get("title") or "item", FEE_KIND_LABEL[kind], line.get("code"),
                             line.get("description"), line.get("amount"), line.get("rate")))
    return rows


def fee_split(order):
    """(rivian_total, passthrough_total) across every item's fee lines."""
    rivian = passthrough = 0.0
    for item in as_list(as_dict(order).get("items")):
        for line in as_list(as_dict(item.get("fees")).get("items")):
            amount = as_num(line.get("amount"))
            if line.get("code") in RIVIAN_FEE_CODES:
                rivian += amount
            else:
                passthrough += amount
    return rivian, passthrough


def journey(order, deliv=None):
    v = as_dict(order.get("vehicle"))
    rows = []
    if order.get("orderDate"):
        placed = str(order.get("orderDate"))
        rows.append((placed, placed[:10], "Order placed",
                     "order {}".format(dash(order.get("id"))), "done"))
    for p in as_list(order.get("payments")):
        when = str(p.get("date") or "")
        rows.append((when, when[:10], "Payment {}".format(money(p.get("amount"))),
                     "{} \u00b7 {} \u00b7 {}".format(dash(p.get("method")), dash(p.get("intent")),
                                                     dash(p.get("status"))), "done"))
    for key, label, kind in (("expectedBuildDate", "Expected build date (plan)", "plan"),
                             ("expectedGeneralAssemblyStartDate", "Expected GA start (plan)", "plan"),
                             ("plannedBuildDate", "Planned build date (plan)", "plan"),
                             ("actualGeneralAssemblyDate", "BUILT \u2014 general assembly", "key")):
        if v.get(key):
            rows.append((str(v[key]) + "~" + kind, str(v[key]), label,
                         "{} from the vehicle record".format(key), kind))
    d = as_dict((deliv or {}).get("data"))
    appt = as_dict(d.get("appointmentDetails"))
    if appt.get("startDateTime"):
        detail = appt_local(appt) or pretty_dt(appt.get("startDateTime"))
        if d.get("carrier"):
            detail += " \u00b7 " + str(d["carrier"])
        rows.append((str(appt["startDateTime"]), str(appt["startDateTime"])[:10],
                     "PICKUP SCHEDULED", detail, "sched"))
    rows.sort(key=lambda r: (r[0], r[1]))
    out = list(rows)
    if not as_list(as_dict(order.get("fulfillmentInfo")).get("fulfillments")):
        out.append(("zz1", "next", "Shipping / tracking", "no fulfillment record yet", "pending"))
    if not appt.get("startDateTime"):
        out.append(("zz2", "next", "Delivery appointment",
                    "resolver refused \u2014 see 'endpoints tried'" if (deliv or {}).get("error")
                    else "not scheduled yet", "pending"))
    return out


def summary(order, deliv=None, t2d=None, decode=None, personal=True):
    v = as_dict(order.get("vehicle"))
    fi = as_dict(order.get("fulfillmentInfo"))
    pend = as_list(fi.get("pendingFulfillmentItems"))
    fulfills = as_list(fi.get("fulfillments"))
    pays = as_list(order.get("payments"))
    recorded = sum(as_num(p.get("amount")) for p in pays)
    L = ["Order {} \u2014 {} / {} / {}   (placed {})".format(
        dash(order.get("id")), dash(order.get("state")), dash(order.get("configurationStatus")),
        dash(order.get("fulfillmentSummaryStatus")), dash(str(order.get("orderDate") or "")[:10]))]
    if v:
        L.append("Vehicle      : {} {} {}".format(
            dash(v.get("modelYear")), dash(v.get("make")), dash(v.get("model"))))
        L.append("VIN          : {}   (Rivian vehicle id {})".format(
            dash(v.get("vin")), dash(v.get("vehicleId"))))
        if v.get("actualGeneralAssemblyDate"):
            L.append("BUILT        : {}  (actual general assembly)".format(v["actualGeneralAssemblyDate"]))
        for label, key in (("Expected build", "expectedBuildDate"),
                           ("Planned build", "plannedBuildDate"),
                           ("Expected GA start", "expectedGeneralAssemblyStartDate")):
            if v.get(key):
                L.append("{:<13}: {}".format(label, v[key]))
        L.append("Factory status: {}".format(dash(v.get("status"))))
    else:
        L.append("Vehicle      : no vehicle record on this order yet (not assigned/built)")
    if decode:
        L.append("VIN decodes as: " + ", ".join(
            "{}={}".format(k, x) for k, x in decode.items()
            if k in ("Make", "Model", "ModelYear", "Trim", "PlantCity", "PlantState")))
    items = items_of(order)
    if items:
        L.append("Configuration:")
        for title, itype, sku, qty, price, options in items:
            L.append("  - {} ({})  qty {}  {}".format(title or "item", itype or "?", qty, money(price))
                     + ("  [sku {}]".format(sku) if sku else ""))
            for opt in options:
                L.append("      {}: {}".format(opt.get("groupId"), opt.get("optionName")))
    rows = fee_rows(order)
    if rows:
        L.append("Itemized taxes and fees:")
        for item_title, kind, code, desc, amount, rate in rows:
            rate_txt = "  rate {}".format(rate) if rate not in (None, "") else ""
            L.append("  {:6} {:38} {:>10}{}  ({})".format(
                kind, str(code or ""), money(amount), rate_txt, desc or ""))
        rivian, passthrough = fee_split(order)
        L.append("  of which: Rivian's own {} \u00b7 state/DMV pass-through {}".format(
            money(rivian), money(passthrough)))
    L.append("Money        : total {}, paid {} (the API's paidTotal), balance {}  (subtotal {}, "
             "fees {}, tax {})".format(money(order.get("total")), money(order.get("paidTotal")),
                                       money(order.get("outstandingBalance")),
                                       money(order.get("subtotal")), money(order.get("feesTotal")),
                                       money(order.get("taxTotal"))))
    if pays:
        L.append("               payments recorded: {} across {} record(s) — this includes any "
                 "still PROCESSING, so it can exceed paidTotal".format(money(recorded), len(pays)))
    for p in pays:
        L.append("  payment    : {} {} {} {} ({})".format(
            str(p.get("date"))[:10], money(p.get("amount")), p.get("status"), p.get("method"),
            p.get("intent")))
    L.append("Pending items: {} record(s) (that list carries quantities only; the items above "
             "are what is on the order)".format(len(pend)))
    if fulfills:
        for f in fulfills:
            tr = as_dict(f.get("tracking"))
            win = as_dict(f.get("estimatedDeliveryWindow"))
            L.append("  shipment   : {} \u00b7 carrier {} \u00b7 tracking {} \u00b7 shipped {} \u00b7 "
                     "delivered {} \u00b7 window {}->{}".format(
                         dash(f.get("fulfillmentStatus")), dash(tr.get("carrier")),
                         dash(tr.get("number")), dash(tr.get("shipDate")),
                         dash(tr.get("deliveredDate")), dash(win.get("startDate")),
                         dash(win.get("endDate"))))
    else:
        L.append("Shipments    : none \u2014 no tracking record yet")
    if deliv:
        if as_dict(deliv).get("data"):
            d = as_dict(deliv.get("data"))
            appt = as_dict(d.get("appointmentDetails"))
            L.append("Delivery     : {} \u00b7 carrier {}".format(
                dash(d.get("status")), dash(d.get("carrier"))))
            if appt:
                L.append("  appointment: {}".format(appt_local(appt) or appt.get("startDateTime")))
                L.append("  raw        : {} -> {}  ({})".format(
                    dash(appt.get("startDateTime")), dash(appt.get("endDateTime")),
                    dash(appt.get("timeZone"))))
            addr = as_dict(d.get("deliveryAddress"))
            if personal and addr.get("addressLine1"):
                L.append("  address    : " + ", ".join(str(x) for x in (
                    addr.get("addressLine1"), addr.get("addressLine2"), addr.get("city"),
                    addr.get("state"), addr.get("zipcode")) if x))
        else:
            L.append("Delivery     : err \u2014 the resolver refused (http {}{}). This is NOT the "
                     "same as 'none': no appointment is known.".format(
                         deliv.get("http"),
                         " \u00b7 " + str(deliv.get("error")) if deliv.get("error") else ""))
    if t2d:
        if as_dict(t2d).get("data"):
            steps = []
            tdata = as_dict(as_dict(t2d).get("data"))
            for step in T2D_STEPS:
                st = as_dict(as_dict(tdata.get(step)).get("sourceStatus")).get("status")
                steps.append("{}={}".format(step, dash(st) if st else "\u2014"))
            L.append("Checklist    : " + ", ".join(steps))
        else:
            L.append("Checklist    : unavailable \u2014 http {}{} (that service has answered 503 to "
                     "every request since 2026-09-03)".format(
                         t2d.get("http"),
                         " \u00b7 " + str(t2d.get("error")) if t2d.get("error") else ""))
    if personal:
        addr = as_dict(order.get("shippingAddress"))
        if addr.get("line1"):
            L.append("Address      : " + ", ".join(str(x) for x in (
                addr.get("line1"), addr.get("line2"), addr.get("city"), addr.get("state"),
                addr.get("postalCode")) if x))
        if order.get("orderEmail"):
            L.append("Order email  : {}".format(order.get("orderEmail")))
    return "\n".join(L)


def render_html(order, deliv=None, t2d=None, decode=None, personal=True, redacted=False):
    def esc(x):
        return "&mdash;" if x in (None, "") else html.escape(str(x))

    def tbl(headers, rows, cls=""):
        head = "".join("<th>{}</th>".format(html.escape(h)) for h in headers)
        body = "".join("<tr>" + "".join("<td>{}</td>".format(c) for c in r) + "</tr>" for r in rows)
        return '<table class="{}"><thead><tr>{}</tr></thead><tbody>{}</tbody></table>'.format(
            cls, head, body)

    def tile(label, value, hot=False):
        return ('<div class="tile{}"><div class="l">{}</div><div class="v">{}</div></div>'
                .format(" hot" if hot else "", html.escape(label), esc(value)))

    v = as_dict(order.get("vehicle"))
    fi = as_dict(order.get("fulfillmentInfo"))
    pend = as_list(fi.get("pendingFulfillmentItems"))
    fulfills = as_list(fi.get("fulfillments"))
    pays = as_list(order.get("payments"))
    items = items_of(order)
    rows = fee_rows(order)

    d = as_dict((deliv or {}).get("data"))
    appt = as_dict(d.get("appointmentDetails"))
    pickup_text = appt_local(appt) if appt.get("startDateTime") else "\u2014"
    tiles = "".join([
        tile("state", order.get("state")),
        tile("config status", order.get("configurationStatus")),
        tile("fulfillment", order.get("fulfillmentSummaryStatus")),
        tile("vin", v.get("vin"), True),
        tile("build (actual | planned)", "{}\u2009|\u2009{}".format(
            v.get("actualGeneralAssemblyDate") or "none", v.get("plannedBuildDate") or "none"), True),
        tile("factory status", v.get("status")),
        tile("delivery", d.get("status") if d else ("err" if (deliv or {}).get("error") else "\u2014"), True),
        tile("carrier", d.get("carrier") if d else ("err" if (deliv or {}).get("error") else "\u2014")),
        tile("pickup", pickup_text, True),
        tile("balance", money(order.get("outstandingBalance"))),
        tile("order total", money(order.get("total"))),
        tile("pending items", len(pend)),
    ])

    road = "".join(
        '<div class="jrow {}"><div class="jd">{}</div><div><div class="jt">{}</div>'
        '<div class="jx">{}</div></div></div>'.format(
            kind, html.escape(when), html.escape(title), html.escape(detail))
        for _k, when, title, detail, kind in journey(order, deliv))

    vrows = [] if not v else [[("<code>{}</code>".format(html.escape(k))), esc(val)] for k, val in (
        ("vin", v.get("vin")), ("vehicleId", v.get("vehicleId")),
        ("model", "{} {} {}".format(v.get("modelYear") or "", v.get("make") or "",
                                    v.get("model") or "").strip()),
        ("expectedBuildDate", v.get("expectedBuildDate")),
        ("plannedBuildDate", v.get("plannedBuildDate")),
        ("expectedGeneralAssemblyStartDate", v.get("expectedGeneralAssemblyStartDate")),
        ("actualGeneralAssemblyDate", v.get("actualGeneralAssemblyDate")),
        ("status", v.get("status")))] or \
        [['<span class="muted">no vehicle record on this order yet</span>', "&mdash;"]]
    drows = [["<code>{}</code>".format(html.escape(k)), esc(val)]
             for k, val in as_dict(decode).items()]

    cfg_rows = [[esc(title), esc(itype), "<code>{}</code>".format(esc(sku)), esc(qty), money(price),
                 "<br>".join("<code>{}</code> {}".format(esc(o.get("groupId")), esc(o.get("optionName")))
                             for o in options) or '<span class="muted">&mdash;</span>']
                for title, itype, sku, qty, price, options in items] or \
        [['<span class="muted">no items on this order payload</span>', "", "", "", "", ""]]

    fee_table_rows = [[esc(item_title), esc(kind), "<code>{}</code>".format(esc(code)),
                       esc(desc), money(amount),
                       '{:g}'.format(rate) if isinstance(rate, (int, float)) else "&mdash;"]
                      for item_title, kind, code, desc, amount, rate in rows]
    rivian_fees, passthrough = fee_split(order)

    mrows = [["<code>{}</code>".format(html.escape(k)), money(order.get(k))] for k in
             ("subtotal", "discountTotal", "taxTotal", "feesTotal", "paidTotal", "remainingTotal",
              "outstandingBalance", "costAfterCredits", "total")]
    prows = [["<code>{}</code>".format(esc(str(p.get("date"))[:10])), money(p.get("amount")),
              esc(p.get("status")), esc(p.get("method")), esc(p.get("intent"))] for p in pays] or \
        [['<span class="muted">no payment records</span>', "", "", "", ""]]
    frows = [[esc(f.get("fulfillmentStatus")), esc(as_dict(f.get("tracking")).get("carrier")),
              esc(as_dict(f.get("tracking")).get("number")),
              esc(as_dict(f.get("tracking")).get("shipDate")),
              esc(as_dict(f.get("tracking")).get("deliveredDate")),
              "{}&rarr;{}".format(esc(as_dict(f.get("estimatedDeliveryWindow")).get("startDate")),
                                  esc(as_dict(f.get("estimatedDeliveryWindow")).get("endDate")))]
             for f in fulfills] or [['<span class="muted">no shipment records yet</span>', "", "", "", "", ""]]

    addr = as_dict(d.get("deliveryAddress"))
    delivery_rows = [
        ["<code>status</code>", esc(d.get("status") if d else
                                    ("err \u2014 resolver refused" if (deliv or {}).get("error") else "\u2014"))],
        ["<code>carrier</code>", esc(d.get("carrier"))],
        ["<code>appointment</code>", esc(pickup_text)],
        ["<code>start \u2192 end</code>", "{} &rarr; {}".format(esc(appt.get("startDateTime")),
                                                               esc(appt.get("endDateTime")))],
        ["<code>timeZone</code>", esc(appt.get("timeZone"))],
    ]
    if personal:
        delivery_rows.append(["<code>address</code>", esc(", ".join(str(x) for x in (
            addr.get("addressLine1"), addr.get("addressLine2"), addr.get("city"),
            addr.get("state"), addr.get("zipcode")) if x))])

    ep_rows = [
        ["<code>order(id)</code> \u2014 build, configuration, money, itemized fees and taxes",
         '<span class="pill ok">ok</span>'],
        ["<code>delivery(orderId)</code> \u2014 carrier, appointment, address",
         '<span class="pill {}">{}</span> <span class="muted">{}</span>'.format(
             "ok" if d else "bad",
             esc(d.get("status") if d else "http " + str((deliv or {}).get("http"))),
             esc((deliv or {}).get("error") or ENDPOINT_NOTES["delivery"]))],
        ["<code>transactionStatus</code> \u2014 8-step delivery checklist",
         '<span class="pill {}">{}</span> <span class="muted">{}</span>'.format(
             "ok" if (t2d or {}).get("data") else "bad",
             esc("ok" if (t2d or {}).get("data") else "http " + str((t2d or {}).get("http"))),
             esc((t2d or {}).get("error") or ENDPOINT_NOTES["transactionStatus"]))],
        ["NHTSA vPIC \u2014 independent VIN decode",
         '<span class="pill {}">{}</span>'.format("ok" if decode else "warn",
                                                  esc("ok" if decode else "unavailable"))],
    ]

    personal_html = ""
    if personal:
        addr_rows = []
        for label, key in (("order email", "orderEmail"), ("locale", "locale"),
                           ("store id", "storeId"), ("billing address", "billingAddress"),
                           ("shipping address", "shippingAddress")):
            val = order.get(key)
            if isinstance(val, dict):
                val = ", ".join(str(x) for x in (val.get("firstName"), val.get("lastName"),
                                                 val.get("line1"), val.get("line2"),
                                                 val.get("city"), val.get("state"),
                                                 val.get("country"), val.get("postalCode")) if x)
            addr_rows.append(["<code>{}</code>".format(label), esc(val)])
        personal_html = "<h2>Contact &amp; addresses</h2>{}".format(
            tbl(["field", "value"], addr_rows))

    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    banner = ('<div class="banner">Redacted for sharing \u2014 identifiers are masked. '
              'Amounts and statuses are unchanged.</div>') if redacted else ""
    fee_note = ('<div class="note">Rivian\'s own money here is <strong>{}</strong> (destination and '
                'documentation). The remaining <strong>{}</strong> is state/DMV money collected and '
                'passed through. A line whose code starts with <code>PRIOR_YEAR_</code> is an excise '
                'year that precedes the model year.</div>').format(
        money(rivian_fees), money(passthrough)) if rows else ""

    doc = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rivian order {oid}</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}
body{{margin:0;padding:26px 30px 40px;background:#0b0f14;color:#e8eef5;
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}}
h2{{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#8194aa;margin:24px 0 8px}}
.sub{{color:#94a2b4;font-size:12.5px;margin-bottom:18px}}
.banner{{margin:0 0 16px;padding:9px 13px;border-radius:9px;background:#3a2c10;color:#f4c873;
 border:1px solid #55401a;font-size:12.5px}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}
.tile{{background:#121a24;border:1px solid #1f2833;border-radius:11px;padding:10px 13px}}
.tile.hot{{border-color:#2b5c7c;background:#0f1e29}}
.tile .l{{font-size:10.5px;text-transform:uppercase;letter-spacing:.8px;color:#7d8ea3}}
.tile .v{{font-size:17px;font-weight:600;margin-top:3px;word-break:break-word}}
table{{width:100%;border-collapse:collapse;background:#101822;border:1px solid #1f2833;
 border-radius:10px;overflow:hidden;font-size:13px}}
th,td{{text-align:left;padding:7px 11px;border-bottom:1px solid #1b242f;vertical-align:top}}
th{{background:#151f2a;color:#8fa1b6;font-size:10.5px;text-transform:uppercase;letter-spacing:.7px}}
tr:last-child td{{border-bottom:none}}td:first-child{{white-space:nowrap}}
code{{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:12px;color:#b9d4e8;
 word-break:break-word}}
.muted{{color:#6b7a8d;font-style:italic}}
.note{{margin-top:9px;padding:10px 13px;border-radius:9px;background:#0f1e29;border:1px solid #1f2833;
 color:#9fb3c8;font-size:12.5px}}
.pill{{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11.5px;white-space:nowrap}}
.pill.ok{{background:#123524;color:#71e7ab}}.pill.warn{{background:#3a2c10;color:#f4c873}}
.pill.bad{{background:#3a1618;color:#ff9f9f}}
.journey{{background:#101822;border:1px solid #1f2833;border-radius:10px;padding:2px 16px}}
.jrow{{display:grid;grid-template-columns:104px 1fr;gap:16px;padding:10px 0;
 border-bottom:1px solid #1b242f}}
.jrow:last-child{{border-bottom:none}}
.jd{{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:12px;color:#8fa1b6;padding-top:2px}}
.jt{{font-weight:600;font-size:13.5px}}.jx{{color:#93a2b5;font-size:12.5px;margin-top:2px}}
.jrow.plan .jt{{color:#c3d2e0;font-weight:500}}.jrow.plan .jd{{color:#6b7a8d}}
.jrow.key{{border-left:3px solid #2b5c7c;padding-left:12px;background:#0f1e29}}
.jrow.sched{{border-left:3px solid #2f9e5f;padding-left:12px;background:#0f2419}}
.jrow.key .jt{{color:#cfe6f7}}.jrow.pending{{opacity:.6}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}}
.foot{{margin-top:22px;color:#6b7a8d;font-size:11.5px}}
@media (max-width:820px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}
 .cols{{grid-template-columns:1fr}}}}
</style></head><body>
{banner}
<h1>Rivian order {oid}</h1>
<div class="sub">from Rivian's app API (read-only) &middot; {otype} &middot; placed {odate} &middot;
 generated {now}</div>
<div class="grid">{tiles}</div>
<h2>The journey so far</h2><div class="journey">{road}</div>
<div class="note"><strong>Reading these values.</strong> Status strings are quoted exactly as the API
returns them, because that is what support and bug reports need. The observed ladder is
<code>PENDING_FULFILLMENT &rarr; READY_FOR_FULFILLMENT</code> for the order and
<code>FACTORY_OK &rarr; FACTORY_GATED &rarr; STAGED_FOR_PDI &rarr; PDI_COMPLETED &rarr;
VEHICLE_PREPPED</code> for the vehicle; a value can skip steps, and the published state lags the
physical car. <code>actualGeneralAssemblyDate</code> is the real build; the planned and expected
dates can legitimately fall later.</div>
<div class="cols">
<div><h2>Vehicle record</h2>{vehicle}</div>
<div><h2>VIN decode (independent)</h2>{vin_decode}</div>
</div>
<h2>Delivery</h2>{delivery}{appt_note}
<h2>Build configuration</h2>{config}
<h2>Itemized taxes &amp; fees</h2>{fees}{fee_note}
<div class="cols">
<div><h2>Order money</h2>{money_tbl}</div>
<div><h2>Payments ({n_pay})</h2>{pay_tbl}</div>
</div>
<h2>Shipments ({n_ship})</h2>{ship_tbl}
<h2>Pending fulfillment items ({n_pend})</h2>
<div class="muted">the order payload reports {n_pend} pending item record(s); that list carries
quantities only. The items in "build configuration" above are what is actually on the order.</div>
{personal}
<h2>Endpoints tried</h2>{endpoints}
<div class="note"><strong>What this page cannot show.</strong> Before Rivian provisions the car on
your account, <code>currentUser.vehicles</code> is empty and the vehicle-state surface answers with
an all-null skeleton, so no live telemetry, location or charge level exists to read. No field
anywhere names where the car physically is &mdash; the PDI/prep statuses are evidence, not a
location. And status timestamps are record-level: two steps 22 minutes apart measure a database
write, not the labour behind it.</div>
<div class="foot">"actual general assembly" is the real build date; planned/expected dates can
legitimately fall later. Order state and fulfillment status often lag the physical build.
Read-only snapshot from Rivian's app API &mdash; nothing in this file is a credential.
Generated by rivian_order_view v{ver}.</div>
</body></html>""".format(
        banner=banner, oid=html.escape(str(order.get("id"))),
        otype=html.escape(str(order.get("type") or "")),
        odate=html.escape(str(order.get("orderDate") or "")[:10]),
        now=html.escape(now), ver=VERSION, tiles=tiles, road=road,
        vehicle=tbl(["field", "value"], vrows),
        vin_decode=(tbl(["attribute", "value"], drows) if drows
                    else '<div class="muted">not available</div>'),
        delivery=tbl(["field", "value"], delivery_rows),
        appt_note=('<div class="note">The appointment above is rendered from Rivian\'s own ISO '
                   'timestamp with the weekday computed in code, never written by hand. The '
                   'timeZone field is quoted verbatim.</div>') if appt.get("startDateTime") else "",
        config=tbl(["item", "type", "sku", "qty", "unit price", "configuration"], cfg_rows),
        fees=(tbl(["item", "kind", "code", "description", "amount", "rate"], fee_table_rows)
              if fee_table_rows else '<div class="muted">no itemized fee or tax lines on this '
                                     'payload (they appear once the purchase flow prices the '
                                     'order)</div>'),
        fee_note=fee_note,
        money_tbl=tbl(["field", "value"], mrows), n_pay=len(pays),
        pay_tbl=tbl(["date", "amount", "status", "method", "intent"], prows),
        n_ship=len(fulfills), ship_tbl=tbl(["status", "carrier", "tracking", "shipped",
                                            "delivered", "window"], frows),
        n_pend=len(pend), personal=personal_html,
        endpoints=tbl(["endpoint", "result"], ep_rows))
    return doc


# --------------------------------------------------------------------------- offline + tests

def load_payload(path):
    """Load a payload written by --json (or a bare {'order': ...} object)."""
    p = pathlib.Path(str(path)).expanduser()
    if not p.exists():
        raise SystemExit("no such payload file: {}".format(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit("could not parse {}: {}".format(p, exc))
    if not isinstance(data, dict) or not isinstance(data.get("order"), dict):
        raise SystemExit("{} does not look like a payload from --json: it needs an 'order' "
                         "object".format(p))
    return data


def self_test():
    """Offline checks — no network, no credentials. Exits non-zero on any failure."""
    results = []

    def check(name, ok):
        results.append((bool(ok), name))

    check("money formats and refuses junk", money(1234.5) == "$1,234.50" and money(None) == "\u2014")
    check("pretty_dt keeps the weekday computed",
          pretty_dt("2030-01-05T14:00:00") == "Sat 05 Jan 2030, 2:00 PM")
    check("pretty_time drops the leading zero", pretty_time("2030-01-05T14:15:00") == "2:15 PM")
    appt = {"startDateTime": "2030-01-05T14:00:00", "endDateTime": "2030-01-05T14:15:00",
            "timeZone": "America/Indiana/Indianapolis"}
    check("appointment line carries a computed weekday and the window",
          appt_local(appt).startswith("Sat 05 Jan 2030, 2:00 PM \u2013 2:15 PM"))
    check("appointment line is empty without a start",
          appt_local({"endDateTime": "2030-01-05T14:15:00"}) == "")

    masked = redact_data({"order": {"id": "1000000001", "orderEmail": "someone@example.com",
                                    "shippingAddress": {"line1": "1 Main St", "city": "X"},
                                    "items": [{"vin": "7PD2EAAB4VN000001",
                                               "tracking": {"number": "1Z999AA10123456784"}}]}})
    blob = json.dumps(masked)
    check("redaction masks the order id", "1000000001" not in blob)
    check("redaction masks the VIN", "7PD2EAAB4VN000001" not in blob)
    check("redaction masks the email local part", "someone@" not in blob)
    check("redaction masks the street line", "1 Main St" not in blob)
    check("redaction masks the tracking number", "1Z999AA10123456784" not in blob)

    sample = {
        "id": "9999999999", "state": "ORDERED", "type": "VEHICLE", "orderDate": "2029-12-15T10:00:00",
        "configurationStatus": "SOFT_LOCKED", "fulfillmentSummaryStatus": "PENDING_FULFILLMENT",
        "subtotal": 100.0, "taxTotal": 7.0, "feesTotal": 12.0, "paidTotal": 5.0,
        "remainingTotal": 107.0, "outstandingBalance": 107.0, "total": 119.0,
        "vehicle": {"vehicleId": "01-000000001", "vin": "7PD2EAAB4VN000001", "modelYear": 2027,
                    "make": "Rivian", "model": "R2", "plannedBuildDate": "2030-01-03",
                    "actualGeneralAssemblyDate": "2030-01-02", "status": "FACTORY_OK"},
        "items": [{"title": "Rivian R2", "type": "VEHICLE", "sku": "R2", "quantity": 1,
                   "unitPrice": 100.0,
                   "configuration": {"options": [{"groupId": "PAINT", "optionName": "Forest Green"}]},
                   "fees": {"total": 12.0, "items": [
                       {"description": "Destination", "amount": 11.0, "code": "DELIVERY_FEE"},
                       {"description": "Title", "amount": 1.0, "code": "TITLE_FEE"}]},
                   "taxes": {"total": 7.0, "items": [
                       {"description": "Sales tax", "amount": 7.0, "code": "STATE_TAX",
                        "rate": 0.07, "type": "StateTax"}]}}],
        "payments": [{"amount": 5.0, "status": "SUCCEEDED", "method": "CARD",
                      "intent": "DEPOSIT", "date": "2029-12-15T10:05:00"}],
        "fulfillmentInfo": {"fulfillments": [], "pendingFulfillmentItems": [{"quantity": 1}]},
    }
    deliv = {"http": 200, "error": None, "data": {
        "status": "APPT_SCHEDULED", "carrier": "THIRD_PARTY",
        "appointmentDetails": appt,
        "deliveryAddress": {"addressLine1": "1 Rivian Way", "city": "Somewhere",
                            "state": "IN", "zipcode": "46000"}}}
    doc = render_html(sample, deliv, {"http": 503, "error": None, "data": None}, None, personal=False)
    for needle in ("The journey so far", "Vehicle record", "Delivery", "Build configuration",
                   "Itemized taxes", "Order money", "Payments", "Endpoints tried",
                   "What this page cannot show", "TITLE_FEE", "DELIVERY_FEE", "APPT_SCHEDULED",
                   "Sat 05 Jan 2030", "v" + VERSION):
        check("html contains {!r}".format(needle), needle in doc)
    check("html marks the checklist endpoint as failing", "http 503" in doc or "503" in doc)
    text = summary(sample, deliv, {"http": 503, "error": None, "data": None}, None, personal=False)
    for needle in ("Vehicle      : 2027 Rivian R2", "Itemized taxes and fees", "TITLE_FEE",
                   "appointment:", "Checklist    : unavailable"):
        check("summary contains {!r}".format(needle), needle in text)
    check("fee split separates Rivian money from pass-through",
          fee_split(sample) == (11.0, 1.0))

    bad = 0
    for ok, name in results:
        print("{}  {}".format("PASS" if ok else "FAIL", name))
        bad += 0 if ok else 1
    print("\n{}/{} PASS".format(len(results) - bad, len(results)))
    return 1 if bad else 0


# --------------------------------------------------------------------------- console

def harden_console():
    """Degrade an unencodable character instead of dying on it.

    Windows Python encodes stdout with the ANSI code page whenever stdout is not a real console
    (a pipe, a redirect, a wrapper process). One non-ASCII character in the summary therefore
    raised UnicodeEncodeError and killed the run BEFORE the HTML page and the JSON dump were
    written — so `py rivian_order_view.py > out.txt` on Windows produced nothing at all. The
    page is written as UTF-8 and is unaffected; only the terminal is degraded.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:                              # pythonw (None) or a replaced stream
            pass


# --------------------------------------------------------------------------- main

def main():
    harden_console()
    ap = argparse.ArgumentParser(description="Read-only view of your own Rivian order.")
    ap.add_argument("--version", action="version", version="rivian_order_view " + VERSION)
    ap.add_argument("--tokens", help="tokens JSON to reuse (default: ~/.rivian_order_view/tokens.json)")
    ap.add_argument("--email", help="account email (or you will be prompted)")
    ap.add_argument("--relogin", action="store_true", help="ignore cached tokens and log in again")
    ap.add_argument("--order", help="order id to inspect (default: the one with a vehicle, else the first)")
    ap.add_argument("--html", default="rivian_order_view.html", help="where to write the HTML page")
    ap.add_argument("--json", dest="json_out", help="also write the raw API payload here")
    ap.add_argument("--from-json", dest="from_json",
                    help="render offline from a payload written by --json (no network, no login)")
    ap.add_argument("--redact", action="store_true",
                    help="mask identifiers (order id, VIN, vehicle id, tracking, email, names, "
                         "addresses) in the text, the HTML and the JSON dump")
    ap.add_argument("--no-personal", action="store_true", help="omit email and addresses")
    ap.add_argument("--no-vin-decode", action="store_true", help="skip the NHTSA VIN decode")
    ap.add_argument("--no-html", action="store_true", help="text summary only")
    ap.add_argument("--self-test", action="store_true", help="offline checks, no credentials")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    personal = not args.no_personal

    if args.from_json:
        data = load_payload(args.from_json)
        order = data["order"]
        deliv = data.get("delivery")
        t2d = data.get("checklist")
        decode = data.get("vin_decode")
        print("offline render from {} (no network, no credentials)".format(args.from_json))
    else:
        token_path = args.tokens or TOKEN_FILE
        tokens = None if args.relogin else load_tokens(token_path)
        if tokens and not tokens.get("access_token"):
            tokens = None
        if not tokens:
            email = args.email or input("Rivian account email: ").strip()
            password = getpass.getpass("Password (not stored): ")
            if not email or not password:
                raise SystemExit("email and password are required")
            tokens = login(email, password, token_path)
        else:
            print("using cached session from {}  (--relogin to redo)".format(
                args.tokens or TOKEN_FILE))

        # Rivian access tokens last ~2 hours; the delivery resolver stops answering without a
        # live one, so renew before querying (no OTP needed while the refresh token is valid).
        if refresh_access_token(tokens, token_path=token_path):
            print("session refreshed (Rivian access tokens expire after ~2 hours)")
        print("token: {}".format(token_state(tokens)))

        snaps = my_orders(tokens)
        order_id = args.order
        if not order_id:
            with_vehicle = [x for x in snaps if x.get("vehicleId") or x.get("vin")]
            order_id = (with_vehicle or snaps)[0].get("id")
            if len(snaps) > 1:
                ids = ([mask_value(x.get("id")) for x in snaps] if args.redact
                       else [x.get("id") for x in snaps])
                shown = mask_value(order_id) if args.redact else order_id
                print("orders on this account: {} — using {} (pass --order to change)".format(
                    ids, shown))

        snaps_for_dump = snaps
        order = order_detail(tokens, order_id)
        deliv = delivery_probe(tokens, order_id)
        t2d = t2d_probe(tokens, order_id)
        decode = None if args.no_vin_decode else vin_decode((order.get("vehicle") or {}).get("vin"))

    if args.redact:
        order = redact_data(order)
        deliv = redact_data(deliv) if isinstance(deliv, dict) else deliv
        t2d = redact_data(t2d) if isinstance(t2d, dict) else t2d
        decode = redact_data(decode) if isinstance(decode, dict) else decode

    print()
    print(summary(order, deliv, t2d, decode, personal=personal))

    if args.json_out:
        snaps_out = [] if args.from_json else locals().get("snaps_for_dump", [])
        if args.redact and snaps_out:
            snaps_out = redact_data(snaps_out)
        payload = {"tool": "rivian_order_view", "version": VERSION,
                   "generated_at": datetime.datetime.now().astimezone().isoformat(),
                   "snapshots": snaps_out,
                   "order": order, "delivery": deliv, "checklist": t2d, "vin_decode": decode}
        written = write_private(args.json_out, json.dumps(payload, indent=1))
        print("\nraw payload written to {} (0600 on POSIX{})".format(
            written, ", redacted" if args.redact else " — it contains personal data"))
    if not args.no_html:
        written = write_private(args.html, render_html(
            order, deliv, t2d, decode, personal=personal, redacted=bool(args.redact)))
        print("HTML page written to {} (0600 on POSIX{})".format(
            written.resolve(), ", redacted" if args.redact else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
