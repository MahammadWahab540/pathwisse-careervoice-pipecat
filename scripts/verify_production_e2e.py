import os
import json
import urllib.request
import urllib.error

PIPECAT_URL = "https://7pmmmiwq7m.ap-south-1.awsapprunner.com"
SERVICE_TOKEN = "V8ogCSfhVJu-gwU8hexPx4pE0JfUg9QVX4nlOdpDCsU"


def test_endpoint(name, url, method="GET", headers=None, data=None, expected_status=200):
    headers = headers or {}
    req_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            print(f"[{name}] -> Status {status} (Expected {expected_status}) - PASSED")
            try:
                return status, json.loads(body)
            except Exception:
                return status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        if e.code == expected_status:
            print(f"[{name}] -> Status {e.code} (Expected {expected_status}) - PASSED (Rejected properly)")
        else:
            print(f"[{name}] -> Status {e.code} (Expected {expected_status}) - FAILED")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body
    except Exception as e:
        print(f"[{name}] -> Exception: {e} - FAILED")
        return 0, str(e)


def main():
    print("==================================================================")
    print("      PRODUCTION AWS PIPECAT & CONTRACT E2E VERIFICATION          ")
    print("==================================================================")

    # 1. Health check
    s, data = test_endpoint("1. Pipecat /health probe", f"{PIPECAT_URL}/health", "GET", expected_status=200)
    assert s == 200, "Health check failed"
    assert data.get("status") == "healthy"

    # 2. Readiness check
    s, data = test_endpoint("2. Pipecat /ready probe", f"{PIPECAT_URL}/ready", "GET", expected_status=200)
    assert s == 200, "Readiness check failed"
    assert data.get("status") == "ready"
    assert data.get("providers", {}).get("serviceAuth") is True

    # 3. Fail-closed without Bearer token
    s, _ = test_endpoint(
        "3. Missing Service Token Rejection",
        f"{PIPECAT_URL}/api/voice/session",
        "POST",
        headers={"Content-Type": "application/json"},
        data={"auditId": "audit-test-e2e-01", "targetRole": "Frontend Engineer"},
        expected_status=401,
    )
    assert s == 401, "Expected 401 on missing service token"

    # 4. Fail-closed with Invalid Bearer token
    s, _ = test_endpoint(
        "4. Invalid Service Token Rejection",
        f"{PIPECAT_URL}/api/voice/session",
        "POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer invalid-tampered-token"},
        data={"auditId": "audit-test-e2e-01", "targetRole": "Frontend Engineer"},
        expected_status=401,
    )
    assert s == 401, "Expected 401 on invalid service token"

    # 5. Provisioning with Valid Bearer token
    s, session_data = test_endpoint(
        "5. Valid Voice Session Provisioning",
        f"{PIPECAT_URL}/api/voice/session",
        "POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {SERVICE_TOKEN}"},
        data={
            "auditId": "audit-e2e-valid-session",
            "targetRole": "Frontend Engineer",
            "studentName": "Alex",
            "studentId": "00000000-0000-0000-0000-000000000001",
        },
        expected_status=200,
    )
    assert s == 200, "Expected 200 on valid service token"
    assert session_data.get("success") is True
    assert session_data.get("provider") == "daily"
    assert "roomUrl" in session_data
    assert "token" in session_data
    assert "connection" in session_data

    print("\n==================================================================")
    print(" ALL PRODUCTION PIPECAT & CONTRACT TESTS PASSED WITH 100% SUCCESS  ")
    print("==================================================================")


if __name__ == "__main__":
    main()
