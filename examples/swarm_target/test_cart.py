"""Failing test that triggers the bug in cart.py.

Run with: pytest -xvs examples/swarm_target/test_cart.py

The test is deterministic — two Cart() instances are constructed, the
first has an item added, the second is asserted empty. With the buggy
mutable default the second cart is NOT empty.
"""

from cart import Cart


def test_carts_are_independent():
    c1 = Cart()
    c1.add("apple")
    c2 = Cart()
    assert c2.items == [], (
        f"new cart should be empty, got {c2.items}. "
        f"hint: c1 and c2 share state somehow."
    )


def test_total_uses_own_items():
    prices = {"apple": 1, "banana": 2}
    c1 = Cart()
    c1.add("apple")
    c1.add("banana")
    c2 = Cart()
    c2.add("apple")
    assert c1.total(prices) == 3
    assert c2.total(prices) == 1, (
        f"c2 should only have an apple, got total={c2.total(prices)}"
    )
