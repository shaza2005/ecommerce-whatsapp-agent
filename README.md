# E-Commerce WhatsApp AI Agent

An intelligent, production-ready WhatsApp e-commerce shopping assistant powered by Google Gemini (`gemini-3.5-flash-lite`), FastAPI, SQLite, and the `neonize` WhatsApp framework.

The agent handles catalog search in both Arabic and English, manages customer shopping carts, executes single-turn composite checkouts with delivery addresses, tracks orders, and processes cancellations with automatic inventory restoration—all with strict zero-hallucination and session-isolation guarantees.

---

## Architecture

The system is organized into a clean 4-layer architecture:

```
+-------------------------------------------------------------------------+
|                  Layer 4: Client & Gateway Layer                        |
|   +---------------------------------+  +----------------------------+   |
|   |  WhatsApp Bot (bot.py)          |  |  CLI Simulator             |   |
|   |  - Neonize client               |  |  (simulate_chat.py)        |   |
|   |  - Terminal QR auth (segno)     |  |  - Interactive mode        |   |
|   |  - ThreadPoolExecutor workers   |  |  - Automated --demo suite  |   |
|   +---------------------------------+  +----------------------------+   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+------------------------------------+------------------------------------+
|                  Layer 3: Gemini AI Agent Layer                         |
|   +-----------------------------------------------------------------+   |
|   |  Agent Core (agent.py)                                          |   |
|   |  - Google GenAI SDK (gemini-3.5-flash-lite)                     |   |
|   |  - Multi-tool calling loop (up to 8 rounds / single-turn)       |   |
|   |  - Deterministic language adaptation ([reply_language=...])     |   |
|   |  - Retry logic for 429/500/503 & network errors with backoff   |   |
|   |  - Thread-safe per-phone session locking & history trimming     |   |
|   +-----------------------------------------------------------------+   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+------------------------------------+------------------------------------+
|                  Layer 2: Tools & Integration Layer                     |
|   +-----------------------------------------------------------------+   |
|   |  Tool Registry (tools.py)                                       |   |
|   |  - 10 typed client functions exposed to Gemini                  |   |
|   |  - HTTP client (httpx) calling FastAPI backend                  |   |
|   |  - Session phone injection & ownership validation               |   |
|   +-----------------------------------------------------------------+   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+------------------------------------+------------------------------------+
|                  Layer 1: Persistence & REST API                        |
|   +-----------------------------------------------------------------+   |
|   |  FastAPI Backend (backend/)                                     |   |
|   |  - REST API with Pydantic validation (main.py, schemas.py)       |   |
|   |  - SQLAlchemy ORM & SQLite database (models.py, database.py)    |   |
|   |  - Seed catalog data (seed_data.py -> ecommerce.db)             |   |
|   |  - Atomic inventory deduction & single-cancel stock restoration  |   |
|   +-----------------------------------------------------------------+   |
+-------------------------------------------------------------------------+
```

---

## Folder Tree

```
ecommerce-whatsapp-agent/
├── backend/
│   ├── database.py       # SQLite engine, sessionmaker, and Base
│   ├── ecommerce.db      # SQLite database file storing categories, products, carts, orders
│   ├── main.py           # FastAPI application endpoints, startup migrations, and business rules
│   ├── models.py         # SQLAlchemy ORM models (Category, Product, CartItem, Order, OrderItem)
│   ├── schemas.py        # Pydantic schemas, validation rules, and response shapes
│   └── seed_data.py      # Database seeding script with catalog items and stock
├── agent.py              # Gemini AI agent, system prompt, tool-execution loop, and retry backoff
├── bot.py                # Production WhatsApp bot via neonize, QR terminal auth, and ThreadPoolExecutor
├── simulate_chat.py      # Interactive CLI chat simulator and automated 6-scenario verification suite
├── tools.py              # 10 client tools exposed to Gemini, wrapping backend REST endpoints
├── requirements.txt      # Project dependencies (Python 3.10+)
├── .env.example          # Environment variable template with placeholders
└── README.md             # Project documentation and user guide
```

---

## Prerequisites

- **Python**: Version 3.10 or higher.
- **Operating System**: macOS (tested on Apple Silicon & Intel) or Linux.
- **Gemini API Key**: A valid Google Gemini API key from Google AI Studio.

---

## Step-by-Step Installation

### 1. Clone & Navigate to Project

```bash
cd /Users/shazaahmed/Desktop/ai/ecommerce-whatsapp-agent
```

### 2. Create and Activate Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note for macOS users**: `pylibmagic` is included in `requirements.txt` to automatically provide the required `libmagic` C-library binary for Apple Silicon / Intel architecture without needing Homebrew.

