/* WASI has no processes: libgit2's process API (ssh and filter commands) is replaced by one that always fails. */
#include <errno.h>
#include <git2.h>

#include "git2_util.h"
#include "process.h"

#define NO_PROCESS (git_error_set(GIT_ERROR_OS, "processes are not supported on WASI"), -1)

int git_process_new(git_process **out, const char **argv, size_t argv_len, const char **env, size_t env_len,
                    git_process_options *opts)
{
	(void)out; (void)argv; (void)argv_len; (void)env; (void)env_len; (void)opts;
	return NO_PROCESS;
}

int git_process_new_from_cmdline(git_process **out, const char *cmdline, const char **env, size_t env_len,
                                 git_process_options *opts)
{
	(void)out; (void)cmdline; (void)env; (void)env_len; (void)opts;
	return NO_PROCESS;
}

int git_process_start(git_process *process) { (void)process; return NO_PROCESS; }
int git_process_id(p_pid_t *out, git_process *process) { (void)out; (void)process; return NO_PROCESS; }
ssize_t git_process_read(git_process *process, void *buf, size_t count) { (void)process; (void)buf; (void)count; return NO_PROCESS; }
ssize_t git_process_read_err(git_process *process, void *buf, size_t count) { (void)process; (void)buf; (void)count; return NO_PROCESS; }
ssize_t git_process_write(git_process *process, const void *buf, size_t count) { (void)process; (void)buf; (void)count; return NO_PROCESS; }
int git_process_wait(git_process_result *result, git_process *process) { (void)result; (void)process; return NO_PROCESS; }
int git_process_close_in(git_process *process) { (void)process; return 0; }
int git_process_close_out(git_process *process) { (void)process; return 0; }
int git_process_close_err(git_process *process) { (void)process; return 0; }
int git_process_close(git_process *process) { (void)process; return 0; }
int git_process_result_msg(git_str *msg, git_process_result *result) { (void)msg; (void)result; return NO_PROCESS; }
void git_process_free(git_process *process) { (void)process; }
