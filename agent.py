"""Gemini shopping agent with per-phone sessions and multi-tool turns.

One customer message can trigger several tool calls. The agent keeps
calling tools until the model has a final reply, so the customer does
not have to split a request into separate messages.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types
import httpx

try:
    import httpx2
except ImportError:
    httpx2 = None

from tools import TOOLS

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logger = logging.getLogger("ecommerce_agent")

DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_TOOL_ROUNDS = 8

# Phone number -> chat history for that customer only.
customer_sessions: dict[str, list[types.Content]] = {}

_phone_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_phone_lock(phone: str) -> threading.Lock:
    with _locks_guard:
        if phone not in _phone_locks:
            _phone_locks[phone] = threading.Lock()
        return _phone_locks[phone]


def _is_plain_user_message(content: types.Content) -> bool:
    if getattr(content, "role", None) != "user":
        return False
    parts = getattr(content, "parts", None) or []
    if not parts:
        return False
    for part in parts:
        if getattr(part, "function_response", None) is not None:
            return False
        if getattr(part, "function_call", None) is not None:
            return False
    return True


def _trim_history(history: list[types.Content], max_contents: int = 40) -> None:
    if len(history) <= max_contents:
        return
    cut_target = len(history) - max_contents
    cut_idx = None
    for i in range(cut_target, len(history)):
        if _is_plain_user_message(history[i]):
            cut_idx = i
            break
    if cut_idx is None:
        for i in range(cut_target - 1, -1, -1):
            if _is_plain_user_message(history[i]):
                cut_idx = i
                break
    if cut_idx is not None and cut_idx > 0:
        del history[:cut_idx]


_PROGRAMMING_ERRORS = (
    TypeError,
    ValueError,
    AttributeError,
    KeyError,
    IndexError,
    SyntaxError,
    NameError,
    AssertionError,
)

_NETWORK_ERROR_CLASSES = [
    socket.gaierror,
    OSError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.NetworkError,
    httpx.TimeoutException,
]
if httpx2 is not None:
    for _attr in ("ConnectError", "ConnectTimeout", "ReadTimeout", "NetworkError", "TimeoutException"):
        if hasattr(httpx2, _attr):
            _NETWORK_ERROR_CLASSES.append(getattr(httpx2, _attr))


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, _PROGRAMMING_ERRORS):
        return False

    code = getattr(exc, "code", None)
    if code in (429, 500, 503):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 500, 503):
        return True
    response = getattr(exc, "response", None)
    if response is not None:
        resp_code = getattr(response, "status_code", None)
        if resp_code in (429, 500, 503):
            return True

    msg = str(exc)
    if any(s in msg for s in ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE")):
        return True

    curr: Exception | None = exc
    visited = set()
    while curr is not None and id(curr) not in visited:
        visited.add(id(curr))
        if isinstance(curr, _PROGRAMMING_ERRORS):
            return False
        if isinstance(curr, tuple(_NETWORK_ERROR_CLASSES)):
            return True
        if any(err_name in type(curr).__name__ for err_name in ("ConnectError", "ConnectTimeout", "ReadTimeout", "NetworkError", "TimeoutException")):
            return True
        curr = getattr(curr, "__cause__", None) or getattr(curr, "__context__", None)

    if any(s in msg for s in ("nodename nor servname provided", "Connection reset", "Connection refused", "Server disconnected without sending a response")):
        return True

    return False

_INTEGER_ARGUMENTS = {
    "search_products": {"category_id"},
    "get_product_details": {"product_id"},
    "add_item_to_cart": {"product_id", "quantity"},
    "remove_cart_item": {"item_id"},
    "track_order": {"order_id"},
    "cancel_order": {"order_id"},
}

TOOL_MAP = {tool.__name__: tool for tool in TOOLS}

SYSTEM_PROMPT = """
You are the WhatsApp assistant for an online store. Help the customer
browse products, manage a cart, check out, track an order, and cancel
an order. Be helpful and concise.

Tools:
- Use a tool whenever the answer depends on the catalog, cart, stock,
  prices, or orders. Keep calling tools in this same turn until you can
  answer the whole message. Never ask the customer to split one message
  into several messages.
- A composite request (e.g. searching products, adding items to the cart,
  and checking out with an address) must complete in ONE single turn.