### 4. Configure Environment Variables

Copy the template `.env.example` to `.env`:

```bash
cp .env.example .env
```

Open `.env` and fill in your Gemini API key:

```ini
# Google Gemini AI Configuration
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite

# FastAPI E-Commerce Backend (used by tools.py)
ECOMMERCE_API_BASE_URL=http://127.0.0.1:8000
ECOMMERCE_API_TIMEOUT=15

# WhatsApp Bot Session (used by bot.py)
WHATSAPP_SESSION_FILE=session.db
```

---

## Configuration & Gemini Model Note

### Configuration Reference

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | *(Required)* | API key obtained from Google AI Studio |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model identifier used by `agent.py` |
| `ECOMMERCE_API_BASE_URL` | `http://127.0.0.1:8000` | Base URL of the running FastAPI backend |
| `ECOMMERCE_API_TIMEOUT` | `15` | Request timeout in seconds for backend HTTP calls |
| `WHATSAPP_SESSION_FILE` | `session.db` | SQLite file where WhatsApp pairing credentials are saved |

### Gemini Model Note

The original project specification was written around `gemini-2.5-flash`. However, when initializing with newly generated Google API keys, Google's API returns a `404 NOT_FOUND` error because Google no longer provisions `gemini-2.5-flash` to newly registered accounts (this is an upstream Google model-availability and lifecycle deprecation restriction, not an application code defect).

We switched the agent to **`gemini-3.5-flash-lite`**, which has been thoroughly verified across all single-turn composite tool workflows, multi-turn sessions, and Arabic/English bilingual scenarios, and was approved by the mentor. The model is cleanly decoupled and configurable via `GEMINI_MODEL` in `.env`.

---

## Database Seeding & Running the Backend

### 1. Seed the Database

Seed categories and initial products into SQLite:

```bash
cd backend
python seed_data.py
cd ..
```

### 2. Run the FastAPI Backend

```bash
cd backend
uvicorn main:app --reload
```

> **IMPORTANT: Why it must run from `backend/`**:
> Inside `backend/main.py`, imports are written as top-level local imports (e.g. `import models`, `import database`, `import schemas`). Running `uvicorn` from outside `backend/` will fail with `ModuleNotFoundError: No module named 'models'` unless the working directory is set to `backend/`. Starting it from `backend/` ensures `sys.path` correctly resolves the local modules.

The backend starts at `http://127.0.0.1:8000`. You can inspect the Swagger documentation at `http://127.0.0.1:8000/docs`.

---

## Running the Simulator (`simulate_chat.py`)

The simulator provides both an interactive terminal chat and an automated test suite. It includes automatic terminal BiDi/RTL display formatting using `arabic-reshaper` and `python-bidi`.

### 1. Interactive Chat Mode

Start an interactive conversation with a specific phone number session:

```bash
python simulate_chat.py --phone 201000000001
```

**Commands available during interactive chat**:
- `/phone <number>`: Switch session to another customer phone number (demonstrates cart and order isolation).
- `/reset`: Clear chat history for the active session.
- `/exit`: Exit the simulator.
- `--verbose`: Run with verbose logging to observe tool executions in real time.
- `--no-rtl`: Disable Arabic BiDi reshaping if your terminal emulator already handles Arabic RTL rendering.

### 2. Automated Demonstration Suite (`--demo`)

Run the complete 6-scenario automated test suite:

```bash
python simulate_chat.py --demo
```

**What the demo tests**:
1. **Scenario 1 (Composite Query & Checkout)**: Sends a multi-intent Arabic message (`"مساء الخير، شوفيلي الكيبورد الميكانيكال والماوس الجيمينج متاحين ولا لأ؟ وضيفلي واحد من كل نوع للسلة، واعملي تشيك أوت لعنواني في القاهرة - المعادي"`). Verifies an order is placed in ONE turn with 2 items, product stocks drop by 1 each, and the shipping address is stored.
2. **Scenario 2 (Out of Stock Item)**: Requests an item with stock 0 (`Waterproof Bluetooth Speaker`). Verifies the item is rejected and NOT added to the cart.
3. **Scenario 3 (Empty Cart Checkout)**: Attempts checkout with an empty cart. Verifies the backend rejects checkout and creates no order.
4. **Scenario 4 (Track & Cancel Order)**: Cancels the newly created order from Scenario 1, verifies status updates to `cancelled`, and confirms product inventory is restored to baseline.
5. **Scenario 5 (Cart Isolation)**: Tests Phone A and Phone B simultaneously; verifies their carts remain strictly independent.
6. **Scenario 6 (English Language Consistency)**: Sends an English query and asserts the reply is strictly English with zero Arabic characters.

