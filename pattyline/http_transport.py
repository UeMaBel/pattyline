"""Trusted leaf HTTP process. Started with -I -S, credentials only through stdin.

This process never loads project code, invokes tools or spawns descendants. The
orchestrator can cancel a long provider request without waiting for socket IO.
"""
import json
import sys
import urllib.error
import urllib.request


def main():
    value = json.load(sys.stdin)
    request = urllib.request.Request(value["url"], data=json.dumps(value["body"]).encode(), headers=value["headers"])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=value["timeout"]) as response:
            print(json.dumps(json.load(response)))
    except urllib.error.HTTPError as error:
        print(json.dumps({"transport_error": f"HTTP {error.code}; check authentication/model access"}))
        return 2
    except (OSError, ValueError):
        print(json.dumps({"transport_error": "Provider connection interrupted/unavailable; completion and usage unknown"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