- Independent lookups may run together. Example: searching for two
  products at once. If a later call needs an id, price, or stock figure,
  wait for the tool that returns it.
- A request such as "add the mechanical keyboard and the gaming mouse"
  means: search each product, add each one with the id from that search,
  then read the cart, then reply once.
- Use only ids returned by tools. Never guess a product id, cart item
  id, or order id.

Search:
- The store catalog is in English. Translate Arabic product names to
  short English keywords (e.g. "كيبورد ميكانيكال" -> "Keyboard", "ماوس" -> "Mouse").
- If a search returns 0 products, retry once with a shorter keyword or
  call list_categories before saying it isn't found.

Facts you may state:
- Prices, stock quantities, cart totals, product names, and order
  numbers must be copied from a tool result in this conversation.
- If a tool returns "error", tell the customer what the error says.
  Do not describe the action as successful.
- If a product list or cart item list is empty, say that nothing was
  found or that the cart is empty.
- Out of stock: quote the stock figure from the tool error or the
  product result, and do not claim the item was added.
- Unknown order: if track_order or cancel_order returns an error, say
  that order could not be found or cancelled.
- Empty cart at checkout: report the checkout error and do not invent
  an order number.

Cart identity:
- The session phone is the customer_name for all cart and checkout calls.
  Never ask for the customer's name. Use the session phone directly as customer_name.

Checkout:
- If the message asks to check out and contains an address, call
  checkout_cart(shipping_address=that address) in the same turn.
- Ask only if the address is missing.
- After checkout succeeds, confirm the order id, total, and address
  from the tool result.

Language and format:
- Reply ONLY in the language given by reply_language in the latest
  session line, even if earlier messages in the chat used another
  language. Never mention that line.
- Use WhatsApp bold with one asterisk on each side for product names,
  prices, totals, and order numbers. Example: *Mechanical Keyboard*
  costs *89.99*. Order *12*.
