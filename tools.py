"""Tool functions the Gemini agent calls against the FastAPI e-commerce backend.

Every price, stock quantity, cart total, and order number in a return value
comes from the API response. These functions never invent catalog or order
data. When a call fails, the result is a dictionary with an ``error`` key
and the backend message, so the agent can report the failure exactly.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
import httpx

load_dotenv()

API_BASE_URL = os.getenv("ECOMMERCE_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
_TIMEOUT_SECONDS = float(os.getenv("ECOMMERCE_API_TIMEOUT", "15"))


def _error_payload(message: str, status_code: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if status_code is not None:
        payload["status_code"] = status_code
    return payload


def _detail_from_response(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        return text or f"HTTP {response.status_code}"

    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, list):
            return "; ".join(str(item) for item in detail)
        return str(detail)
    return str(body)


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Perform one HTTP call and return parsed JSON or an error dictionary."""
    url = f"{API_BASE_URL}{path}"
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            response = client.request(method, url, params=params, json=json_body)
    except httpx.RequestError as exc:
        return _error_payload(f"Backend unreachable at {API_BASE_URL}: {exc}")

    if response.status_code >= 400:
        return _error_payload(_detail_from_response(response), response.status_code)

    if not response.content:
        return {"ok": True}

    try:
        return response.json()
    except ValueError:
        return _error_payload(
            f"Backend returned a non-JSON body (HTTP {response.status_code}).",
            response.status_code,
        )


def _as_record(result: dict[str, Any] | list[Any], list_key: str) -> dict[str, Any]:
    """Wrap a JSON list so every tool returns one dictionary."""
    if isinstance(result, dict):
        return result
    return {list_key: result, "count": len(result)}


def list_categories() -> dict[str, Any]:
    """List every product category currently stored in the catalog.

    Call this when the customer asks what the store sells, or before using
    a category filter. The ``id`` values in the result are the only valid
    values for ``search_products(category_id=...)``.

    Returns:
        A dictionary from GET /categories:
        - categories: list of objects with id, name, and description.
        - count: how many categories the backend returned.
        On failure the dictionary contains ``error`` (and ``status_code``
        when the server responded). Report that message. Do not invent
        category names or ids.
    """
    result = _request("GET", "/categories")
    return _as_record(result, "categories")


def search_products(
    keyword: str,
    category_id: int | None = None,
    in_stock_only: bool = False,
) -> dict[str, Any]:
    """Search the catalog by name, with optional category and stock filters.

    Use this to find products before quoting a price, checking availability,
    or adding an item to the cart. keyword is an English substring of the
    product name; use short keywords like "Keyboard". Pass ``category_id``
    only when it came from ``list_categories``. Set ``in_stock_only`` to True
    when the customer wants items that can be purchased now.

    Args:
        keyword: Text to match against product names; keyword is an English
            substring of the product name; use short keywords like "Keyboard".
            An empty string returns the unfiltered catalog page (still subject
            to the other filters).
        category_id: Category id from ``list_categories``. Omit it to
            search every category.
        in_stock_only: When True, the backend returns only products whose
            stock is greater than zero.

    Returns:
        A dictionary from GET /products:
        - products: list of objects with id, name, description, price,
          stock, and category_id. Price and stock are the backend values.
        - count: how many products were returned (the API page size is
          at most 20).
        On failure the dictionary contains ``error``. If ``products`` is
        empty, tell the customer nothing matched. Do not invent products,
        prices, or stock quantities.
    """
    params: dict[str, Any] = {"search": keyword, "in_stock_only": in_stock_only}
    if category_id is not None:
        params["category_id"] = category_id

    result = _request("GET", "/products", params=params)
    return _as_record(result, "products")


def get_product_details(product_id: int) -> dict[str, Any]:
    """Fetch one product by the id returned from search or the cart.

    Call this when the customer asks about a specific item, or before
    adding it to the cart if you need the current price and stock.

    Args:
        product_id: Numeric product id from ``search_products`` or a cart
            item. Do not guess an id.

    Returns:
        The product object from GET /products/{product_id}: id, name,
        description, price, stock, and category_id.
        On failure the dictionary contains ``error`` (404 when the id
        does not exist). Do not invent a price or stock figure.
    """
    return _as_record(_request("GET", f"/products/{product_id}"), "products")


def get_customer_cart(customer_name: str) -> dict[str, Any]:
    """Read the current cart for one customer, including line totals.

    Call this before answering questions about what is in the cart, the
    cart total, or which line to remove. ``customer_name`` must be the
    name for this chat session, not a name you make up.

    Args:
        customer_name: Customer name that owns the cart. Matching is
            exact, as stored by the backend.

    Returns:
        The cart summary from GET /cart/{customer_name}:
        - customer_name
        - items: list of cart lines with id, product_id, product_name,
          unit_price, quantity, and subtotal. ``id`` is the cart item id
          required by ``remove_cart_item``.
        - total_items: sum of quantities.
        - total_price: sum of subtotals from the backend.
        An empty ``items`` list means the cart is empty. On failure the
        dictionary contains ``error``. Do not invent line items or totals.
    """
    encoded_name = quote(customer_name, safe="")
    result = _request("GET", f"/cart/{encoded_name}")
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the cart endpoint.")
    return result


