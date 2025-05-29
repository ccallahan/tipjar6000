"""Welcome to Reflex! This file outlines the steps to create a basic app."""

import reflex as rx
from square.client import Square
from square.environment import SquareEnvironment as square_env
import threading
import time
import os
import http.server
import json
import socketserver


# Configure your Square client (set your access token and location ID)
SQUARE_ACCESS_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN")
SQUARE_LOCATION_ID = os.environ.get("SQUARE_LOCATION_ID")

square_client = Square(
    token=SQUARE_ACCESS_TOKEN,
    environment=square_env.PRODUCTION,  # Change to "production" for live
)

shared_device_id = ""  # <-- Add this shared variable
payment_timer = None  # <-- Add this global variable for the payment timer


# --- Webhook/Callback Server ---
class PaymentCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)
        # You may want to add authentication/validation here
        if data.get("type") == "payment.updated":
            payment = data.get("data", {}).get("object", {}).get("payment", {})
            status = payment.get("status")
            idempotency_key = payment.get("reference_id")
            if status == "COMPLETED":
                State.on_payment_success(idempotency_key)
        self.send_response(200)
        self.end_headers()


# Start the callback server in a background thread
PORT = 8081


def start_callback_server():
    with socketserver.TCPServer(("", PORT), PaymentCallbackHandler) as httpd:
        print(f"Callback server running on port {PORT}")
        httpd.serve_forever()


threading.Thread(target=start_callback_server, daemon=True).start()


# --- State ---
class State(rx.State):
    amount: int = 0
    value_entry: str = ""
    auto_retry: bool = False
    transaction_success: bool = False
    current_idempotency_key: str = ""
    # Pairing state
    entered_password: str = ""
    pairing_code: str = ""
    device_id: str = ""
    error: str = ""
    is_authenticated: bool = False
    is_pairing: bool = False
    # Removed payment_timer from state

    def set_value_entry(self, value: str):
        self.value_entry = value

    def set_auto_retry(self, value: bool):
        self.auto_retry = value

    def set_entered_password(self, value: str):
        self.entered_password = value
        self.error = ""

    def submit_password(self):
        env_password = os.environ.get("PAIRING_PASSWORD")
        entered = (
            self.entered_password.strip()
            if self.entered_password else ""
        )
        env_pw = env_password.strip() if env_password else ""
        print(f"[DEBUG] Entered: {repr(entered)}, Env: {repr(env_pw)}")
        if not env_pw:
            self.error = "PAIRING_PASSWORD environment variable is not set."
            self.is_authenticated = False
            self.entered_password = ""
            return
        if entered == env_pw:
            self.is_authenticated = True
            self.error = ""
            self.entered_password = ""
        else:
            self.error = "Incorrect password."
            self.is_authenticated = False
            self.entered_password = ""

    def pair_terminal(self):
        if not self.is_authenticated:
            self.error = "You must enter the correct password."
            return
        self.is_pairing = True
        self.error = ""
        try:
            idempotency_key = f"pair-{int(time.time())}"
            device_code_body = {
                "name": "Reflex POS Terminal",
                "product_type": "TERMINAL_API",
                "location_id": SQUARE_LOCATION_ID,
            }
            codes_api = square_client.devices.codes
            result = codes_api.create(
                idempotency_key=idempotency_key,
                device_code=device_code_body
            )
            if result.device_code and ("code" in result.device_code.__dict__):
                self.pairing_code = result.device_code.code
                threading.Thread(
                    target=self._poll_for_device_id,
                    args=(result.device_code.code,),
                    daemon=True
                ).start()
            else:
                self.error = (
                    f"Pairing failed: {getattr(result, 'errors', 'Unknown error')}"
                )
        except Exception as e:
            self.error = f"Pairing failed: {e}"
        self.is_pairing = False

    def _poll_for_device_id(self, pairing_code):
        devices_api = square_client.devices
        global shared_device_id
        for _ in range(30):  # Poll for up to 30 seconds
            try:
                devices = devices_api.codes.list(
                    location_id=SQUARE_LOCATION_ID
                )
                for device_code in getattr(devices, "items", []):
                    if (
                        getattr(device_code, "code", "") == pairing_code and
                        getattr(device_code, "status", "") == "PAIRED"
                    ):
                        self.device_id = getattr(device_code, "device_id", "")
                        shared_device_id = self.device_id
                        return
            except Exception:
                pass
            time.sleep(1)

    def submit_value(self):
        try:
            amt = int(float(self.value_entry) * 100)
            self.amount = amt
            self.trigger_transaction()
        except Exception:
            pass

    def trigger_transaction(self):
        global shared_device_id, payment_timer
        if not shared_device_id:
            print("Device ID is not set. Please pair the terminal first.")
            return
        idempotency_key = f"trans-{int(time.time())}"
        self.current_idempotency_key = idempotency_key
        body = {
            "idempotency_key": idempotency_key,
            "checkout": {
                "amount_money": {
                    "amount": self.amount,
                    "currency": "USD",
                },
                "device_options": {
                    "device_id": shared_device_id,
                },
                "note": "POS Payment",
                "reference_id": idempotency_key,
            }
        }
        result = square_client.terminal.checkouts.create(**body)
        if hasattr(result, "checkout") and result.checkout:
            print("Checkout created:", result.checkout)
            self.transaction_success = False
            # Start a timer for 2 minutes to check for payment completion
            if payment_timer:
                payment_timer.cancel()
            payment_timer = threading.Timer(120, self.retry_transaction)
            payment_timer.start()
        else:
            print(
                "Terminal checkout failed:",
                getattr(result, "errors", "Unknown error")
            )

    def retry_transaction(self):
        global payment_timer
        if not self.transaction_success and self.auto_retry:
            print(
                "No payment confirmation after 2 minutes. "
                "Cancelling and retrying..."
            )
            # Cancel the previous transaction if possible
            self.trigger_transaction()

    @classmethod
    def on_payment_success(cls, idempotency_key):
        global payment_timer
        # Called by the webhook handler
        if cls.current_idempotency_key == idempotency_key:
            cls.transaction_success = True
            print(f"Payment {idempotency_key} completed!")
            if payment_timer:
                payment_timer.cancel()


