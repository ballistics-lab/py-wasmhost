"""`compile` and `instantiate`, awaited: the JavaScript API's promises, with no races between tasks."""

from __future__ import annotations

import asyncio

import wasm_builder as wb

import wasmhost


def test_compile_and_instantiate(session: str) -> None:
    async def main() -> tuple[int, int, bool]:
        module = await wasmhost.compile(wb.arith())
        instance = await wasmhost.instantiate(module)
        assert isinstance(instance, wasmhost.Instance)
        both = await wasmhost.instantiate(wb.arith())
        assert isinstance(both, wasmhost.Instantiated)
        return instance.exports.add(1, 2), both.instance.exports.add(3, 4), isinstance(both.module, wasmhost.Module)

    assert asyncio.run(main()) == (3, 7, True)


def test_many_tasks_at_once_do_not_race(session: str) -> None:
    async def one(n: int) -> int:
        inst = await wasmhost.instantiate(wasmhost.Module(wb.arith()))
        assert isinstance(inst, wasmhost.Instance)
        return int(inst.exports.add(n, n))

    async def main() -> list[int]:
        return list(await asyncio.gather(*(one(n) for n in range(12))))

    assert asyncio.run(main()) == [2 * n for n in range(12)]


def test_a_bad_module_is_an_error_of_the_await(session: str) -> None:
    async def main() -> None:
        try:
            await wasmhost.compile(b"not wasm")
        except wasmhost.CompileError:
            return
        raise AssertionError("no CompileError")

    asyncio.run(main())


def test_the_loop_keeps_running_while_a_module_is_made(session: str) -> None:
    ticks: list[int] = []

    async def ticker() -> None:
        for i in range(5):
            ticks.append(i)
            await asyncio.sleep(0)

    async def main() -> None:
        await asyncio.gather(ticker(), wasmhost.compile(wb.arith()))

    asyncio.run(main())
    assert ticks == [0, 1, 2, 3, 4]
