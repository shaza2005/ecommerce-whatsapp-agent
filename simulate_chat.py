"""Simulation script for the WhatsApp E-Commerce Agent.

Supports interactive chat testing across phone sessions and automated
demonstration scenarios with end-to-end backend verification.

Usage:
    Interactive mode:
        python simulate_chat.py --phone 201000000001
        python simulate_chat.py --phone 201000000001 --verbose
        python simulate_chat.py --phone 201000000001 --no-rtl

    Automated demo mode:
        python simulate_chat.py --demo
        python simulate_chat.py --demo --verbose
        python simulate_chat.py --demo --no-rtl
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import arabic_reshaper
from bidi.algorithm import get_display

# Ensure the repository directory containing agent and tools is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
AGENT_DIR = os.path.join(SCRIPT_DIR, "ecommerce-whatsapp-agent")
if os.path.isdir(AGENT_DIR) and AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Load environment variables
load_dotenv()
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
if os.path.isdir(AGENT_DIR):
    load_dotenv(os.path.join(AGENT_DIR, ".env"))

import agent
import tools

logger = logging.getLogger("simulate_chat")


def check_backend() -> bool:
    """Verify that the FastAPI e-commerce backend is reachable.

    Returns:
        True if the backend responds successfully to GET /categories,
        False otherwise.
    """
    categories = tools.list_categories()
    if isinstance(categories, dict) and "error" in categories:
        return False
    return True


def print_check(description: str, passed: bool, details: str = "") -> bool:
    """Print a formatted test check result.

    Args:
        description: Description of the assertion.
        passed: Whether the assertion passed.
        details: Optional additional diagnostic details.

    Returns:
        The boolean value of `passed`.
    """
    status = "[PASS]" if passed else "[FAIL]"
    detail_str = f" ({details})" if details else ""
    print(f"  {status} {description}{detail_str}")
    return passed


def format_arabic_display(text: str, rtl_enabled: bool = True) -> str:
    """Format Arabic text for correct terminal rendering via reshaping and BiDi.

    Never modifies the text passed to the agent. Only reshapes and orders
    Arabic text when printing to the terminal if rtl_enabled is True.

    Args:
        text: The string to format for terminal output.
        rtl_enabled: Whether RTL display processing is active (default: True).

    Returns:
        The formatted string suitable for terminal display.
    """
    if not rtl_enabled or not text:
        return text
    if not any("\u0600" <= c <= "\u06ff" for c in text):
        return text
    try:
        lines = text.split("\n")
        formatted_lines = []
        for line in lines:
            if any("\u0600" <= c <= "\u06ff" for c in line):
                reshaped = arabic_reshaper.reshape(line)
                formatted_lines.append(get_display(reshaped))
            else:
                formatted_lines.append(line)
        return "\n".join(formatted_lines)
    except Exception:
        return text


def run_interactive(phone: str, verbose: bool = False, rtl_enabled: bool = True) -> None:
    """Run an interactive console chat session with the WhatsApp agent.

    Args:
        phone: Initial customer phone number used as the WhatsApp session ID.
        verbose: When True, shows tool execution logs.
        rtl_enabled: When True, reshapes and reorders Arabic text for terminal display.
    """
    current_phone = phone
    print("=" * 65)
    print(" E-Commerce WhatsApp Agent - Interactive Chat Simulator")
    print("=" * 65)
    print(f"Active Session Phone : {current_phone}")
    print("Available Commands   :")
    print("  /phone <number>    : Switch session to another phone number")
    print("  /reset             : Clear chat history for the active session")
    print("  /exit              : Quit the simulator")
    print("=" * 65)
    print()

    while True:
        try:
            user_input = input(f"[{current_phone}] > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting chat simulator. Goodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() == "/exit":
            print("Exiting chat simulator. Goodbye!")
            break

        if user_input.lower() == "/reset":
            if current_phone in agent.customer_sessions:
                del agent.customer_sessions[current_phone]
            print(f"Chat history cleared for {current_phone}.\n")
            continue

        if user_input.lower().startswith("/phone"):
            parts = user_input.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                current_phone = parts[1].strip()
                print(f"Switched session to phone: {current_phone}\n")
            else:
                print(f"Current session phone: {current_phone}\n")
            continue

        try:
            reply = agent.process_message(current_phone, user_input)
            display_reply = format_arabic_display(reply, rtl_enabled=rtl_enabled)
            print(f"\n[Agent]\n{display_reply}\n")
        except Exception as exc:
            print(f"\n[Error] {exc}\n")


FALLBACK_REPLIES = {
    "تعذر إكمال الطلب من بيانات المتجر. أعد إرسال رسالتك من فضلك.",
    "I could not finish that from the store data. Please send the message again.",
}


def is_fallback_error(reply: str) -> bool:
    """Check if the agent reply is a generic fallback error response."""
    return reply.strip() in FALLBACK_REPLIES


def run_demo(verbose: bool = False, rtl_enabled: bool = True) -> int:
    """Run automated demonstration scenarios and validate backend state.

    Args:
        verbose: When True, shows tool execution logs.
        rtl_enabled: When True, reshapes and reorders Arabic text for terminal display.

    Returns:
        Exit code: 0 if all checks pass, 1 if any check fails.
    """
    phone_a = "201000000001"
    phone_b = "201000000002"

    print("=" * 70)
    print(" Starting Automated Demonstration Scenarios")
    print("=" * 70)

    # Clean initial state for repeatable runs
    tools.clear_customer_cart(phone_a)
    tools.clear_customer_cart(phone_b)
    agent.customer_sessions.pop(phone_a, None)
    agent.customer_sessions.pop(phone_b, None)

    # Record baseline stock and highest existing order id
    p1_init = tools.get_product_details(1)
    p2_init = tools.get_product_details(2)
    kb_baseline = int(p1_init.get("stock", 0))
    mouse_baseline = int(p2_init.get("stock", 0))

    orders_init: Any = tools._request("GET", "/orders")
    orders_init_list: List[Dict[str, Any]] = orders_init if isinstance(orders_init, list) else []
    max_order_id = max((int(o.get("id", 0)) for o in orders_init_list), default=0)

    all_passed = True

    # -------------------------------------------------------------------------
    # Scenario 1: Composite message from Phone A (search, add 2 items, checkout)
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 1: Composite Query & Checkout (Phone A: {phone_a})")
    print("----------------------------------------------------------------------")

    msg1 = (
        "مساء الخير، شوفيلي الكيبورد الميكانيكال والماوس الجيمينج متاحين ولا لأ؟ "
        "وضيفلي واحد من كل نوع للسلة، واعملي تشيك أوت لعنواني في القاهرة - المعادي"
    )
    print(f"Customer:\n  {format_arabic_display(msg1, rtl_enabled=rtl_enabled)}\n")
    reply1 = agent.process_message(phone_a, msg1)
    print(f"Agent Reply:\n  {format_arabic_display(reply1, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply1):
        c_err1 = print_check("Agent executed without fallback error", False, "agent error")
        all_passed = all_passed and c_err1

    orders_after_1: Any = tools._request("GET", "/orders")
    orders_after_list_1: List[Dict[str, Any]] = orders_after_1 if isinstance(orders_after_1, list) else []
    new_orders_a = [
        o for o in orders_after_list_1
        if o.get("customer_phone") == phone_a and int(o.get("id", 0)) > max_order_id
    ]
    new_order: Dict[str, Any] = new_orders_a[-1] if new_orders_a else {}
    new_order_id: Optional[int] = new_order.get("id")

    # Verification 1.1: Order exists with 2 items
    items = new_order.get("items", [])
    c1 = print_check(
        "New order exists with exactly 2 items",
        bool(new_orders_a) and len(items) == 2,
        f"OrderID={new_order_id}, ItemsCount={len(items)}",
    )
    all_passed = all_passed and c1

    # Verification 1.2: Stock = baseline - 1 for each
    p1_after_1 = tools.get_product_details(1)
    p2_after_1 = tools.get_product_details(2)
    kb_after_1 = int(p1_after_1.get("stock", 0))
    mouse_after_1 = int(p2_after_1.get("stock", 0))
    c2 = print_check(
        "Product stocks dropped by 1 each from baseline",
        kb_after_1 == kb_baseline - 1 and mouse_after_1 == mouse_baseline - 1,
        f"Keyboard: {kb_after_1} (expected {kb_baseline - 1}), Mouse: {mouse_after_1} (expected {mouse_baseline - 1})",
    )
    all_passed = all_passed and c2

    # Verification 1.3: Shipping address is stored
    stored_address = str(new_order.get("shipping_address") or "")
    c3 = print_check(
        "Shipping address is stored accurately on the new order",
        "المعادي" in stored_address or "القاهرة" in stored_address,
        f"Address={stored_address!r}",
    )
    all_passed = all_passed and c3

    # -------------------------------------------------------------------------
    # Scenario 2: Asking for Waterproof Bluetooth Speaker (stock 0) -> not added
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 2: Out of Stock Item (Phone A: {phone_a})")
    print("----------------------------------------------------------------------")

    msg2 = "ضيفلي Waterproof Bluetooth Speaker للسلة"
    print(f"Customer:\n  {format_arabic_display(msg2, rtl_enabled=rtl_enabled)}\n")
    reply2 = agent.process_message(phone_a, msg2)
    print(f"Agent Reply:\n  {format_arabic_display(reply2, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply2):
        c_err2 = print_check("Agent executed without fallback error", False, "agent error")
        all_passed = all_passed and c_err2

    cart_a = tools.get_customer_cart(phone_a)
    cart_items = cart_a.get("items", []) if isinstance(cart_a, dict) else []
    speaker_in_cart = any(
        item.get("product_id") == 7 or "speaker" in item.get("product_name", "").lower()
        for item in cart_items
    )
    c4 = print_check(
        "Out of stock item was not added to cart",
        not speaker_in_cart,
        f"CartItems={len(cart_items)}",
    )
    all_passed = all_passed and c4

    # -------------------------------------------------------------------------
    # Scenario 3: Checkout with an empty cart from Phone B
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 3: Empty Cart Checkout (Phone B: {phone_b})")
    print("----------------------------------------------------------------------")

    tools.clear_customer_cart(phone_b)
    orders_raw_b = tools._request("GET", "/orders")
    orders_list_b = orders_raw_b if isinstance(orders_raw_b, list) else []
    orders_b_before = [o for o in orders_list_b if o.get("customer_phone") == phone_b]

    msg3 = "اعملي تشيك أوت لعنواني 5 شارع النصر"
    print(f"Customer:\n  {format_arabic_display(msg3, rtl_enabled=rtl_enabled)}\n")
    reply3 = agent.process_message(phone_b, msg3)
    print(f"Agent Reply:\n  {format_arabic_display(reply3, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply3):
        c_err3 = print_check("Agent executed without fallback error", False, "agent error")
        all_passed = all_passed and c_err3

    orders_check_b: Any = tools._request("GET", "/orders")
    orders_list_b_after: List[Dict[str, Any]] = orders_check_b if isinstance(orders_check_b, list) else []
    orders_b_after = [o for o in orders_list_b_after if o.get("customer_phone") == phone_b]

    c5 = print_check(
        "Checkout rejected when cart is empty; no order created",
        len(orders_b_after) == len(orders_b_before),
        f"OrdersCount={len(orders_b_after)}",
    )
    all_passed = all_passed and c5

    # -------------------------------------------------------------------------
    # Scenario 4: Track the order, cancel it, check stock restored
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 4: Track & Cancel Order (Phone A: {phone_a}, OrderID: {new_order_id})")
    print("----------------------------------------------------------------------")

    if new_order_id is None:
        c_err4_id = print_check("New order ID available from Scenario 1", False, "No new order found in Scenario 1")
        all_passed = all_passed and c_err4_id
    else:
        msg4 = f"عايز الغي الأوردر رقم {new_order_id}"
        print(f"Customer:\n  {format_arabic_display(msg4, rtl_enabled=rtl_enabled)}\n")
        reply4 = agent.process_message(phone_a, msg4)
        print(f"Agent Reply:\n  {format_arabic_display(reply4, rtl_enabled=rtl_enabled)}\n")

        if is_fallback_error(reply4):
            c_err4 = print_check("Agent executed without fallback error", False, "agent error")
            all_passed = all_passed and c_err4

        tracked_order = tools.track_order(int(new_order_id))
        order_status = tracked_order.get("status")

        c6 = print_check(
            "Order status updated to cancelled",
            order_status == "cancelled",
            f"Status={order_status!r}",
        )
        all_passed = all_passed and c6

        p1_cancelled = tools.get_product_details(1)
        p2_cancelled = tools.get_product_details(2)
        kb_stock_restored = int(p1_cancelled.get("stock", 0))
        mouse_stock_restored = int(p2_cancelled.get("stock", 0))

        c7 = print_check(
            "Product stock restored to baseline after cancellation",
            kb_stock_restored == kb_baseline and mouse_stock_restored == mouse_baseline,
            f"Keyboard: {kb_after_1} -> {kb_stock_restored} (baseline: {kb_baseline}), "
            f"Mouse: {mouse_after_1} -> {mouse_stock_restored} (baseline: {mouse_baseline})",
        )
        all_passed = all_passed and c7

    # -------------------------------------------------------------------------
    # Scenario 5: Phones A and B have independent carts
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 5: Cart Isolation Between Phone Sessions")
    print("----------------------------------------------------------------------")

    tools.clear_customer_cart(phone_a)
    tools.clear_customer_cart(phone_b)
    agent.customer_sessions.pop(phone_a, None)
    agent.customer_sessions.pop(phone_b, None)

    msg5_a = "ضيفلي كيبورد ميكانيكال للسلة"
    print(f"Customer (Phone A):\n  {format_arabic_display(msg5_a, rtl_enabled=rtl_enabled)}\n")
    reply5_a = agent.process_message(phone_a, msg5_a)
    print(f"Agent Reply:\n  {format_arabic_display(reply5_a, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply5_a):
        c_err5a = print_check("Agent executed without fallback error for Phone A", False, "agent error")
        all_passed = all_passed and c_err5a

    msg5_b = "ضيفلي ماوس جيمنج للسلة"
    print(f"Customer (Phone B):\n  {format_arabic_display(msg5_b, rtl_enabled=rtl_enabled)}\n")
    reply5_b = agent.process_message(phone_b, msg5_b)
    print(f"Agent Reply:\n  {format_arabic_display(reply5_b, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply5_b):
        c_err5b = print_check("Agent executed without fallback error for Phone B", False, "agent error")
        all_passed = all_passed and c_err5b

    cart_res_a = tools.get_customer_cart(phone_a)
    cart_res_b = tools.get_customer_cart(phone_b)
    items_a = cart_res_a.get("items", []) if isinstance(cart_res_a, dict) else []
    items_b = cart_res_b.get("items", []) if isinstance(cart_res_b, dict) else []

    ids_a = [i.get("product_id") for i in items_a]
    ids_b = [i.get("product_id") for i in items_b]

    cart_a_isolated = 1 in ids_a and 2 not in ids_a
    cart_b_isolated = 2 in ids_b and 1 not in ids_b

    c8 = print_check(
        "Phone A and Phone B maintain strictly isolated carts",
        cart_a_isolated and cart_b_isolated,
        f"PhoneA Items={ids_a}, PhoneB Items={ids_b}",
    )
    all_passed = all_passed and c8

    # -------------------------------------------------------------------------
    # Scenario 6: An English message gets an English reply
    # -------------------------------------------------------------------------
    print("\n----------------------------------------------------------------------")
    print(f"Scenario 6: English Language Consistency")
    print("----------------------------------------------------------------------")

    msg6 = "What categories and products do you have available in the store?"
    print(f"Customer:\n  {format_arabic_display(msg6, rtl_enabled=rtl_enabled)}\n")
    reply6 = agent.process_message(phone_a, msg6)
    print(f"Agent Reply:\n  {format_arabic_display(reply6, rtl_enabled=rtl_enabled)}\n")

    if is_fallback_error(reply6):
        c_err6 = print_check("Agent executed without fallback error", False, "agent error")
        all_passed = all_passed and c_err6

    has_arabic = any("\u0600" <= c <= "\u06ff" for c in reply6)
    is_english = bool(reply6) and not has_arabic and any(
        kw in reply6.lower()
        for kw in ["category", "categories", "product", "products", "electronic", "audio", "accessori", "keyboard"]
    )
    c9 = print_check(
        "English query received an English reply with no Arabic characters",
        is_english,
        f"Length={len(reply6)}, HasArabic={has_arabic}",
    )
    all_passed = all_passed and c9

    # Clean up carts
    tools.clear_customer_cart(phone_a)
    tools.clear_customer_cart(phone_b)

    print("\n" + "=" * 70)
    if all_passed:
        print(" ALL SCENARIO CHECKS PASSED SUCCESSFULLY!")
        print("=" * 70)
        return 0
    else:
        print(" SOME SCENARIO CHECKS FAILED.")
        print("=" * 70)
        return 1


def main() -> None:
    """Parse command line arguments and launch interactive or demo mode."""
    parser = argparse.ArgumentParser(
        description="Simulate WhatsApp chat interactions with the e-commerce agent."
    )
    parser.add_argument(
        "--phone",
        type=str,
        default="201000000001",
        help="Customer phone number used as the WhatsApp session ID (default: 201000000001)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run scripted demonstration scenarios and backend assertions",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging to display tool execution logs",
    )
    parser.add_argument(
        "--no-rtl",
        action="store_true",
        help="Disable Arabic text reshaping and BiDi reordering for terminal display",
    )
    args = parser.parse_args()

    rtl_enabled = not args.no_rtl

    # Configure logging
    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(message)s")

    # Startup Backend Check
    if not check_backend():
        print(f"Error: Backend unreachable at {tools.API_BASE_URL}.")
        print("Please start the backend first (e.g. cd backend && uvicorn main:app).")
        sys.exit(1)

    if args.demo:
        exit_code = run_demo(verbose=args.verbose, rtl_enabled=rtl_enabled)
        sys.exit(exit_code)
    else:
        run_interactive(phone=args.phone, verbose=args.verbose, rtl_enabled=rtl_enabled)


if __name__ == "__main__":
    main()
