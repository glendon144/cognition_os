#ifndef COGBOOT_PROCESS_H
#define COGBOOT_PROCESS_H

#include <stddef.h>

enum process_kind {
    PROCESS_OK,
    PROCESS_EXITED,
    PROCESS_EXEC_NOT_FOUND,
    PROCESS_EXEC_DENIED,
    PROCESS_EXEC_ERROR,
    PROCESS_TIMEOUT,
    PROCESS_SIGNALED,
    PROCESS_INTERRUPTED,
    PROCESS_INTERNAL_ERROR
};

struct process_result {
    enum process_kind kind;
    int code;
    char *output;
};

int process_run(char *const argv[], unsigned timeout_seconds,
    volatile int *interrupted, struct process_result *result);
void process_result_free(struct process_result *result);
const char *process_kind_name(enum process_kind kind);

#endif
