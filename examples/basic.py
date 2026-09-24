"""A WebAssembly module from bytes: call an export, use its memory, batch the steps.

    python examples/basic.py

The module is `(module (func (export "add") (param i32 i32) (result i32) local.get 0 local.get 1 i32.add)
(memory (export "memory") 1 4))`, written out as bytes so the example needs no compiler.
"""

import wasmhost

ADD = bytes.fromhex(
    "0061736d01000000"  # magic, version
    "01070160027f7f017f"  # types: (i32, i32) -> i32
    "03020100"  # functions: one, of that type
    "050401010104"  # memory: 1 page, at most 4
    "071002036164640000066d656d6f72790200"  # exports: "add" (function 0), "memory" (memory 0)
    "0a09010700200020016a0b"  # code: local.get 0, local.get 1, i32.add
)

module = wasmhost.Module(ADD)
print("exports:", [(e.name, e.kind) for e in wasmhost.Module.exports(module)])

instance = wasmhost.Instance(module)
print("backend:", wasmhost.get_backend().name)
print("add(2, 3) =", instance.exports.add(2, 3))
print("add(2**31 - 1, 1) =", instance.exports.add(2**31 - 1, 1), "(i32 wraps, as in JavaScript)")

memory = instance.exports.memory
memory.write(16, b"hello")
print("memory[16:21] =", memory[16:21], "of", len(memory), "bytes")

# Several steps in one trip to the engine, the later ones using the earlier results.
batch = instance.batch()
total = batch.call(instance.exports.add, 40, 2)
batch.write(memory, total, b"!")  # at offset 42
echo = batch.read(memory, total, 1)
batch.run()
print("batch:", total.value, echo.value)
