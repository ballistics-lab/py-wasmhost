#!/bin/sh
# Builds git.wasm: libgit2 (from the libgit2-sys crate, which carries its sources) plus lg2.c, with `zig cc`
# targeting wasm32-wasi. Needs: python3 with `ziglang` (uv pip install ziglang), cmake, ninja, curl, tar.
#   ZIG="python3 -m ziglang" ./build.sh        # output: ./git.wasm
set -eu
here=$(cd "$(dirname "$0")" && pwd)
work=${WORK:-$here/.build}
ZIG=${ZIG:-python3 -m ziglang}
crate=libgit2-sys-0.18.1+1.9.0
mkdir -p "$work/bin"

# the sources are the ones that were built from, not whatever is served now
sha256() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
verify() { [ "$(sha256 "$1")" = "$2" ] || { echo "build.sh: the checksum of $1 is not $2" >&2; exit 1; }; }
want_zig=0.17.0
have_zig=$($ZIG version 2>/dev/null || true)
[ "$have_zig" = "$want_zig" ] || echo "build.sh: the committed modules were built with zig $want_zig; this is '${have_zig:-not found}'" >&2

if [ ! -d "$work/src/$crate" ]; then
	mkdir -p "$work/src"
	curl -fsSL -o "$work/crate.tgz" "https://static.crates.io/crates/libgit2-sys/$crate.crate"
	verify "$work/crate.tgz" e1dcb20f84ffcdd825c7a311ae347cce604a6f084a767dec4a4929829645290e
	tar xzf "$work/crate.tgz" -C "$work/src"
fi
src=$work/src/$crate/libgit2
cp "$here/stubs/process.c" "$src/src/util/unix/process.c"
cp "$here/stubs/socket.c" "$src/src/libgit2/streams/socket.c"

for tool in cc ar ranlib; do
	args=""; [ $tool = cc ] && args="-target wasm32-wasi -ffile-prefix-map=$here=/lg2 -ffile-prefix-map=$work=/build"
	printf '#!/bin/sh\nexec %s %s %s "$@"\n' "$ZIG" "$tool" "$args" > "$work/bin/z$tool"
	chmod +x "$work/bin/z$tool"
done

flags="-D_WASI_EMULATED_MMAN -D_WASI_EMULATED_SIGNAL -D_WASI_EMULATED_PROCESS_CLOCKS -D_WASI_EMULATED_GETPID -Wno-incompatible-pointer-types"
cmake -S "$src" -B "$work/cmake" -G Ninja -DCMAKE_SYSTEM_NAME=WASI -DCMAKE_SYSTEM_PROCESSOR=wasm32 -DCMAKE_SIZEOF_VOID_P=4 \
	-DCMAKE_C_COMPILER="$work/bin/zcc" -DCMAKE_AR="$work/bin/zar" -DCMAKE_RANLIB="$work/bin/zranlib" \
	-DCMAKE_C_COMPILER_WORKS=1 -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY -DCMAKE_BUILD_TYPE=MinSizeRel \
	-DBUILD_SHARED_LIBS=OFF -DBUILD_TESTS=OFF -DBUILD_CLI=OFF -DBUILD_EXAMPLES=OFF -DUSE_THREADS=OFF \
	-DUSE_HTTPS=OFF -DUSE_SSH=OFF -DUSE_SHA1=CollisionDetection -DUSE_SHA256=builtin -DREGEX_BACKEND=builtin \
	-DUSE_HTTP_PARSER=builtin -DUSE_NTLMCLIENT=OFF -DUSE_GSSAPI=OFF -DUSE_NSEC=OFF -DUSE_ICONV=OFF \
	-DCMAKE_C_FLAGS="$flags" > "$work/cmake.log"
ninja -C "$work/cmake"

"$work/bin/zcc" -Os $flags -I"$src/include" "$here/lg2.c" "$here/compat.c" "$work/cmake/libgit2.a" \
	-lwasi-emulated-mman -lwasi-emulated-signal -lwasi-emulated-process-clocks -lwasi-emulated-getpid \
	-o "$here/git.wasm"
ls -l "$here/git.wasm"
