import requests
import json
import sys
import uuid

BASE = "http://127.0.0.1:8080"


def call(method, path, payload=None):
    url = BASE + path
    try:
        if method == "GET":
            r = requests.get(url, timeout=15)
        else:
            r = requests.post(url, json=payload, timeout=15)

        print(f"\n{method} {path}")
        print("STATUS:", r.status_code)

        try:
            data = r.json()
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return r.status_code, data
        except Exception:
            print(r.text)
            return r.status_code, None

    except Exception as e:
        print("ERROR:", repr(e))
        return None, None


def check(name, condition):
    print(("PASS" if condition else "FAIL") + f": {name}")
    return condition


print("=" * 70)
print("VERA AI — P0 REGRESSION TEST")
print("=" * 70)

passed = 0
failed = 0


# ------------------------------------------------------------
# 1. HEALTH
# ------------------------------------------------------------
status, data = call("GET", "/v1/healthz")

if check(
    "Health endpoint",
    status == 200 and isinstance(data, dict) and data.get("status") == "ok"
):
    passed += 1
else:
    failed += 1


# ------------------------------------------------------------
# 2. METADATA
# ------------------------------------------------------------
status, data = call("GET", "/v1/metadata")

if check(
    "Metadata endpoint",
    status == 200 and isinstance(data, dict)
):
    passed += 1
else:
    failed += 1


# ------------------------------------------------------------
# 3. CONTEXT VERSIONING
# Use a unique context ID on every run so prior test runs do not
# make version 1 look stale.
# ------------------------------------------------------------
TEST_CONTEXT_ID = f"p0_test_merchant_{uuid.uuid4().hex[:8]}"

context_v1 = {
    "scope": "merchant",
    "context_id": TEST_CONTEXT_ID,
    "version": 1,
    "payload": {
        "name": "P0 Test Merchant",
        "category": "restaurant",
        "city": "Delhi"
    }
}

status1, data1 = call("POST", "/v1/context", context_v1)

context_stale = {
    "scope": "merchant",
    "context_id": TEST_CONTEXT_ID,
    "version": 1,
    "payload": {
        "name": "STALE",
        "category": "restaurant"
    }
}

status_stale, data_stale = call(
    "POST",
    "/v1/context",
    context_stale
)

context_v2 = {
    "scope": "merchant",
    "context_id": TEST_CONTEXT_ID,
    "version": 2,
    "payload": {
        "name": "P0 Test Merchant Updated",
        "category": "restaurant",
        "city": "Delhi"
    }
}

status2, data2 = call("POST", "/v1/context", context_v2)

if check(
    "Context versioning",
    status1 == 200
    and isinstance(data1, dict)
    and data1.get("accepted") is True
    and status_stale == 409
    and isinstance(data_stale, dict)
    and data_stale.get("accepted") is False
    and data_stale.get("reason") == "stale_version"
    and data_stale.get("current_version") == 1
    and status2 == 200
    and isinstance(data2, dict)
    and data2.get("accepted") is True
):
    passed += 1
else:
    failed += 1


# ------------------------------------------------------------
# 4. REPLY INTENT
# The expected "wait" behavior for "Maybe later" is retained from
# the original test specification. If it fails, inspect the reply
# handler in bot.py rather than weakening this assertion.
# ------------------------------------------------------------
reply_tests = [
    ("Yes, let's do it", "send"),
    ("No, not interested", "end"),
    ("Maybe later", "wait"),
]

for text, expected in reply_tests:
    status, data = call(
        "POST",
        "/v1/reply",
        {
            "merchant_id": TEST_CONTEXT_ID,
            "message": text
        }
    )

    action = data.get("action") if isinstance(data, dict) else None

    if check(
        f"Reply intent: {text}",
        status == 200 and action == expected
    ):
        passed += 1
    else:
        failed += 1


# ------------------------------------------------------------
# 5. MALFORMED / UNKNOWN REPLY
# ------------------------------------------------------------
for text in ["", "hello", "random off topic message"]:
    status, data = call(
        "POST",
        "/v1/reply",
        {
            "merchant_id": TEST_CONTEXT_ID,
            "message": text
        }
    )

    if check(
        f"Reply does not crash: {text!r}",
        status in (200, 400, 422)
    ):
        passed += 1
    else:
        failed += 1


# ------------------------------------------------------------
# SUMMARY
# ------------------------------------------------------------
print("\n" + "=" * 70)
print("P0 BASIC TEST SUMMARY")
print("=" * 70)
print("PASSED:", passed)
print("FAILED:", failed)

if failed:
    sys.exit(1)

print("\nALL BASIC P0 TESTS PASSED.")
