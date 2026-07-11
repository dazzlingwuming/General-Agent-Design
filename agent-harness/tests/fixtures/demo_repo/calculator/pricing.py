from calculator.discounts import apply_discount


def calculate_subtotal(items):
    return sum(item["price"] * item.get("quantity", 1) for item in items)


def calculate_total(items, discount_rate=0.0, already_discounted=False):
    subtotal = calculate_subtotal(items)
    if discount_rate and not already_discounted:
        return apply_discount(subtotal, discount_rate)
    return subtotal


def quote_order(order):
    total = calculate_total(order["items"], order.get("discount_rate", 0.0))
    if order.get("vip"):
        total = apply_discount(total, 0.05)
    return total

