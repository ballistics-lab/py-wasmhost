/* What wasi-libc does not have and git links against: processes fail, signals do nothing. */
#include <errno.h>
#include <sys/types.h>
#include <signal.h>
#include <time.h>

#include "wasi_compat.h"

#define NOSYS do { errno = ENOSYS; return -1; } while (0)

int kill(pid_t p, int s) { (void)p; (void)s; NOSYS; }
pid_t fork(void) { NOSYS; }
pid_t vfork(void) { NOSYS; }
pid_t waitpid(pid_t p, int *st, int o) { (void)p; (void)st; (void)o; NOSYS; }
pid_t wait(int *st) { (void)st; NOSYS; }
int pipe(int fds[2]) { (void)fds; NOSYS; }
int dup(int fd) { (void)fd; NOSYS; }
int dup2(int a, int b) { (void)a; (void)b; NOSYS; }
int execv(const char *p, char *const a[]) { (void)p; (void)a; NOSYS; }
int execvp(const char *p, char *const a[]) { (void)p; (void)a; NOSYS; }
int execve(const char *p, char *const a[], char *const e[]) { (void)p; (void)a; (void)e; NOSYS; }
unsigned alarm(unsigned s) { (void)s; return 0; }
pid_t setsid(void) { NOSYS; }
int sigaction(int s, const struct sigaction *a, struct sigaction *o) { (void)s; (void)a; (void)o; return 0; }
int sigprocmask(int h, const sigset_t *s, sigset_t *o) { (void)h; (void)s; (void)o; return 0; }
int setitimer(int w, const struct itimerval *n, struct itimerval *o) { (void)w; (void)n; (void)o; return 0; }
uid_t getuid(void) { return 0; }
uid_t geteuid(void) { return 0; }
gid_t getgid(void) { return 0; }
pid_t getppid(void) { return 1; }
pid_t getpgid(pid_t p) { (void)p; return 1; }
pid_t getsid(pid_t p) { (void)p; return 1; }

#include <fcntl.h>
#include <netdb.h>
#include <pwd.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <termios.h>
#include <unistd.h>

int sigemptyset(sigset_t *s) { memset(s, 0, sizeof *s); return 0; }
int sigfillset(sigset_t *s) { memset(s, 0xff, sizeof *s); return 0; }
int sigaddset(sigset_t *s, int n) { (void)s; (void)n; return 0; }
int tcgetattr(int fd, struct termios *t) { (void)fd; (void)t; errno = ENOTTY; return -1; }
int tcsetattr(int fd, int a, const struct termios *t) { (void)fd; (void)a; (void)t; errno = ENOTTY; return -1; }
pid_t tcgetpgrp(int fd) { (void)fd; errno = ENOTTY; return -1; }
int execl(const char *p, const char *a, ...) { (void)p; (void)a; NOSYS; }
int execlp(const char *p, const char *a, ...) { (void)p; (void)a; NOSYS; }
struct passwd *getpwuid(uid_t u) { (void)u; return 0; }
struct passwd *getpwnam(const char *n) { (void)n; return 0; }
ssize_t getrandom(void *b, size_t n, unsigned f)
{
	size_t done = 0;
	(void)f;
	while (done < n) {
		size_t c = n - done > 256 ? 256 : n - done;
		if (getentropy((char *)b + done, c)) return -1;
		done += c;
	}
	return (ssize_t)n;
}
int getaddrinfo(const char *h, const char *s, const struct addrinfo *hi, struct addrinfo **r) { (void)h; (void)s; (void)hi; (void)r; return EAI_FAIL; }
void freeaddrinfo(struct addrinfo *a) { (void)a; }
int getnameinfo(const struct sockaddr *a, socklen_t l, char *h, socklen_t hl, char *s, socklen_t sl, int f)
{ (void)a; (void)l; (void)h; (void)hl; (void)s; (void)sl; (void)f; return EAI_FAIL; }
const char *gai_strerror(int e) { (void)e; return "name resolution is not available"; }
int socket(int d, int t, int p) { (void)d; (void)t; (void)p; errno = ENOSYS; return -1; }
int connect(int fd, const struct sockaddr *a, socklen_t l) { (void)fd; (void)a; (void)l; errno = ENOSYS; return -1; }
int setsockopt(int fd, int lv, int n, const void *v, socklen_t l) { (void)fd; (void)lv; (void)n; (void)v; (void)l; errno = ENOSYS; return -1; }
void flockfile(FILE *f) { (void)f; }
void funlockfile(FILE *f) { (void)f; }
mode_t umask(mode_t m) { (void)m; return 022; }
int sync_file_range(int fd, long long o, long long n, unsigned f) { (void)fd; (void)o; (void)n; (void)f; return 0; }
int gethostname(char *n, size_t l) { snprintf(n, l, "wasm"); return 0; }

static void suffix(char *p)
{
	static const char c[] = "abcdefghijklmnopqrstuvwxyz0123456789";
	unsigned char r[6];
	size_t l = strlen(p), i;
	getentropy(r, sizeof r);
	for (i = 0; i < 6; i++) p[l - 6 + i] = c[r[i] % (sizeof c - 1)];
}
int mkstemp(char *t)
{
	int i;
	for (i = 0; i < 100; i++) {
		int fd;
		suffix(t);
		fd = open(t, O_RDWR | O_CREAT | O_EXCL, 0600);
		if (fd >= 0 || errno != EEXIST) return fd;
	}
	return -1;
}
char *mkdtemp(char *t)
{
	int i;
	for (i = 0; i < 100; i++) {
		suffix(t);
		if (!mkdir(t, 0700)) return t;
		if (errno != EEXIST) return 0;
	}
	return 0;
}

/* git is built with chmod and fchmod renamed to these: WASI has no file modes, and a failed chmod is fatal to git. */
int git_wasi_chmod(const char *p, mode_t m) { (void)p; (void)m; return 0; }
int git_wasi_fchmod(int fd, mode_t m) { (void)fd; (void)m; return 0; }
