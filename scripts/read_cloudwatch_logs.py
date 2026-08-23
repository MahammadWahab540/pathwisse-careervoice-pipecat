import boto3
import json

client = boto3.client("logs", region_name="ap-south-1")
resp = client.get_log_events(
    logGroupName="/aws/apprunner/careervoice-pipecat/bf5e39b16d4c46af824aed0f2f05373a/application",
    logStreamName="instance/0b8aaf0124b345059aaf9f959134104e",
    limit=50,
)

print("=== LATEST APPRUNNER APPLICATION LOGS ===")
for event in resp.get("events", []):
    msg = event.get("message", "").strip()
    # Check for secret leakage
    has_token = "V8ogCSfhVJu" in msg or "CAREERVOICE_SERVICE_TOKEN" in msg or "Bearer " in msg
    print(f"[{event.get('timestamp')}] {msg} (Secret Leaked: {has_token})")
