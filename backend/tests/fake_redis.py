"""In-memory stand-in for the redis-py calls used by the backend.

Covers strings, sets, MULTI/EXEC pipelines and the compare-and-delete
lock-release script. Set ``fail = True`` to simulate an unreachable Redis.
"""

from __future__ import annotations


class FakePipeline:
    def __init__(self, client: "FakeRedis") -> None:
        self.client = client
        self.operations: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        if name not in {"set", "delete", "sadd", "srem"}:
            raise AttributeError(name)

        def queue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        self.client._check()

        # Apply all queued writes together, like MULTI/EXEC.
        results = [
            getattr(self.client, name)(*args, **kwargs)
            for name, args, kwargs in self.operations
        ]
        self.operations = []
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("Simulated Redis outage")

    def get(self, key):
        self._check()
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._check()

        if nx and key in self.values:
            return False

        self.values[key] = value
        return True

    def delete(self, *keys):
        self._check()
        removed = 0

        for key in keys:
            removed += int(self.values.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)

        return removed

    def sadd(self, key, *values):
        self._check()
        self.sets.setdefault(key, set()).update(values)

    def srem(self, key, *values):
        self._check()
        self.sets.setdefault(key, set()).difference_update(values)

    def smembers(self, key):
        self._check()
        return set(self.sets.get(key, set()))

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def register_script(self, script):
        # Only the lock-release script is used by the catalog code.
        def release(keys, args):
            self._check()

            if self.values.get(keys[0]) == args[0]:
                return self.delete(keys[0])

            return 0

        return release