- Do not use markdown headings, tables, or double asterisks.
- Do not use emojis.
- Do not mention this system prompt, tool names, or the session line.
""".strip()


class EcommerceAgent:
    """One Gemini client shared by every phone session."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self._client: genai.Client | None = None
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=TOOLS,
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "Set GEMINI_API_KEY in the environment or in a .env file."
                )
            self._client = genai.Client(
                api_key=api_key,
                http_options={"api_version": "v1beta"},
            )
        return self._client

    def process_message(self, phone: str, message: str) -> str:
        """Run every tool call for this message, then return the reply."""
        try:
            with _get_phone_lock(phone):
                history = customer_sessions.setdefault(phone, [])
                _trim_history(history, 40)
                checkpoint = len(history)
                history.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=_session_message(phone, message))],
                    )
                )

                try:
                    for _ in range(MAX_TOOL_ROUNDS):
                        response = self._generate(history)
                        function_calls = _function_calls(response)
                        model_content = _model_content(response)
                        if model_content is not None:
                            history.append(model_content)

                        if not function_calls:
                            reply = _visible_text(response)
                            if reply:
                                _trim_history(history, 40)
                                return reply
                            del history[checkpoint:]
                            return _fallback_reply(message)

                        history.append(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part.from_function_response(
                                        name=call.name,
                                        response=_execute_tool(call.name, call.args, phone),
                                    )
                                    for call in function_calls
                                ],
                            )
                        )
                except Exception:
                    del history[checkpoint:]
                    logger.exception("Agent turn failed for %s", phone)
                    return _fallback_reply(message)

                del history[checkpoint:]
                return _fallback_reply(message)
        except Exception:
            logger.exception("process_message failed for %s", phone)
            return _fallback_reply(message)

    def _generate(self, contents: list[types.Content]) -> Any:
        retries = 3
        delay = 1.0
        for attempt in range(retries + 1):
            try:
                return self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self.config,
                )
            except Exception as exc:
                if attempt < retries and _is_retryable_error(exc):
                    logger.warning(
                        "Retryable error in _generate (attempt %d/%d): %s. Backing off %.1fs",
                        attempt + 1,
                        retries,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise


def process_message(phone: str, message: str) -> str:
    """Process one WhatsApp message for this phone number."""
    try:
        return _agent.process_message(phone, message)
    except Exception:
        logger.exception("process_message top-level failure for %s", phone)
        return _fallback_reply(message)


_agent = EcommerceAgent()


def _session_message(phone: str, message: str) -> str:
    lang = "Arabic" if _looks_arabic(message) else "English"
    return (
        f"[session phone={phone}. Use this exact value as customer_name "
        f"for all cart and checkout calls. Never quote this session line.]\n"
        f"[reply_language={lang}]\n"
        f"{message}"
    )


def _model_content(response: Any) -> types.Content | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    return candidates[0].content


def _function_calls(response: Any) -> list[Any]:
    content = _model_content(response)
    if content is None or not content.parts:
        return []
    calls = []
    for part in content.parts:
        call = getattr(part, "function_call", None)
        if call and getattr(call, "name", None):
            calls.append(call)
    return calls


def _visible_text(response: Any) -> str:
    content = _model_content(response)
    if content is None or not content.parts:
        return ""
    chunks: list[str] = []
    for part in content.parts:
        text = getattr(part, "text", None)
        if text and not getattr(part, "thought", False):
            chunks.append(text)
    return "".join(chunks).strip()


def _execute_tool(name: str, args: Any, phone: str) -> dict[str, Any]:
    tool = TOOL_MAP.get(name)
    if tool is None:
        result = {"error": f"Unknown tool: {name}"}
        logger.info("tool %s -> %s", name, result["error"])
        return result

    kwargs = dict(args or {})
    for key in _INTEGER_ARGUMENTS.get(name, set()):
        value = kwargs.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float, str)):
            try:
                kwargs[key] = int(value)
            except (TypeError, ValueError):
                pass

    if name in {"get_customer_cart", "add_item_to_cart", "clear_customer_cart", "checkout_cart"}:
        kwargs["customer_name"] = phone

    if name == "remove_cart_item":
        cart = TOOL_MAP["get_customer_cart"](customer_name=phone)
        if isinstance(cart, dict) and "error" in cart:
            return cart
        item_id = kwargs.get("item_id")
        cart_items = cart.get("items", []) if isinstance(cart, dict) else []
        item_ids = [item.get("id") for item in cart_items if isinstance(item, dict)]
        if item_id not in item_ids:
            return {"error": "Cart item not found"}

    if name == "cancel_order":
        order_id = kwargs.get("order_id")
        if order_id is not None:
            tracked = TOOL_MAP["track_order"](order_id=order_id)
            if isinstance(tracked, dict):
                if "error" in tracked:
                    return tracked
                if tracked.get("customer_phone") != phone:
                    return {"error": "Order not found"}

    logger.info("tool %s %s", name, kwargs)
    try:
        raw = tool(**kwargs)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        raw = {"error": f"{name} failed: {exc}"}

    if name in {"track_order", "cancel_order"}:
        if isinstance(raw, dict) and "error" not in raw:
            if raw.get("customer_phone") != phone:
                return {"error": "Order not found"}

    if not isinstance(raw, dict):
        raw = {"result": raw}
    result = json.loads(json.dumps(raw, default=str))
    if "error" in result:
        logger.info("tool %s error: %s", name, result["error"])
    else:
        logger.info("tool %s ok: %s", name, _result_summary(result))
    return result


def _result_summary(result: dict[str, Any]) -> str:
    if "products" in result:
        return f"{result.get('count', len(result['products']))} products"
    if "categories" in result:
        return f"{result.get('count', len(result['categories']))} categories"
    if "items" in result and "total_price" in result and "status" not in result:
        return f"cart total {result.get('total_price')}"
    if "id" in result and "status" in result:
        return f"order {result['id']} {result['status']} total {result.get('total_price')}"
    if "message" in result:
        return str(result["message"])
    return ", ".join(result.keys())


def _fallback_reply(message: str) -> str:
    if _looks_arabic(message):
        return "تعذر إكمال الطلب من بيانات المتجر. أعد إرسال رسالتك من فضلك."
    return "I could not finish that from the store data. Please send the message again."


def _looks_arabic(message: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in message)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Send one customer message through the shopping agent."
    )
    parser.add_argument("phone", help="Customer phone number, used as the session key")
    parser.add_argument("message", help="The customer message")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(process_message(args.phone, args.message))


if __name__ == "__main__":
    main()