def add_item_to_cart(
    customer_name: str,
    product_id: int,
    quantity: int = 1,
) -> dict[str, Any]:
    """Add a catalog product to the customer's cart, or increase its quantity.

    Call this only after ``product_id`` has been returned by
    ``search_products`` or ``get_product_details``. If that product is
    already in the cart, the backend adds ``quantity`` to the existing
    line. The backend rejects the call when the requested quantity exceeds
    stock.

    Args:
        customer_name: Customer name for this chat session.
        product_id: Product id from the catalog tools. Do not guess it.
        quantity: How many units to add. Must be an integer greater than 0.
            Defaults to 1.

    Returns:
        The cart line from POST /cart/items: id, customer_name, product_id,
        product_name, unit_price, quantity, and subtotal. Prices and the
        resulting quantity come from the backend.
        On failure the dictionary contains ``error``, including the
        backend stock message when there is not enough inventory.
    """
    if quantity <= 0:
        return _error_payload("Quantity must be greater than 0.")

    result = _request(
        "POST",
        "/cart/items",
        json_body={
            "customer_name": customer_name,
            "product_id": product_id,
            "quantity": quantity,
        },
    )
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the cart endpoint.")
    return result


def remove_cart_item(item_id: int) -> dict[str, Any]:
    """Remove one line from a cart by its cart item id.

    Call ``get_customer_cart`` first and use the line ``id`` from that
    response. This is not a product id.

    Args:
        item_id: Cart line id (the ``id`` field on a cart item), not
            ``product_id``.

    Returns:
        The backend confirmation from DELETE /cart/items/{item_id},
        typically ``{"message": "Item removed from cart"}``.
        On failure the dictionary contains ``error`` (404 when that line
        does not exist).
    """
    result = _request("DELETE", f"/cart/items/{item_id}")
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the cart endpoint.")
    return result


def clear_customer_cart(customer_name: str) -> dict[str, Any]:
    """Delete every line in the customer's cart.

    Call this only when the customer explicitly asks to empty the cart.
    This does not cancel an order that was already placed.

    Args:
        customer_name: Customer name for this chat session.

    Returns:
        The backend confirmation from DELETE /cart/clear/{customer_name}.
        On failure the dictionary contains ``error``.
    """
    encoded_name = quote(customer_name, safe="")
    result = _request("DELETE", f"/cart/clear/{encoded_name}")
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the cart endpoint.")
    return result


def checkout_cart(
    customer_name: str,
    shipping_address: str,
) -> dict[str, Any]:
    """Place an order for the current cart and return the order the backend created.

    Call this only after the customer explicitly confirms checkout and
    provides a shipping address.

    The backend refuses checkout when the cart is empty or any line
    exceeds current stock. A successful checkout clears the cart and
    deducts stock. Quote the order id, prices, status, and shipping address
    only from this result.

    Args:
        customer_name: Customer name whose cart will be checked out. Use
            the exact name already used for this session's cart.
        shipping_address: Delivery address where the order should be shipped.

    Returns:
        The order from POST /orders/checkout:
        - id: order number assigned by the backend.
        - customer_name, customer_phone, shipping_address, total_price, status.
        - items: lines with product_id, product_name, unit_price,
          quantity, and subtotal.
        On failure the dictionary contains ``error`` and no order id.
        Do not invent an order number.
    """
    body: dict[str, Any] = {
        "customer_name": customer_name,
        "shipping_address": shipping_address,
        "customer_phone": customer_name,
    }

    result = _request("POST", "/orders/checkout", json_body=body)
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the checkout endpoint.")
    return result


def track_order(order_id: int) -> dict[str, Any]:
    """Look up one order by the id returned from checkout.

    Call this when the customer asks for order status, items, or the total
    charged. Use only an order id that checkout returned or that the
    customer provided and the backend confirms.

    Args:
        order_id: Numeric order id. Do not guess one.

    Returns:
        The order from GET /orders/{order_id}: id, customer_name,
        customer_phone, total_price, status, and items (product name,
        unit price, quantity, subtotal).
        On failure the dictionary contains ``error`` (404 when the order
        does not exist). Do not invent a status or total.
    """
    result = _request("GET", f"/orders/{order_id}")
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the orders endpoint.")
    return result


def cancel_order(order_id: int) -> dict[str, Any]:
    """Cancel an existing order and restore its stock.

    Call this only when the customer explicitly asks to cancel an order,
    and only with an order id from ``checkout_cart`` or ``track_order``.
    The backend sets the status to ``cancelled`` and returns the reserved
    units to product stock when the order was not already cancelled.

    Args:
        order_id: Numeric order id to cancel. Do not guess one.

    Returns:
        The updated order from PUT /orders/{order_id}/cancel, including
        ``status``. Confirm cancellation only when this status is
        ``cancelled``. On failure the dictionary contains ``error``.
    """
    result = _request("PUT", f"/orders/{order_id}/cancel")
    if isinstance(result, list):
        return _error_payload("Unexpected list response from the orders endpoint.")
    return result


# Registry used by the agent to expose every tool to Gemini in one place.
TOOLS: list[Any] = [
    list_categories,
    search_products,
    get_product_details,
    get_customer_cart,
    add_item_to_cart,
    remove_cart_item,
    clear_customer_cart,
    checkout_cart,
    track_order,
    cancel_order,
]
