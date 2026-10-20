#!/bin/sh
# Builds python.wasm and lib/python315.zip: the official CPython (python/cpython, branch 3.15) for wasm32-wasip1, with
# the build that CPython itself ships (Platforms/WASI) and the wasi-sdk it asks for. Needs: git, curl, tar, make, a C
# compiler and python3 (for the build Python that the cross build needs), llvm-strip or strip optional.
#   ./build.sh            # output: ./python.wasm and ./lib/python315.zip
# Nothing has to run WebAssembly while it builds, so there is no wasmtime step. Sources and the SDK go to $WORK
# (default ./.build), which is not committed.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
work=${WORK:-$here/.build}
cpython_commit=1c6794eb7d0f015c38b2fa7868912c4859f1293c # 3.15.0+dev of 2026-10-20
sdk=33
mkdir -p "$work"

sha256() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

case "$(uname -s)-$(uname -m)" in
	Linux-x86_64) arch=x86_64-linux; want=0ba8b5bfaeb2adf3f29bab5841d76cf5318ab8e1642ea195f88baba1abd47bce ;;
	Linux-aarch64) arch=arm64-linux; want=4f98ee738c7abb45c81a94d1461fc53cc569d1cd01498951c8184d841a027844 ;;
	Darwin-arm64) arch=arm64-macos; want=85c997a2665ead91673b5bb88b7d0df3fc8900df3bfa244f720d478187bbdc78 ;;
	Darwin-x86_64) arch=x86_64-macos; want=18f3f201ba9734e6a4455b0b6410690395a55e9ffa9f6f5066f66083a94b93b3 ;;
	*) echo "build.sh: no wasi-sdk for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

if [ ! -d "$work/wasi-sdk-$sdk.0-$arch" ]; then
	curl -fsSL -o "$work/sdk.tgz" "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-$sdk/wasi-sdk-$sdk.0-$arch.tar.gz"
	[ "$(sha256 "$work/sdk.tgz")" = "$want" ] || { echo "build.sh: the checksum of the wasi-sdk is not $want" >&2; exit 1; }
	tar xzf "$work/sdk.tgz" -C "$work"
fi

src=$work/cpython
if [ ! -d "$src/.git" ]; then
	mkdir -p "$src"
	git -C "$src" init -q
	git -C "$src" remote add origin https://github.com/python/cpython
fi
git -C "$src" fetch -q --depth 1 origin "$cpython_commit"
git -C "$src" checkout -q --detach FETCH_HEAD

export WASI_SDK_PATH=$work/wasi-sdk-$sdk.0-$arch
mkdir -p "$work/logs"
(cd "$src" && python3 Platforms/WASI build --quiet --logdir "$work/logs" --host-runner true)

cross=$src/cross-build/wasm32-wasip1
if command -v llvm-strip > /dev/null; then strip=llvm-strip; else strip="$WASI_SDK_PATH/bin/llvm-strip"; fi
$strip -o "$here/python.wasm" "$cross/python.wasm" # the debug information is most of it
mkdir -p "$here/lib"
"$src/cross-build/$("$src/config.guess")/python" "$here/mkstdlib.py" "$src/Lib" "$cross/build/lib.wasi-wasm32-3.15" "$here/lib/python315.zip"
ls -l "$here/python.wasm" "$here/lib/python315.zip"
