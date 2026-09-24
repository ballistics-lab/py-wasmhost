"""Host functions: a module that calls back into Python.

    python examples/imports.py

The module (written out as bytes, so the example needs no compiler) imports four functions from `env` and calls
them from its own exports. The import object is a dict of dicts of callables, as in the JavaScript API; a callable
gets the arguments as ints and floats and returns what the signature says.

    env.plus(i32, i32) -> i32    env.note(i64)    env.half(f64) -> f64    env.pair(i32) -> (i32, i32)
    exports: call_plus, call_note, call_half, call_pair, and twice(a) = plus(plus(a, 1), 1)

On the backends that can (wasmtime, wasm3, jsc, node, and JSContext on iOS; not gi-jsc) it works the same on all.
"""

import wasmhost

MODULE = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "011b0560027f7f017f60017e0060017c017c60017f027f7f60017f017f"  # types
    "022d0403656e7604706c7573000003656e76046e6f7465000103656e760468616c66000203656e76"  # imports: env.plus, env.note,
    "04706169720003"  # ... env.half, env.pair
    "0306050001020304"  # functions: 5
    "0739050963616c6c5f706c757300040963616c6c5f6e6f746500050963616c6c5f68616c66000609"  # exports
    "63616c6c5f7061697200070574776963650008"
    "0a2c0508002000200110000b0600200010010b0600200010020b0600200010030b0c002000410110"  # code
    "00410110000b"
)


log = []


def plus(a, b):
    log.append(f"plus({a}, {b})")
    return a + b


def note(x):
    log.append(f"note({x})")  # no result: returns None


def half(x):
    return x / 2


def pair(x):
    return (x, x + 1)  # several results: a tuple


imports = {"env": {"plus": plus, "note": note, "half": half, "pair": pair}}

instance = wasmhost.Instance(wasmhost.Module(MODULE), imports)
print("backend:", wasmhost.get_backend().name)
print("call_plus(2, 3) =", instance.exports.call_plus(2, 3))
print("twice(10)       =", instance.exports.twice(10), "(two host calls)")
print("call_half(5.0)  =", instance.exports.call_half(5.0))
print("call_pair(7)    =", instance.exports.call_pair(7))
instance.exports.call_note(2**62 + 1)  # an i64 arrives as an exact int
print("what the module asked for:", log)


# An exception in a host function comes out of the call that led to it, and the instance is still good.
def broken(a, b):
    raise ValueError("no thanks")


imports["env"]["plus"] = broken
other = wasmhost.Instance(wasmhost.Module(MODULE), imports)
try:
    other.exports.call_plus(1, 2)
except ValueError as exc:
    print("call_plus raised:", exc, "| call_half still works:", other.exports.call_half(1.0))
