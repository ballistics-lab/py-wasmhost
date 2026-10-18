/* POSIX identity calls that wasi-libc does not have; libgit2 calls them (permissions checks, a random seed). */
#include <sys/types.h>

uid_t getuid(void) { return 1000; }
uid_t geteuid(void) { return 1000; }
gid_t getgid(void) { return 1000; }
pid_t getppid(void) { return 1; }
pid_t getpgid(pid_t pid) { (void)pid; return 1; }
pid_t getsid(pid_t pid) { (void)pid; return 1; }

/* There is no user database: no home directory is found from it (HOME is used when it is set). */
int getpwuid_r(uid_t uid, void *pwd, char *buf, unsigned long buflen, void **result)
{
	(void)uid; (void)pwd; (void)buf; (void)buflen;
	*result = 0;
	return 0;
}
