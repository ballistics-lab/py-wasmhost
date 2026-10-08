import wasmhost


def test_the_jits_are_tried_before_the_interpreters() -> None:
    order = list(wasmhost.AUTO_ORDER)
    assert order[0] == "wasmtime"
    assert order.index("wasm3") > order.index("jsc")  # an interpreter after the JITs
    assert order.index("jscontext") > order.index("wasm3")  # a Mac with rubicon-objc keeps wasmtime
    assert order[-1] == "pyodide"  # the only one that can start inside Pyodide, so nothing is lost by its being last
    assert set(order) == set(wasmhost.BACKENDS)
    assert list(wasmhost.BACKENDS) == order  # the table in the README is in this order


def test_the_javascript_engines_keep_their_relative_order() -> None:
    assert list(wasmhost.JS_AUTO_ORDER) == ["node", "bun", "jsc", "gi-jsc", "jscontext"]
    assert set(wasmhost.JS_AUTO_ORDER) == set(wasmhost.JS_BACKENDS)
