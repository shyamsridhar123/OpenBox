"""Tiny shopping-cart module with a bug.

This is the target of the hypothesis-swarm demo. The orchestrator hands
this file + the failing test below to Kimi, asks for N candidate
fixes, fans each one out to its own Kata sandbox, and races them.

If you're reading this file: the bug is intentional. Don't fix it.
"""


class Cart:
    def __init__(self, items=[]):
        # The classic Python mutable-default-argument footgun. `items=[]`
        # is evaluated ONCE at function-definition time. Every Cart() with
        # no argument shares the same list. Adding to one mutates all of
        # them. The unit test below fails the second time it runs in
        # the same process.
        self.items = items

    def add(self, item):
        self.items.append(item)
        return self.items

    def total(self, prices):
        return sum(prices[i] for i in self.items)
