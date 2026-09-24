import wasmhost


def test_the_native_runtimes_are_tried_before_the_javascript_engines() -> None:
    order = list(wasmhost.AUTO_ORDER)
    assert order[:2] == ["wasmtime", "wasm3"]
    assert order.index("jscontext") > order.index("wasm3")  # a Mac with rubicon-objc keeps wasmtime
    assert set(order) == set(wasmhost.BACKENDS)
    assert list(wasmhost.BACKENDS) == order  # the table in the README is in this order


def test_the_javascript_engines_keep_their_relative_order() -> None:
    assert list(wasmhost.JS_AUTO_ORDER) == ["jscontext", "jsc", "gi-jsc", "node"]
    assert set(wasmhost.JS_AUTO_ORDER) == set(wasmhost.JS_BACKENDS)
