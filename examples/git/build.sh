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

if [ ! -d "$work/src/$crate" ]; then
	mkdir -p "$work/src"
	curl -fsSL -o "$work/crate.tgz" "https://static.crates.io/crates/libgit2-sys/$crate.crate"
	tar xzf "$work/crate.tgz" -C "$work/src"
fi
src=$work/src/$crate/libgit2
cp "$here/stubs/process.c" "$src/src/util/unix/process.c"
cp "$here/stubs/socket.c" "$src/src/libgit2/streams/socket.c"

for tool in cc ar ranlib; do
	args=""; [ $tool = cc ] && args="-target wasm32-wasi"
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
