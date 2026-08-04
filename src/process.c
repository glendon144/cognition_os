#define _POSIX_C_SOURCE 200809L
#include "process.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static double monotonic_seconds(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0.0;
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1000000000.0;
}

static char *read_capture(FILE *capture)
{
    long length;
    char *buffer;
    if (fflush(capture) != 0 || fseek(capture, 0, SEEK_END) != 0)
        return NULL;
    length = ftell(capture);
    if (length < 0 || length > 1024L * 1024L)
        return NULL;
    if (fseek(capture, 0, SEEK_SET) != 0)
        return NULL;
    buffer = malloc((size_t)length + 1U);
    if (buffer == NULL)
        return NULL;
    if (length != 0 && fread(buffer, 1, (size_t)length, capture) != (size_t)length) {
        free(buffer);
        return NULL;
    }
    buffer[length] = '\0';
    return buffer;
}

int process_run(char *const argv[], unsigned timeout_seconds,
    volatile int *interrupted, struct process_result *result)
{
    FILE *capture;
    int error_pipe[2];
    pid_t pid;
    int status = 0, exec_errno = 0;
    double started;
    struct timespec pause_time = {0, 50000000L};

    memset(result, 0, sizeof(*result));
    result->kind = PROCESS_INTERNAL_ERROR;
    capture = tmpfile();
    if (capture == NULL || pipe(error_pipe) != 0) {
        if (capture != NULL) fclose(capture);
        return -1;
    }
    (void)fcntl(error_pipe[1], F_SETFD, FD_CLOEXEC);
    pid = fork();
    if (pid < 0) {
        close(error_pipe[0]); close(error_pipe[1]); fclose(capture);
        return -1;
    }
    if (pid == 0) {
        int saved;
        (void)setpgid(0, 0);
        close(error_pipe[0]);
        if (dup2(fileno(capture), STDOUT_FILENO) < 0 ||
            dup2(fileno(capture), STDERR_FILENO) < 0)
            _exit(126);
        (void)unsetenv("GIT_DIR");
        (void)unsetenv("GIT_WORK_TREE");
        (void)unsetenv("GIT_INDEX_FILE");
        (void)unsetenv("GIT_OBJECT_DIRECTORY");
        (void)unsetenv("GIT_ALTERNATE_OBJECT_DIRECTORIES");
        (void)unsetenv("GIT_CONFIG_COUNT");
        (void)setenv("GIT_TERMINAL_PROMPT", "0", 1);
        (void)setenv("LC_ALL", "C", 1);
        execvp(argv[0], argv);
        saved = errno;
        (void)write(error_pipe[1], &saved, sizeof(saved));
        _exit(127);
    }
    close(error_pipe[1]);
    (void)setpgid(pid, pid);
    started = monotonic_seconds();
    for (;;) {
        pid_t waited = waitpid(pid, &status, WNOHANG);
        if (waited == pid) break;
        if (waited < 0 && errno != EINTR) break;
        if (interrupted != NULL && *interrupted) {
            (void)kill(-pid, SIGTERM);
            (void)waitpid(pid, &status, 0);
            result->kind = PROCESS_INTERRUPTED;
            break;
        }
        if (timeout_seconds != 0 && monotonic_seconds() - started >= timeout_seconds) {
            (void)kill(-pid, SIGTERM);
            nanosleep(&pause_time, NULL);
            (void)kill(-pid, SIGKILL);
            (void)waitpid(pid, &status, 0);
            result->kind = PROCESS_TIMEOUT;
            break;
        }
        nanosleep(&pause_time, NULL);
    }
    if (read(error_pipe[0], &exec_errno, sizeof(exec_errno)) < 0)
        exec_errno = 0;
    close(error_pipe[0]);
    result->output = read_capture(capture);
    fclose(capture);
    if (result->output == NULL) result->output = strdup("");
    if (result->kind == PROCESS_TIMEOUT || result->kind == PROCESS_INTERRUPTED)
        return 0;
    if (exec_errno != 0) {
        result->code = exec_errno;
        result->kind = exec_errno == ENOENT ? PROCESS_EXEC_NOT_FOUND :
            (exec_errno == EACCES ? PROCESS_EXEC_DENIED : PROCESS_EXEC_ERROR);
    } else if (WIFSIGNALED(status)) {
        result->kind = PROCESS_SIGNALED;
        result->code = WTERMSIG(status);
    } else if (WIFEXITED(status)) {
        result->code = WEXITSTATUS(status);
        result->kind = result->code == 0 ? PROCESS_OK : PROCESS_EXITED;
    }
    return 0;
}

void process_result_free(struct process_result *result)
{
    free(result->output);
    result->output = NULL;
}

const char *process_kind_name(enum process_kind kind)
{
    switch (kind) {
    case PROCESS_OK: return "success";
    case PROCESS_EXITED: return "command failure";
    case PROCESS_EXEC_NOT_FOUND: return "executable not found";
    case PROCESS_EXEC_DENIED: return "permission failure";
    case PROCESS_EXEC_ERROR: return "execution failure";
    case PROCESS_TIMEOUT: return "timeout";
    case PROCESS_SIGNALED: return "signal termination";
    case PROCESS_INTERRUPTED: return "interrupted";
    default: return "internal error";
    }
}