Exit code is `0` if all checks pass, and `1` if any check fails.

---

## Running the Live WhatsApp Bot (`bot.py`)

The production bot integrates directly with WhatsApp using `neonize`.

```bash
python bot.py
```

### Authentication & Pairing
1. On first run, a QR code is generated and displayed directly in your terminal via `segno`.
2. Open WhatsApp on your mobile phone: **Settings > Linked Devices > Link a Device**.
3. Scan the QR code shown in your terminal.
4. The pairing credentials and keys are saved in `session.db` (or the path defined in `WHATSAPP_SESSION_FILE`).
5. **Subsequent runs automatically reconnect without requiring a QR scan.**

### Message Routing & Architecture
- **Non-blocking Execution**: The bot runs incoming message processing inside a `ThreadPoolExecutor` so the `neonize` Go/Python event loop is never blocked by agent reasoning or backend calls.
- **Filtering**: Ignores messages sent by the bot itself (`IsFromMe`), group chats (`g.us`), status/broadcast updates, and non-text messages.
- **Identity Extraction**: Extracts the sender's phone digits from the JID user part while preserving the original chat JID for replying.
- **Stopping**: Press `Control+C` (`Ctrl+C`) to trigger clean shutdown of both the network client and the thread pool.

---

## The 10 Tools Reference Table

The agent has access to 10 typed client functions registered in `tools.py` that map directly to the FastAPI backend:

| Tool Name | Backend Endpoint | Parameters | Description |
| :--- | :--- | :--- | :--- |
| `list_categories` | `GET /categories` | *None* | Retrieves all product categories available in the store. |
| `search_products` | `GET /products` | `keyword` *(Optional[str])*, `category_id` *(Optional[int])* | Searches products by keyword (English substring) or category ID. |
| `get_product_details` | `GET /products/{product_id}` | `product_id` *(int)* | Retrieves details, price, and current stock for a specific product. |
| `get_customer_cart` | `GET /cart/{customer_name}` | `customer_name` *(str)* | Fetches the current cart items, quantities, and subtotal for a session. |
| `add_item_to_cart` | `POST /cart/items` | `customer_name` *(str)*, `product_id` *(int)*, `quantity` *(int=1)* | Adds a product to the customer's cart. |
| `remove_cart_item` | `DELETE /cart/items/{item_id}` | `item_id` *(int)* | Removes an item from the cart by its cart item ID. |
| `clear_customer_cart` | `DELETE /cart/{customer_name}` | `customer_name` *(str)* | Clears all items from the customer's cart. |
| `checkout_cart` | `POST /orders/checkout` | `customer_name` *(str)*, `shipping_address` *(str)* | Checks out the customer's cart, saves shipping address, clears cart, and deducts inventory. |
| `track_order` | `GET /orders/{order_id}` | `order_id` *(int)* | Retrieves status, items, total, and shipping address for an order. |
| `cancel_order` | `PUT /orders/{order_id}/cancel` | `order_id` *(int)* | Cancels a pending/processing order and restores inventory exactly once. |

---

## Multi-Tool Composite Execution Example

A core requirement is completing composite customer requests in **one single turn**.

### Customer Request (Arabic)
> *"مساء الخير، شوفيلي الكيبورد الميكانيكال والماوس الجيمينج متاحين ولا لأ؟ وضيفلي واحد من كل نوع للسلة، واعملي تشيك أوت لعنواني في القاهرة - المعادي"*

### Single-Turn Execution Trace
When this message arrives, `agent.py` loops up to 8 tool rounds within the same turn:

1. **Translates & Searches Keyboard**:
   `search_products(keyword="Keyboard")`
   -> Returns `Mechanical Keyboard` (ID: 1, Price: $89.99, Stock: 12).
2. **Translates & Searches Mouse**:
   `search_products(keyword="Mouse")`
   -> Returns `Wireless Gaming Mouse` (ID: 2, Price: $49.99, Stock: 25).
3. **Adds Keyboard to Cart**:
   `add_item_to_cart(customer_name="201000000001", product_id=1, quantity=1)`
   -> Cart updated.
4. **Adds Mouse to Cart**:
   `add_item_to_cart(customer_name="201000000001", product_id=2, quantity=1)`
   -> Cart updated.
5. **Executes Checkout with Address**:
   `checkout_cart(customer_name="201000000001", shipping_address="القاهرة - المعادي")`
   -> Backend creates Order `#3`, saves `shipping_address="القاهرة - المعادي"`, deducts stock, and clears cart.
