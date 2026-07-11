from calculator.pricing import calculate_total


def test_calculate_total_applies_discount():
    assert calculate_total([{"price": 100, "quantity": 2}], discount_rate=0.1) == 180