# --- UI ---
def index() -> rx.Component:
    return rx.container(
        rx.color_mode.button(position="top-right"),
        rx.vstack(
            rx.heading("Enter Amount", size="9"),
            rx.input(
                placeholder="Enter amount $",
                value=State.value_entry,
                on_change=State.set_value_entry,
                width="200px",
                height="80px",
                font_size="2xl",
                padding="0 10px",
            ),
            rx.switch(
                label="Auto Retry if not paid in 2 min",
                checked=State.auto_retry,
                on_change=State.set_auto_retry,
                size="3",
            ),
            rx.button(
                "Charge",
                on_click=State.submit_value,
                size="3",
                height="80px",
                width="180px",
                font_size="2xl",
            ),
            rx.cond(
                State.transaction_success,
                rx.text("Payment successful!", color="green", font_size="xl"),
            ),
            spacing="5",
            justify="center",
            min_height="85vh",
        ),
    )


class TerminalPairState(rx.State):
    entered_password: str = ""
    pairing_code: str = ""
    device_id: str = ""
    error: str = ""
    is_authenticated: bool = False
    is_pairing: bool = False

    def set_entered_password(self, value: str):
        self.entered_password = value
        self.error = ""

    def submit_password(self):
        env_password = os.environ.get("PAIRING_PASSWORD")
        entered = (
            self.entered_password.strip()
            if self.entered_password else ""
        )
        env_pw = env_password.strip() if env_password else ""
        print(f"[DEBUG] Entered: {repr(entered)}, Env: {repr(env_pw)}")
        if not env_pw:
            self.error = "PAIRING_PASSWORD environment variable is not set."
            self.is_authenticated = False
            return
        if entered == env_pw:
            self.is_authenticated = True
            self.error = ""
        else:
            self.error = "Incorrect password."
            self.is_authenticated = False

    def pair_terminal(self):
        if not self.is_authenticated:
            self.error = "You must enter the correct password."
            return
        self.is_pairing = True
        self.error = ""
        try:
            idempotency_key = f"pair-{int(time.time())}"
            device_code_body = {
                "name": "Reflex POS Terminal",
                "product_type": "TERMINAL_API",
                "location_id": SQUARE_LOCATION_ID,
            }
            codes_api = square_client.devices.codes
            result = codes_api.create(
                idempotency_key=idempotency_key,
                device_code=device_code_body
            )
            if result.device_code and ("code" in result.device_code.__dict__):
                self.pairing_code = result.device_code.code
                threading.Thread(
                    target=self._poll_for_device_id,
                    args=(result.device_code.code,),
                    daemon=True
                ).start()
            else:
                self.error = (
                    f"Pairing failed: {getattr(result, 'errors', 'Unknown error')}"
                )
        except Exception as e:
            self.error = f"Pairing failed: {e}"
        self.is_pairing = False

    def _poll_for_device_id(self, pairing_code):
        devices_api = square_client.devices
        global shared_device_id
        for _ in range(30):  # Poll for up to 30 seconds
            try:
                devices = devices_api.codes.list(location_id=SQUARE_LOCATION_ID)
                for device_code in getattr(devices, "items", []):
                    if (
                        getattr(device_code, "code", "") == pairing_code and
                        getattr(device_code, "status", "") == "PAIRED"
                    ):
                        self.device_id = getattr(device_code, "device_id", "")
                        shared_device_id = self.device_id
                        return
            except Exception:
                pass
            time.sleep(1)


# --- UI ---
def terminal_pairing_page() -> rx.Component:
    return rx.container(
        rx.heading("Pair Square Terminal", size="7", margin_y="4"),
        rx.cond(
            TerminalPairState.is_authenticated,
            rx.vstack(
                rx.button(
                    "Pair Terminal",
                    on_click=TerminalPairState.pair_terminal,
                    size="3",
                    width="220px",
                    height="60px",
                    font_size="xl",
                    margin_y="2",
                ),
                rx.cond(
                    TerminalPairState.pairing_code != "",
                    rx.text(
                        f"Pairing Code: {TerminalPairState.pairing_code}",
                        font_size="2xl",
                        color="green",
                    ),
                ),
                rx.cond(
                    TerminalPairState.device_id != "",
                    rx.text(
                        f"Device ID: {TerminalPairState.device_id}",
                        font_size="xl",
                        color="blue",
                    ),
                ),
                rx.cond(
                    TerminalPairState.error != "",
                    rx.text(TerminalPairState.error, color="red"),
                ),
            ),
            rx.vstack(
                rx.input(
                    placeholder="Enter password",
                    value=TerminalPairState.entered_password,
                    on_change=TerminalPairState.set_entered_password,
                    type_="password",
                    width="220px",
                    height="50px",
                    font_size="xl",
                    autoComplete="new-password",
                ),
                rx.button(
                    "Unlock",
                    on_click=TerminalPairState.submit_password,
                    size="3",
                    width="120px",
                    height="50px",
                    font_size="xl",
                    margin_y="2",
                ),
                rx.cond(
                    TerminalPairState.error != "",
                    rx.text(TerminalPairState.error, color="red"),
                ),
            ),
        ),
        min_height="85vh",
        justify="center",
        align_items="center",
    )


app = rx.App()
app.add_page(index, route="/")
app.add_page(
    terminal_pairing_page,
    route="/pair-terminal"
)