6. **Unified Agent Reply**:
   > *"مساء النور، منتجات Mechanical Keyboard و Wireless Gaming Mouse متاحين وتمت إضافتهم للسلة. تم إتمام الطلب بنجاح لعنوانك في القاهرة - المعادي برقم الطلب 3 وبإجمالي 139.98."*

---

## Core Agent Safeguards

### 1. Zero Hallucination
- All product prices, stock quantities, cart totals, and order IDs must be copied directly from tool output.
- The model is strictly instructed never to invent an order ID or claim an out-of-stock item was added.

### 2. Session & Identity Isolation
- All cart management (`get_customer_cart`, `add_item_to_cart`, `clear_customer_cart`, `checkout_cart`) has `customer_name` overridden at runtime by `_execute_tool` using the verified WhatsApp sender phone.
- **WhatsApp LID Support**: WhatsApp sometimes exposes a user's Linked Identity (LID) instead of their international phone number. The agent sanitizes all sender identifiers down to digits and enforces that ownership checks for order tracking and cancellation match the session ID. Customer A cannot track, modify, or cancel Customer B's order.

### 3. Deterministic Language Adaptation
- Conversational LLMs often get biased by previous conversation history (e.g. replying in Arabic to an English message if the previous turns were in Arabic).
- `_session_message` dynamically inspects the **current** message using `_looks_arabic(message)` and prepends an explicit header line: `[reply_language=Arabic]` or `[reply_language=English]`.
- The system prompt instructs the agent: *"Reply ONLY in the language given by reply_language in the latest session line, even if earlier messages in the chat used another language. Never mention that line."*
- Verified across alternating Arabic -> English -> Arabic turns with zero language bleed.

---

## Troubleshooting

### 1. HTTP 429 Quota Exceeded
- **Cause**: Google Gemini API rate limits or quota exhaustion on your API key tier.
- **Solution**: `agent.py` automatically retries with exponential backoff (`1.0s`, `2.0s`, `4.0s`). If errors persist, wait a moment for the per-minute quota to reset or check your quota in the Google AI Studio Console.

### 2. Backend Unreachable (`ConnectionRefusedError`)
- **Cause**: The FastAPI backend is not running.
- **Solution**: Ensure you started the backend server in another terminal tab:
  ```bash
  cd backend && uvicorn main:app --reload
  ```
  Verify reachability at `http://127.0.0.1:8000/categories`.

### 3. WhatsApp QR Code Expired
- **Cause**: WhatsApp QR codes expire after ~20 seconds for security.
- **Solution**: `neonize` will automatically print a refreshed QR code in the terminal. If needed, restart `python bot.py` and scan promptly.

### 4. Arabic Text Appears Reversed in Terminal
- **Cause**: Standard macOS Terminal / iTerm2 does not always natively reorder bidirectional (BiDi) Arabic text.
- **Solution**: `simulate_chat.py` automatically applies `arabic-reshaper` and `python-bidi` for console rendering. If your terminal already supports BiDi rendering natively and text looks doubly-inverted, launch with `--no-rtl`:
  ```bash
  python simulate_chat.py --no-rtl
  ```

### 5. Bot Keeps Replying After Pressing `Command+C`
- **Cause**: On macOS, `Command+C` is the keyboard shortcut for **Copy**. It does not send an interrupt signal to the running Python process.
- **Solution**: Use **`Control+C`** (`Ctrl+C`) to send `SIGINT` to cleanly shut down `bot.py`.

---

## Demo Video Checklist

Use this checklist when recording a live demonstration of the project:

- [ ] **1. Start Backend**:
  - Run `cd backend && uvicorn main:app --reload`.
  - Show the Swagger UI at `http://127.0.0.1:8000/docs` to demonstrate running endpoints.
- [ ] **2. Automated Test Suite**:
  - In a new terminal tab, run `python simulate_chat.py --demo`.
  - Highlight the 6 automated pass checks (composite checkout, out-of-stock rejection, empty cart, cancellation stock restoration, session isolation, English consistency).
- [ ] **3. Interactive Multi-Session & Language Switching**:
  - Run `python simulate_chat.py --phone 201000000001`.
  - Send an Arabic query: *"ايه الفئات المتاحة؟"*.
  - Send an English query: *"Tell me about the Noise Cancelling Headphones"*, showing deterministic English reply.
  - Switch session via `/phone 201000000002` to show cart isolation.
- [ ] **4. Live WhatsApp Integration**:
  - Run `python bot.py`.
  - Show terminal QR code pairing.
  - Send a live WhatsApp message from your phone and observe the agent's real-time response.
  - Stop the bot cleanly using `Control+C`.
