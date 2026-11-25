from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from typing import List, Optional

import models
import schemas
from database import engine, get_db

# Create all database tables on startup
models.Base.metadata.create_all(bind=engine)


def _startup_guard():
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "orders" in inspector.get_table_names():
            columns = [c["name"] for c in inspector.get_columns("orders")]
            if "shipping_address" not in columns:
                conn.execute(text("ALTER TABLE orders ADD COLUMN shipping_address VARCHAR"))


_startup_guard()

app = FastAPI(
    title="E-Commerce Backend API",
    description="A complete and clean e-commerce backend with Categories, Products, Cart, and Orders (No Auth).",
    version="1.0.0"
)


@app.on_event("startup")
def on_startup():
    _startup_guard()


# ============================================================================
# 1. CATEGORIES ENDPOINTS
# ============================================================================

@app.post("/categories", response_model=schemas.CategoryResponse, status_code=201, tags=["Categories"])
def create_category(category: schemas.CategoryCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Category).filter(models.Category.name == category.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name already exists")

    new_category = models.Category(name=category.name, description=category.description)
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    return new_category


@app.get("/categories", response_model=List[schemas.CategoryResponse], tags=["Categories"])
def get_categories(db: Session = Depends(get_db)):
    return db.query(models.Category).all()


@app.get("/categories/{category_id}", response_model=schemas.CategoryResponse, tags=["Categories"])
def get_category_by_id(category_id: int, db: Session = Depends(get_db)):
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@app.delete("/categories/{category_id}", tags=["Categories"])
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    db.delete(category)
    db.commit()
    return {"message": f"Category {category_id} deleted successfully"}


# ============================================================================
# 2. PRODUCTS ENDPOINTS
# ============================================================================

@app.post("/products", response_model=schemas.ProductResponse, status_code=201, tags=["Products"])
def create_product(product: schemas.ProductCreate, db: Session = Depends(get_db)):
    # Verify that the category exists
    category = db.query(models.Category).filter(models.Category.id == product.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    new_product = models.Product(
        name=product.name,
        description=product.description,
        price=product.price,
        stock=product.stock,
        category_id=product.category_id
    )
    db.add(new_product)
    db.commit()
    db.refresh(new_product)
    return new_product


@app.get("/products", response_model=List[schemas.ProductResponse], tags=["Products"])
def get_products(
    category_id: Optional[int] = Query(None, description="Filter by category ID"),
    search: Optional[str] = Query(None, description="Search products by name"),
    min_price: Optional[float] = Query(None, description="Minimum price filter"),
    max_price: Optional[float] = Query(None, description="Maximum price filter"),
    in_stock_only: bool = Query(False, description="Filter only in-stock products"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    query = db.query(models.Product)

    if category_id is not None:
        query = query.filter(models.Product.category_id == category_id)
    if search:
        query = query.filter(models.Product.name.ilike(f"%{search}%"))
    if min_price is not None:
        query = query.filter(models.Product.price >= min_price)
    if max_price is not None:
        query = query.filter(models.Product.price <= max_price)
    if in_stock_only:
        query = query.filter(models.Product.stock > 0)

    return query.offset(skip).limit(limit).all()


@app.get("/products/{product_id}", response_model=schemas.ProductResponse, tags=["Products"])
def get_product_by_id(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.put("/products/{product_id}", response_model=schemas.ProductResponse, tags=["Products"])
def update_product(product_id: int, updated: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if updated.category_id is not None:
        category = db.query(models.Category).filter(models.Category.id == updated.category_id).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
        product.category_id = updated.category_id

    if updated.name is not None:
        product.name = updated.name
    if updated.description is not None:
        product.description = updated.description
    if updated.price is not None:
        product.price = updated.price
    if updated.stock is not None:
        product.stock = updated.stock

    db.commit()
    db.refresh(product)
    return product


@app.delete("/products/{product_id}", tags=["Products"])
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    db.delete(product)
    db.commit()
    return {"message": f"Product {product_id} deleted successfully"}


# ============================================================================
# 3. CART ENDPOINTS
# ============================================================================

@app.post("/cart/items", response_model=schemas.CartItemResponse, status_code=201, tags=["Cart"])
def add_item_to_cart(item: schemas.CartItemAdd, db: Session = Depends(get_db)):
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than 0")

    product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    if product.stock < item.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough stock available. Current stock: {product.stock}"
        )

    # Check if item is already in the customer's cart
    existing_item = db.query(models.CartItem).filter(
        models.CartItem.customer_name == item.customer_name,
        models.CartItem.product_id == item.product_id
    ).first()

    if existing_item:
        new_quantity = existing_item.quantity + item.quantity
        if product.stock < new_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot add more. Total in cart ({new_quantity}) exceeds stock ({product.stock})"
            )
        existing_item.quantity = new_quantity
        cart_entry = existing_item
    else:
        cart_entry = models.CartItem(
            customer_name=item.customer_name,
            product_id=item.product_id,
            quantity=item.quantity
        )
        db.add(cart_entry)

    db.commit()
    db.refresh(cart_entry)

    subtotal = round(product.price * cart_entry.quantity, 2)
    return {
        "id": cart_entry.id,
        "customer_name": cart_entry.customer_name,
        "product_id": cart_entry.product_id,
        "product_name": product.name,
        "unit_price": product.price,
        "quantity": cart_entry.quantity,
        "subtotal": subtotal
    }


@app.get("/cart/{customer_name}", response_model=schemas.CartSummaryResponse, tags=["Cart"])
def get_customer_cart(customer_name: str, db: Session = Depends(get_db)):
    cart_items = db.query(models.CartItem).filter(models.CartItem.customer_name == customer_name).all()

    items_data = []
    total_price = 0.0
    total_items = 0

    for item in cart_items:
        subtotal = round(item.product.price * item.quantity, 2)
        total_price += subtotal
        total_items += item.quantity
        items_data.append({
            "id": item.id,
            "customer_name": item.customer_name,
            "product_id": item.product_id,
            "product_name": item.product.name,
            "unit_price": item.product.price,
            "quantity": item.quantity,
            "subtotal": subtotal
        })

    return {
        "customer_name": customer_name,
        "items": items_data,
        "total_items": total_items,
        "total_price": round(total_price, 2)
    }


@app.put("/cart/items/{item_id}", response_model=schemas.CartItemResponse, tags=["Cart"])
def update_cart_item_quantity(item_id: int, updated: schemas.CartItemUpdate, db: Session = Depends(get_db)):
    cart_item = db.query(models.CartItem).filter(models.CartItem.id == item_id).first()
    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    if updated.quantity <= 0:
        # If quantity <= 0, remove the item
        db.delete(cart_item)
        db.commit()
        raise HTTPException(status_code=200, detail="Item removed from cart because quantity was 0")

    if cart_item.product.stock < updated.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Requested quantity exceeds available stock ({cart_item.product.stock})"
        )

    cart_item.quantity = updated.quantity
    db.commit()
    db.refresh(cart_item)

    subtotal = round(cart_item.product.price * cart_item.quantity, 2)
    return {
        "id": cart_item.id,
        "customer_name": cart_item.customer_name,
        "product_id": cart_item.product_id,
        "product_name": cart_item.product.name,
        "unit_price": cart_item.product.price,
        "quantity": cart_item.quantity,
        "subtotal": subtotal
    }


@app.delete("/cart/items/{item_id}", tags=["Cart"])
def remove_item_from_cart(item_id: int, db: Session = Depends(get_db)):
    cart_item = db.query(models.CartItem).filter(models.CartItem.id == item_id).first()
    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    db.delete(cart_item)
    db.commit()
    return {"message": "Item removed from cart"}


@app.delete("/cart/{customer_name}", tags=["Cart"])
@app.delete("/cart/clear/{customer_name}", tags=["Cart"])
def clear_customer_cart(customer_name: str, db: Session = Depends(get_db)):
    db.query(models.CartItem).filter(models.CartItem.customer_name == customer_name).delete()
    db.commit()
    return {"message": f"Cart for {customer_name} cleared successfully"}


# ============================================================================
# 4. ORDERS & CHECKOUT ENDPOINTS
# ============================================================================

@app.post("/checkout", response_model=schemas.OrderResponse, status_code=201, tags=["Orders"])
@app.post("/orders/checkout", response_model=schemas.OrderResponse, status_code=201, tags=["Orders"])
def checkout_cart(checkout_data: schemas.CheckoutRequest, db: Session = Depends(get_db)):
    # 1. Fetch customer's cart items
    cart_items = db.query(models.CartItem).filter(
        models.CartItem.customer_name == checkout_data.customer_name
    ).all()

    if not cart_items:
        raise HTTPException(status_code=400, detail="Cart is empty. Add items to cart before checkout.")

    # 2. Check stock for every product in the cart
    for item in cart_items:
        if item.product.stock < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot checkout: '{item.product.name}' has only {item.product.stock} items left in stock."
            )

    # 3. Create the Order
    total_price = 0.0
    for item in cart_items:
        total_price += item.product.price * item.quantity

    new_order = models.Order(
        customer_name=checkout_data.customer_name,
        customer_phone=checkout_data.customer_phone,
        shipping_address=checkout_data.shipping_address,
        total_price=round(total_price, 2),
        status="pending"
    )
    db.add(new_order)
    db.flush()  # Generate order.id before creating order items

    # 4. Create OrderItems & deduct stock
    order_items_responses = []
    for item in cart_items:
        # Deduct stock permanently
        item.product.stock -= item.quantity

        subtotal = round(item.product.price * item.quantity, 2)
        order_item = models.OrderItem(
            order_id=new_order.id,
            product_id=item.product_id,
            product_name=item.product.name,
            unit_price=item.product.price,
            quantity=item.quantity
        )
        db.add(order_item)
        db.flush()

        order_items_responses.append({
            "id": order_item.id,
            "product_id": order_item.product_id,
            "product_name": order_item.product_name,
            "unit_price": order_item.unit_price,
            "quantity": order_item.quantity,
            "subtotal": subtotal
        })

    # 5. Clear the customer's cart
    db.query(models.CartItem).filter(
        models.CartItem.customer_name == checkout_data.customer_name
    ).delete()

    db.commit()
    db.refresh(new_order)

    return {
        "id": new_order.id,
        "customer_name": new_order.customer_name,
        "customer_phone": new_order.customer_phone,
        "shipping_address": new_order.shipping_address,
        "total_price": new_order.total_price,
        "status": new_order.status,
        "items": order_items_responses
    }


@app.get("/orders", response_model=List[schemas.OrderResponse], tags=["Orders"])
def get_all_orders(db: Session = Depends(get_db)):
    orders = db.query(models.Order).all()
    results = []
    for order in orders:
        items_data = [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "unit_price": item.unit_price,
                "quantity": item.quantity,
                "subtotal": round(item.unit_price * item.quantity, 2)
            }
            for item in order.items
        ]
        results.append({
            "id": order.id,
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "shipping_address": order.shipping_address,
            "total_price": order.total_price,
            "status": order.status,
            "items": items_data
        })
    return results


@app.get("/orders/{order_id}", response_model=schemas.OrderResponse, tags=["Orders"])
def get_order_by_id(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    items_data = [
        {
            "id": item.id,
            "product_id": item.product_id,
            "product_name": item.product_name,
            "unit_price": item.unit_price,
            "quantity": item.quantity,
            "subtotal": round(item.unit_price * item.quantity, 2)
        } for item in order.items
    ]

    return {
        "id": order.id,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "shipping_address": order.shipping_address,
        "total_price": order.total_price,
        "status": order.status,
        "items": items_data
    }

@app.put("/orders/{order_id}/cancel", response_model=schemas.OrderResponse, tags=["Orders"])
def cancel_order_direct(order_id: int, db: Session = Depends(get_db)):
    return update_order_status(order_id, schemas.OrderStatusUpdate(status="cancelled"), db)


@app.patch("/orders/{order_id}/status", response_model=schemas.OrderResponse, tags=["Orders"])
def update_order_status(order_id: int, status_update: schemas.OrderStatusUpdate, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    old_status = order.status
    new_status = status_update.status.lower()

    if new_status == "cancelled":
        if old_status.lower() == "cancelled":
            raise HTTPException(status_code=400, detail="Order is already cancelled")
        if old_status.lower() not in ("pending", "processing"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel order with status '{old_status}'. Only pending or processing orders can be cancelled."
            )
        for item in order.items:
            product = db.query(models.Product).filter(models.Product.id == item.product_id).first()
            if product:
                product.stock += item.quantity

    order.status = new_status
    db.commit()
    db.refresh(order)

    items_data = [
        {
            "id": item.id,
            "product_id": item.product_id,
            "product_name": item.product_name,
            "unit_price": item.unit_price,
            "quantity": item.quantity,
            "subtotal": round(item.unit_price * item.quantity, 2)
        }
        for item in order.items
    ]
    return {
        "id": order.id,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "shipping_address": order.shipping_address,
        "total_price": order.total_price,
        "status": order.status,
        "items": items_data
    }
