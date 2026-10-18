#!/bin/sh
# PoC: the real git (git.git, not libgit2) built to WASI with `zig cc`. Writes ../git-real.wasm.
#   ZIG="python3 -m ziglang" ./build.sh        # needs make, curl, tar, xz; about five minutes
# The network does not work in this build (see README.md): no libcurl, and no processes for a remote helper.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
work=${WORK:-$here/.build}
ZIG=${ZIG:-python3 -m ziglang}
version=2.56.0
mkdir -p "$work/bin"

# the sources are the ones that were built from, not whatever is served now
sha256() { if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
verify() { [ "$(sha256 "$1")" = "$2" ] || { echo "build.sh: the checksum of $1 is not $2" >&2; exit 1; }; }
want_zig=0.17.0
have_zig=$($ZIG version 2>/dev/null || true)
[ "$have_zig" = "$want_zig" ] || echo "build.sh: the committed modules were built with zig $want_zig; this is '${have_zig:-not found}'" >&2

for tool in cc ar ranlib; do
	args=""; [ $tool = cc ] && args="-target wasm32-wasi -ffile-prefix-map=$here=/lg2 -ffile-prefix-map=$work=/build"
	printf '#!/bin/sh\nexec %s %s %s "$@"\n' "$ZIG" "$tool" "$args" > "$work/bin/z$tool"
	chmod +x "$work/bin/z$tool"
done

if [ ! -d "$work/git-$version" ]; then
	curl -fsSL -o "$work/git.tar.xz" "https://mirrors.edge.kernel.org/pub/software/scm/git/git-$version.tar.xz"
	verify "$work/git.tar.xz" 26c56c296b38c0695b26fa95f475f1d01704d2d38e73465ca30b0b2f5dc789d3
	tar xf "$work/git.tar.xz" -C "$work"
fi
src=$work/git-$version

# zlib: the copy that the libgit2-sys crate carries (the same crate that build.sh of the parent directory uses)
if [ ! -f "$work/zlib/libz.a" ]; then
	crate=libgit2-sys-0.18.1+1.9.0
	curl -fsSL -o "$work/crate.tgz" "https://static.crates.io/crates/libgit2-sys/$crate.crate"
	verify "$work/crate.tgz" e1dcb20f84ffcdd825c7a311ae347cce604a6f084a767dec4a4929829645290e
	tar xzf "$work/crate.tgz" -C "$work" "$crate/libgit2/deps/zlib"
	mkdir -p "$work/zlib"
	cp "$work/$crate/libgit2/deps/zlib/zlib.h" "$work/$crate/libgit2/deps/zlib/zconf.h" "$work/zlib/"
	for f in "$work/$crate"/libgit2/deps/zlib/*.c; do
		"$work/bin/zcc" -Os -DHAVE_UNISTD_H -c "$f" -o "$work/zlib/$(basename "$f" .c).o"
	done
	"$work/bin/zar" rcs "$work/zlib/libz.a" "$work"/zlib/*.o
fi

"$work/bin/zcc" -Os -D_WASI_EMULATED_SIGNAL -c "$here/wasi_compat.c" -I"$here" -o "$work/wasi_compat.o"

# git opens /dev/null at start, which WASI does not have
if ! grep -q __wasi__ "$src/setup.c"; then
	sed -i '/^void sanitize_stdfds(void)$/,/^{$/{s|^{$|{\n#ifdef __wasi__\n\treturn; /* no /dev/null in WASI */\n#endif|}' "$src/setup.c"
fi

# merge saves the dirty state with a child `git stash create`, to restore it if the merge fails; with no child, it saves nothing
if ! grep -q __wasi__ "$src/builtin/merge.c"; then
	sed -i '/^static int save_state(struct object_id \*stash)$/,/^{$/{s|^{$|{\n#ifdef __wasi__\n\treturn -1; /* no processes: nothing is saved */\n#endif|}' "$src/builtin/merge.c"
fi

cat > "$src/config.mak" <<EOF
CC = $work/bin/zcc
AR = $work/bin/zar
RANLIB = $work/bin/zranlib
uname_S = WASI
uname_M = wasm32
uname_O = WASI
NO_CURL = 1
NO_EXPAT = 1
NO_OPENSSL = 1
NO_GETTEXT = 1
NO_PERL = 1
NO_PYTHON = 1
NO_TCLTK = 1
NO_ICONV = 1
NO_PTHREADS = 1
NO_UNIX_SOCKETS = 1
NO_REGEX = 1
NO_SYS_POLL_H = 1
NO_INSTALL_HARDLINKS = 1
NO_MMAP = 1
NO_POSIX_GOODIES = 1
NO_RUST = 1
HAVE_SYSINFO =
NO_UNCOMPRESS2 =
SKIP_DASHED_BUILT_INS = 1
FSMONITOR_DAEMON_BACKEND =
FSMONITOR_OS_SETTINGS =
CFLAGS = -Os -include $here/wasi_compat.h -Dchmod=git_wasi_chmod -Dfchmod=git_wasi_fchmod -D_WASI_EMULATED_MMAN -D_WASI_EMULATED_SIGNAL -D_WASI_EMULATED_PROCESS_CLOCKS -D_WASI_EMULATED_GETPID -I$work/zlib -Wno-incompatible-pointer-types -Wno-implicit-function-declaration
LDFLAGS = -L$work/zlib
EXTLIBS = $work/wasi_compat.o -lwasi-emulated-mman -lwasi-emulated-signal -lwasi-emulated-process-clocks -lwasi-emulated-getpid
EOF
make -C "$src" -j"$(nproc)" git
cp "$src/git" "$here/../git-real.wasm"
ls -l "$here/../git-real.wasm"
