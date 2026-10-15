import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler

API_BASE = "https://api.derivws.com"
DERIV_APP_ID = os.environ.get("DERIV_APP_ID", "1089")


def deriv_request(method, url, token):
    headers = {
        "Authorization": "Bearer " + token,
        "Deriv-App-ID": DERIV_APP_ID,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    req = Request(url, method=method, headers=headers)

    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)

    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"error": raw or str(e)}
        return e.code, data

    except URLError as e:
        return 503, {"error": "Could not reach Deriv API: " + str(e.reason)}

    except Exception as e:
        return 500, {"error": str(e)}


def error_message(data):
    if isinstance(data, dict):
        if data.get("error"):
            return str(data["error"])
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return first.get("message", first.get("code", "Deriv API error"))
            return str(first)
        if data.get("message"):
            return str(data["message"])
    return "Unknown Deriv API error"


class handler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 50000:
                self.send_json(400, {"ok": False, "error": "Invalid request"})
                return

            raw = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw)
            token = str(body.get("token", "")).strip()

            if not token:
                self.send_json(400, {"ok": False, "error": "PAT is required"})
                return

            # STEP 1: GET OPTIONS ACCOUNTS
            status, accounts_result = deriv_request("GET", API_BASE + "/trading/v1/options/accounts", token)
            if status != 200:
                self.send_json(status, {"ok": False, "error": error_message(accounts_result), "stage": "accounts"})
                return

            accounts = accounts_result.get("data", [])
            if isinstance(accounts, dict):
                accounts = [accounts]
            if not isinstance(accounts, list) or not accounts:
                self.send_json(404, {"ok": False, "error": "No Options trading account found", "stage": "accounts"})
                return

            # PREFER DEMO ACCOUNT
            demo_accounts = [a for a in accounts if str(a.get("account_type", "")).lower() == "demo"]
            account = demo_accounts[0] if demo_accounts else accounts[0]
            account_id = account.get("account_id")

            if not account_id:
                self.send_json(500, {"ok": False, "error": "Deriv account ID was not returned", "stage": "accounts"})
                return

            # STEP 2: REQUEST ONE-TIME PASSWORD / WS URL
            otp_url = API_BASE + "/trading/v1/options/accounts/" + str(account_id) + "/otp"
            otp_status, otp_result = deriv_request("POST", otp_url, token)

            if otp_status not in (200, 201):
                self.send_json(otp_status, {"ok": False, "error": error_message(otp_result), "stage": "otp"})
                return

            otp_data = otp_result.get("data", {})
            ws_url = otp_data.get("url") if isinstance(otp_data, dict) else None

            if not ws_url:
                self.send_json(500, {"ok": False, "error": "Deriv did not return an authenticated WebSocket URL", "stage": "otp"})
                return

            # SUCCESS
            self.send_json(200, {
                "ok": True,
                "account_id": account_id,
                "account_type": account.get("account_type", ""),
                "balance": account.get("balance", 0),
                "currency": account.get("currency", "USD"),
                "ws_url": ws_url
            })

        except Exception as e:
            self.send_json(500, {"ok": False, "error": str(e)})
