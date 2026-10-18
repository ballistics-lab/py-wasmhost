/* POSIX pieces that wasi-libc lacks and git expects; the functions are in wasi_compat.c (they fail or do nothing). */
#ifndef WASI_COMPAT_H
#define WASI_COMPAT_H
#include <sys/types.h>
#include <time.h>
#include <signal.h>
#include <stdio.h>

#ifndef SA_RESTART
#define SA_RESTART 0x10000000
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#ifndef SIG_SETMASK
#define SIG_BLOCK 0
#define SIG_UNBLOCK 1
#define SIG_SETMASK 2
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#ifndef ITIMER_REAL
#define ITIMER_REAL 0
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#ifndef SIGPIPE
#define SIGPIPE 13
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#ifndef SIGCHLD
#define SIGCHLD 17
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#ifndef SIGALRM
#define SIGALRM 14
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
#endif
#define SYNC_FILE_RANGE_WAIT_BEFORE 1
#define SYNC_FILE_RANGE_WRITE 2
#define SYNC_FILE_RANGE_WAIT_AFTER 4

typedef struct { int si_signo; int si_code; int si_status; pid_t si_pid; } siginfo_t;
struct sigaction {
	union { void (*sa_handler)(int); void (*sa_sigaction)(int, siginfo_t *, void *); };
	sigset_t sa_mask;
	int sa_flags;
};
struct itimerval { struct timeval it_interval, it_value; };
int sigaction(int, const struct sigaction *, struct sigaction *);
int sigprocmask(int, const sigset_t *, sigset_t *);
int setitimer(int, const struct itimerval *, struct itimerval *);
#ifndef SO_KEEPALIVE
#define SO_KEEPALIVE 9
#endif
int kill(pid_t, int);
pid_t fork(void);
pid_t vfork(void);
pid_t waitpid(pid_t, int *, int);
pid_t wait(int *);
int pipe(int[2]);
int dup(int);
int dup2(int, int);
int execv(const char *, char *const[]);
int execvp(const char *, char *const[]);
int execve(const char *, char *const[], char *const[]);
unsigned alarm(unsigned);
pid_t setsid(void);
int execl(const char *, const char *, ...);
int execlp(const char *, const char *, ...);
void flockfile(FILE *);
void funlockfile(FILE *);
int sync_file_range(int, long long, long long, unsigned);
#endif
