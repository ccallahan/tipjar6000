"""Welcome to Reflex! This file outlines the steps to create a basic app."""

import reflex as rx
from rxconfig import config
from square.client import Square
from square.environment import SquareEnvironment as square_env
import threading
import time
import os


# Configure your Square client (set your access token and location ID)
SQUARE_ACCESS_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN")
SQUARE_LOCATION_ID = os.environ.get("SQUARE_LOCATION_ID")

square_client = Square(
    token=SQUARE_ACCESS_TOKEN,
    environment=square_env.PRODUCTION,  # Change to "production" for live
)

shared_device_id = ""  # <-- Add this shared variable


class State(rx.State):
    """The app state."""

    amount: int = 0
    custom_amount: str = ""
    transaction_success: bool = False

    def set_amount(self, amount: int):
        self.amount = amount
        self.trigger_transaction()

    def set_custom_amount(self, value: str):
        self.custom_amount = value

    def submit_custom_amount(self):
        try:
            amt = int(float(self.custom_amount) * 100)
            self.amount = amt
            self.trigger_transaction()
        except Exception:
            pass  # Add error handling as needed

    def reset_page(self):
        self.amount = 0
        self.custom_amount = ""
        self.transaction_success = False

    def trigger_transaction(self):
        global shared_device_id
        if not shared_device_id:
            print("Device ID is not set. Please pair the terminal first.")
            return
        # Use Square Terminal API to create a checkout request
        try:
            idempotency_key = f"trans-{int(time.time())}"  # import at function scope to avoid circular import
            body = {
                "idempotency_key": idempotency_key,
                "checkout": {
                    "amount_money": {
                        "amount": self.amount,
                        "currency": "USD",
                    },
                    "device_options": {
                        "device_id": shared_device_id,  # Use the shared device_id
                    },
                    "note": "Tip from Reflex POS",
                    "reference_id": "reflex-pos-transaction",
                }
            }
            result = square_client.terminal.checkouts.create(**body)
            if hasattr(result, "checkout") and result.checkout:
                print("Checkout created:", result.checkout)
                self.transaction_success = True
                threading.Thread(target=self._delayed_reset, daemon=True).start()
            else:
                print("Terminal checkout failed:", getattr(result, "errors", "Unknown error"))
        except Exception as e:
            print("Error processing terminal checkout:", e)

    def _delayed_reset(self):
        time.sleep(10)
        self.reset_page()


class TerminalPairState(rx.State):
    password: str = ""
    entered_password: str = ""
    pairing_code: str = ""
    device_id: str = ""  # <-- Store the device_id here
    error: str = ""
    is_authenticated: bool = False
    is_pairing: bool = False

    def set_entered_password(self, value: str):
        self.entered_password = value
        self.error = ""

    def submit_password(self):
        if (
            self.entered_password == self.password or
            self.entered_password == os.environ.get("PAIRING_PASSWORD")  # Use env var or default
        ):
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
            # Use the Square Devices API to create a device code for pairing
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
                # Now poll for the device_id after pairing
                threading.Thread(target=self._poll_for_device_id, args=(result.device_code.code,), daemon=True).start()
            else:
                self.error = (
                    f"Pairing failed: {getattr(result, 'errors', 'Unknown error')}"
                )
        except Exception as e:
            self.error = f"Pairing failed: {e}"
        self.is_pairing = False

    def _poll_for_device_id(self, pairing_code):
        """Poll the Devices API to get the device_id after pairing is complete."""
        devices_api = square_client.devices
        global shared_device_id
        for _ in range(30):  # Poll for up to 30 seconds
            try:
                devices = devices_api.codes.list(location_id=SQUARE_LOCATION_ID)
                # Find the device code with the matching pairing code and status PAIRED
                for device_code in getattr(devices, "items", []):
                    if (
                        getattr(device_code, "code", "") == pairing_code and
                        getattr(device_code, "status", "") == "PAIRED"
                    ):
                        self.device_id = getattr(device_code, "device_id", "")
                        shared_device_id = self.device_id  # <-- update shared variable
                        return
            except Exception:
                pass
            time.sleep(1)


def index() -> rx.Component:
    # Welcome Page (Index)
    return rx.container(
        rx.color_mode.button(position="top-right"),
        rx.vstack(
            rx.heading("Select an amount to tip!", size="9"),
            rx.grid(
                rx.button(
                    "$1",
                    on_click=lambda: State.set_amount(100),
                    size="3",
                    height="80px",
                    width="120px",
                    font_size="2xl",
                ),
                rx.button(
                    "$5",
                    on_click=lambda: State.set_amount(500),
                    size="3",
                    height="80px",
                    width="120px",
                    font_size="2xl",
                ),
                rx.button(
                    "$10",
                    on_click=lambda: State.set_amount(1000),
                    size="3",
                    height="80px",
                    width="120px",
                    font_size="2xl",
                ),
                rx.input(
                    placeholder="Custom $",
                    value=State.custom_amount,
                    on_change=State.set_custom_amount,
                    width="120px",
                    height="80px",
                    font_size="2xl",
                    padding="0 10px",
                ),
                rx.button(
                    "Charge Custom",
                    on_click=State.submit_custom_amount,
                    size="3",
                    height="80px",
                    width="180px",
                    font_size="2xl",
                ),
                columns="5",  # Responsive: 2 columns on small, 3 on medium+
                gap="4",
                justify_items="center",
                align_items="center",
                margin_y="2",
            ),
            spacing="5",
            justify="center",
            min_height="85vh",
        ),
    )


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
app.add_page(terminal_pairing_page, route="/pair-terminal")
