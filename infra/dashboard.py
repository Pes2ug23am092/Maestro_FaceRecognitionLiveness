"""CloudWatch dashboard 'hidb-kiosk': Lambda invocations/errors/duration (p50/p95/p99), throttles, API Gateway requests + latency."""
import json
import boto3

R, FN = "us-east-1", "hidb-kiosk-backend"
cfg = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[1] / "config" / "deployed.json"))
api = cfg["api_id"]
def w(title, metrics, x, y, stat="Sum", period=60, width=12):
    return {"type": "metric", "x": x, "y": y, "width": width, "height": 6,
            "properties": {"title": title, "region": R, "view": "timeSeries", "stat": stat, "period": period, "metrics": metrics}}
body = {"widgets": [
    w("Decisions per minute (Lambda invocations)", [["AWS/Lambda", "Invocations", "FunctionName", FN]], 0, 0),
    w("Errors and throttles", [["AWS/Lambda", "Errors", "FunctionName", FN], [".", "Throttles", ".", "."]], 12, 0),
    {"type": "metric", "x": 0, "y": 6, "width": 12, "height": 6, "properties": {
        "title": "Lambda duration ms (p50 / p95 / p99), target < 2000", "region": R, "view": "timeSeries", "period": 60,
        "metrics": [["AWS/Lambda", "Duration", "FunctionName", FN, {"stat": "p50"}], ["...", {"stat": "p95"}], ["...", {"stat": "p99"}]],
        "annotations": {"horizontal": [{"label": "2 s budget", "value": 2000}]}}},
    w("API Gateway requests and 4xx/5xx", [["AWS/ApiGateway", "Count", "ApiId", api], [".", "4xx", ".", "."], [".", "5xx", ".", "."]], 12, 6),
    {"type": "metric", "x": 0, "y": 12, "width": 12, "height": 6, "properties": {
        "title": "API Gateway latency ms (p95)", "region": R, "view": "timeSeries", "period": 60,
        "metrics": [["AWS/ApiGateway", "Latency", "ApiId", api, {"stat": "p95"}], [".", "IntegrationLatency", ".", ".", {"stat": "p95"}]]}},
    w("Concurrent executions", [["AWS/Lambda", "ConcurrentExecutions", "FunctionName", FN]], 12, 12, stat="Maximum")]}
boto3.client("cloudwatch", region_name=R).put_dashboard(DashboardName="hidb-kiosk", DashboardBody=json.dumps(body))
print("dashboard hidb-kiosk created")
