#define _POSIX_C_SOURCE 200809L
#include "process.h"

#include <errno.h>
#include <getopt.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#define COGBOOT_VERSION "0.1.0"
#define MAX_RECORDS 256
#define MAX_FIELD 4096

enum outcome { OUT_SYNCED, OUT_CURRENT, OUT_CLONED, OUT_SKIPPED, OUT_DEGRADED, OUT_FAILED };

struct repo {
    bool required;
    char *name, *checkout, *url, *branch;
    enum outcome outcome;
    char detail[256];
};

struct config {
    const char *manifest, *source_root, *git;
    unsigned retries, retry_delay, timeout;
    bool dry_run, status_only;
};

static volatile sig_atomic_t interrupted;

static void on_signal(int signal_number) { interrupted = signal_number; }

static void usage(FILE *stream)
{
    fprintf(stream,
        "usage: cogboot [options]\n"
        "  -m, --manifest PATH       repository manifest\n"
        "  -r, --source-root PATH    checkout root (required)\n"
        "  -g, --git PATH            Git executable (default: git)\n"
        "      --retry-count N       retries after first failure (default: 5)\n"
        "      --retry-delay N       seconds between retries (default: 5)\n"
        "      --timeout N           command timeout seconds (default: 300)\n"
        "  -n, --dry-run             report intended work; make no changes\n"
        "  -s, --status              inspect local state; make no changes\n"
        "  -h, --help                show help\n"
        "  -V, --version             show version\n");
}

static void diagnostic(const char *level, const char *format, ...)
{
    va_list args;
    FILE *stream = strcmp(level, "INFO") == 0 ? stdout : stderr;
    fprintf(stream, "cogboot: %s: ", level);
    va_start(args, format); vfprintf(stream, format, args); va_end(args);
    fputc('\n', stream);
}

static bool parse_unsigned(const char *text, unsigned limit, unsigned *value)
{
    char *end;
    unsigned long parsed;
    if (*text == '\0' || *text == '-') return false;
    errno = 0; parsed = strtoul(text, &end, 10);
    if (errno != 0 || *end != '\0' || parsed > limit) return false;
    *value = (unsigned)parsed;
    return true;
}

static bool safe_checkout(const char *path)
{
    const char *part = path;
    if (*path == '\0' || *path == '/' || strlen(path) >= PATH_MAX) return false;
    while (*part != '\0') {
        const char *slash = strchr(part, '/');
        size_t length = slash == NULL ? strlen(part) : (size_t)(slash - part);
        if (length == 0 || (length == 1 && part[0] == '.') ||
            (length == 2 && part[0] == '.' && part[1] == '.')) return false;
        part = slash == NULL ? part + length : slash + 1;
    }
    return true;
}

static void free_repos(struct repo *repos, size_t count)
{
    size_t i;
    for (i = 0; i < count; i++) {
        free(repos[i].name); free(repos[i].checkout);
        free(repos[i].url); free(repos[i].branch);
    }
    free(repos);
}

static int parse_manifest(const char *path, struct repo **out, size_t *out_count)
{
    FILE *file = fopen(path, "r");
    struct repo *repos = calloc(MAX_RECORDS, sizeof(*repos));
    char line[MAX_FIELD * 4 + 16];
    size_t count = 0, line_number = 0;
    int errors = 0;
    if (file == NULL) {
        diagnostic("ERROR", "cannot open manifest %s: %s", path, strerror(errno));
        free(repos); return -1;
    }
    if (repos == NULL) { fclose(file); return -1; }
    while (fgets(line, sizeof(line), file) != NULL) {
        char *fields[5], *cursor, *newline;
        size_t field, i;
        line_number++;
        newline = strchr(line, '\n');
        if (newline == NULL && !feof(file)) {
            diagnostic("ERROR", "%s:%lu: record is too long", path, (unsigned long)line_number);
            errors++; while (fgets(line, sizeof(line), file) != NULL && strchr(line, '\n') == NULL) {}
            continue;
        }
        if (newline != NULL) *newline = '\0';
        if (line[0] == '\0' || line[0] == '#') continue;
        cursor = line;
        for (field = 0; field < 5; field++) {
            char *tab;
            fields[field] = cursor;
            tab = strchr(cursor, '\t');
            if (field < 4) {
                if (tab == NULL) break;
                *tab = '\0'; cursor = tab + 1;
            } else if (tab != NULL) break;
        }
        if (field != 5) {
            diagnostic("ERROR", "%s:%lu: expected exactly five TAB-separated fields", path, (unsigned long)line_number);
            errors++; continue;
        }
        for (i = 0; i < 5; i++) {
            if (fields[i][0] == '\0' || strlen(fields[i]) > MAX_FIELD ||
                strchr(fields[i], '\r') != NULL || strchr(fields[i], '\n') != NULL) break;
        }
        if (i != 5 || (strcmp(fields[0], "required") != 0 && strcmp(fields[0], "optional") != 0) ||
            !safe_checkout(fields[2])) {
            diagnostic("ERROR", "%s:%lu: invalid requirement, empty/long field, or unsafe checkout", path, (unsigned long)line_number);
            errors++; continue;
        }
        for (i = 0; i < count; i++) {
            if (strcmp(repos[i].name, fields[1]) == 0 || strcmp(repos[i].checkout, fields[2]) == 0) break;
        }
        if (i != count) {
            diagnostic("ERROR", "%s:%lu: duplicate repository name or checkout", path, (unsigned long)line_number);
            errors++; continue;
        }
        if (count == MAX_RECORDS) { diagnostic("ERROR", "manifest exceeds %d records", MAX_RECORDS); errors++; break; }
        repos[count].required = strcmp(fields[0], "required") == 0;
        repos[count].name = strdup(fields[1]); repos[count].checkout = strdup(fields[2]);
        repos[count].url = strdup(fields[3]); repos[count].branch = strdup(fields[4]);
        if (repos[count].name == NULL || repos[count].checkout == NULL || repos[count].url == NULL || repos[count].branch == NULL) {
            errors++; break;
        }
        count++;
    }
    if (ferror(file)) { diagnostic("ERROR", "failed reading manifest %s", path); errors++; }
    fclose(file);
    if (count == 0) { diagnostic("ERROR", "manifest contains no repository records"); errors++; }
    if (errors != 0) { free_repos(repos, count); return -1; }
    *out = repos; *out_count = count; return 0;
}

static int mkdir_safe(const char *path)
{
    char copy[PATH_MAX], *p;
    struct stat st;
    if (strlen(path) >= sizeof(copy)) return -1;
    strcpy(copy, path);
    for (p = copy + 1; ; p++) {
        if (*p == '/' || *p == '\0') {
            char saved = *p; *p = '\0';
            if ((saved == '\0' ? lstat(copy, &st) : stat(copy, &st)) != 0) {
                if (errno != ENOENT || mkdir(copy, 0755) != 0) return -1;
            } else if (!S_ISDIR(st.st_mode) || (saved == '\0' && S_ISLNK(st.st_mode))) {
                errno = ELOOP; return -1;
            }
            *p = saved;
            if (saved == '\0') break;
        }
    }
    return 0;
}

static bool path_state(const char *path, bool *exists, bool *directory)
{
    struct stat st;
    if (lstat(path, &st) != 0) {
        if (errno == ENOENT) { *exists = false; *directory = false; return true; }
        return false;
    }
    *exists = true; *directory = S_ISDIR(st.st_mode) && !S_ISLNK(st.st_mode);
    return true;
}

static int command(const struct config *cfg, struct process_result *result, char *const argv[])
{
    if (process_run(argv, cfg->timeout, (volatile int *)&interrupted, result) != 0) {
        diagnostic("ERROR", "cannot execute process layer: %s", strerror(errno)); return -1;
    }
    return result->kind == PROCESS_OK ? 0 : 1;
}

static void trim(char *text)
{
    size_t length = strlen(text);
    while (length > 0 && (text[length - 1] == '\n' || text[length - 1] == '\r' || text[length - 1] == ' ')) text[--length] = '\0';
}

static bool git_ok(const struct config *cfg, const char *target, struct process_result *r,
    const char *a, const char *b, const char *c)
{
    char *argv[9]; size_t n = 0;
    argv[n++] = (char *)cfg->git;
    if (target != NULL) { argv[n++] = "-C"; argv[n++] = (char *)target; }
    argv[n++] = (char *)a;
    if (b != NULL) argv[n++] = (char *)b;
    if (c != NULL) argv[n++] = (char *)c;
    argv[n] = NULL;
    if (command(cfg, r, argv) == 0) return true;
    diagnostic("WARN", "Git %s failed: %s", a, process_kind_name(r->kind));
    return false;
}

static void set_outcome(struct repo *repo, enum outcome outcome, const char *detail)
{
    repo->outcome = outcome;
    snprintf(repo->detail, sizeof(repo->detail), "%s", detail);
}

static bool local_relation(const struct config *cfg, struct repo *repo, const char *target,
    const char *remote_ref, enum outcome success_outcome)
{
    struct process_result r = {0};
    char *argv_equal[] = {(char *)cfg->git, "-C", (char *)target, "rev-parse", "HEAD", (char *)remote_ref, NULL};
    char *first, *second;
    if (command(cfg, &r, argv_equal) != 0) {
        set_outcome(repo, OUT_DEGRADED, "remote-tracking branch is unavailable"); process_result_free(&r); return false;
    }
    trim(r.output); first = r.output; second = strchr(first, '\n');
    if (second == NULL) { set_outcome(repo, OUT_FAILED, "malformed Git revision output"); process_result_free(&r); return false; }
    *second++ = '\0'; trim(second);
    if (strcmp(first, second) == 0) {
        set_outcome(repo, success_outcome, success_outcome == OUT_SYNCED ? "fast-forwarded" : "already current");
        process_result_free(&r); return true;
    }
    process_result_free(&r);
    {
        char *ancestor[] = {(char *)cfg->git, "-C", (char *)target, "merge-base", "--is-ancestor", "HEAD", (char *)remote_ref, NULL};
        if (command(cfg, &r, ancestor) == 0) {
            set_outcome(repo, OUT_SKIPPED, "fast-forward available"); process_result_free(&r); return true;
        }
        if (r.kind != PROCESS_EXITED || r.code != 1) { set_outcome(repo, OUT_FAILED, "cannot compare Git history"); process_result_free(&r); return false; }
        process_result_free(&r);
    }
    {
        char *ancestor[] = {(char *)cfg->git, "-C", (char *)target, "merge-base", "--is-ancestor", (char *)remote_ref, "HEAD", NULL};
        if (command(cfg, &r, ancestor) == 0) set_outcome(repo, OUT_DEGRADED, "local branch is ahead; refusing to modify it");
        else set_outcome(repo, OUT_DEGRADED, "branch has diverged; refusing automatic merge");
        process_result_free(&r); return false;
    }
}

static void sync_repo(const struct config *cfg, struct repo *repo, const char *root)
{
    char target[PATH_MAX], remote_ref[MAX_FIELD + 32];
    bool exists, directory;
    struct process_result r = {0};
    unsigned attempt;
    if (snprintf(target, sizeof(target), "%s/%s", root, repo->checkout) >= (int)sizeof(target)) {
        set_outcome(repo, OUT_FAILED, "checkout path is too long"); return;
    }
    if (!path_state(target, &exists, &directory)) { set_outcome(repo, OUT_FAILED, strerror(errno)); return; }
    if (!exists) {
        if (cfg->dry_run || cfg->status_only) { set_outcome(repo, OUT_SKIPPED, "repository is absent; clone required"); return; }
        if (mkdir_safe(root) != 0) { set_outcome(repo, OUT_FAILED, "cannot create source root safely"); return; }
        for (attempt = 0; attempt <= cfg->retries; attempt++) {
            char *argv[] = {(char *)cfg->git, "clone", "--branch", repo->branch, "--single-branch", "--", repo->url, target, NULL};
            if (command(cfg, &r, argv) == 0) { set_outcome(repo, OUT_CLONED, "cloned"); process_result_free(&r); return; }
            diagnostic("WARN", "%s clone attempt %u/%u: %s", repo->name, attempt + 1, cfg->retries + 1, process_kind_name(r.kind));
            process_result_free(&r);
            if (interrupted) { set_outcome(repo, OUT_FAILED, "interrupted"); return; }
            if (attempt < cfg->retries) sleep(cfg->retry_delay);
        }
        set_outcome(repo, OUT_FAILED, "clone failed after retries"); return;
    }
    if (!directory) { set_outcome(repo, OUT_FAILED, "existing checkout is not a safe directory"); return; }
    if (!git_ok(cfg, target, &r, "rev-parse", "--show-toplevel", NULL)) {
        set_outcome(repo, OUT_FAILED, "existing path is not a Git work tree"); process_result_free(&r); return;
    }
    trim(r.output);
    {
        char resolved[PATH_MAX];
        if (realpath(target, resolved) == NULL || strcmp(resolved, r.output) != 0) {
            set_outcome(repo, OUT_FAILED, "checkout is nested, linked, or malformed"); process_result_free(&r); return;
        }
    }
    process_result_free(&r);
    if (!git_ok(cfg, target, &r, "status", "--porcelain", NULL)) {
        set_outcome(repo, OUT_FAILED, "cannot inspect working tree"); process_result_free(&r); return;
    }
    if (*r.output != '\0') { set_outcome(repo, OUT_DEGRADED, "working tree is dirty; refusing synchronization"); process_result_free(&r); return; }
    process_result_free(&r);
    if (snprintf(remote_ref, sizeof(remote_ref), "refs/remotes/origin/%s", repo->branch) >= (int)sizeof(remote_ref)) {
        set_outcome(repo, OUT_FAILED, "branch name is too long"); return;
    }
    if (cfg->dry_run || cfg->status_only) { (void)local_relation(cfg, repo, target, remote_ref, OUT_CURRENT); return; }
    for (attempt = 0; attempt <= cfg->retries; attempt++) {
        char *fetch[] = {(char *)cfg->git, "-C", target, "fetch", "--no-tags", "--", repo->url, repo->branch, NULL};
        if (command(cfg, &r, fetch) == 0) break;
        diagnostic("WARN", "%s fetch attempt %u/%u: %s", repo->name, attempt + 1, cfg->retries + 1, process_kind_name(r.kind));
        process_result_free(&r);
        if (interrupted) { set_outcome(repo, OUT_FAILED, "interrupted"); return; }
        if (attempt < cfg->retries) sleep(cfg->retry_delay);
    }
    if (attempt > cfg->retries) { set_outcome(repo, OUT_FAILED, "fetch failed after retries"); return; }
    process_result_free(&r);
    if (!git_ok(cfg, target, &r, "rev-parse", "FETCH_HEAD", NULL)) {
        set_outcome(repo, OUT_FAILED, "fetch did not produce FETCH_HEAD"); process_result_free(&r); return;
    }
    trim(r.output);
    {
        char fetched[128];
        if (strlen(r.output) >= sizeof(fetched)) { set_outcome(repo, OUT_FAILED, "invalid fetched revision"); process_result_free(&r); return; }
        strcpy(fetched, r.output); process_result_free(&r);
        if (!local_relation(cfg, repo, target, fetched, OUT_CURRENT)) return;
        if (repo->outcome == OUT_CURRENT) return;
        if (repo->outcome == OUT_SKIPPED) {
            char *merge[] = {(char *)cfg->git, "-C", target, "merge", "--ff-only", fetched, NULL};
            if (command(cfg, &r, merge) == 0) set_outcome(repo, OUT_SYNCED, "fast-forwarded");
            else set_outcome(repo, OUT_FAILED, "fast-forward update failed");
            process_result_free(&r);
        }
    }
}

static const char *outcome_name(enum outcome value)
{
    static const char *names[] = {"synchronized", "already current", "cloned", "skipped", "degraded", "failed"};
    return names[value];
}

int main(int argc, char **argv)
{
    struct config cfg = {"cogboot.manifest", NULL, "git", 5, 5, 300, false, false};
    struct repo *repos = NULL;
    size_t count = 0, i, totals[6] = {0};
    int option, exit_status = 0;
    static const struct option options[] = {
        {"manifest", required_argument, NULL, 'm'}, {"source-root", required_argument, NULL, 'r'},
        {"git", required_argument, NULL, 'g'}, {"retry-count", required_argument, NULL, 1000},
        {"retry-delay", required_argument, NULL, 1001}, {"timeout", required_argument, NULL, 1002},
        {"dry-run", no_argument, NULL, 'n'}, {"status", no_argument, NULL, 's'},
        {"help", no_argument, NULL, 'h'}, {"version", no_argument, NULL, 'V'}, {NULL, 0, NULL, 0}
    };
    while ((option = getopt_long(argc, argv, "m:r:g:nshV", options, NULL)) != -1) {
        switch (option) {
        case 'm': cfg.manifest = optarg; break; case 'r': cfg.source_root = optarg; break;
        case 'g': cfg.git = optarg; break; case 'n': cfg.dry_run = true; break; case 's': cfg.status_only = true; break;
        case 'V': printf("cogboot %s\n", COGBOOT_VERSION); return 0; case 'h': usage(stdout); return 0;
        case 1000: if (!parse_unsigned(optarg, 100, &cfg.retries)) goto bad_number; break;
        case 1001: if (!parse_unsigned(optarg, 86400, &cfg.retry_delay)) goto bad_number; break;
        case 1002: if (!parse_unsigned(optarg, 86400, &cfg.timeout)) goto bad_number; break;
        default: usage(stderr); return 64;
        }
    }
    if (optind != argc || cfg.source_root == NULL || cfg.source_root[0] != '/') {
        diagnostic("ERROR", "--source-root must be an absolute path and no positional arguments are accepted"); usage(stderr); return 64;
    }
    if (parse_manifest(cfg.manifest, &repos, &count) != 0) return 65;
    (void)signal(SIGINT, on_signal); (void)signal(SIGTERM, on_signal); (void)signal(SIGHUP, on_signal);
    diagnostic("INFO", "%s %lu repositories from %s", cfg.status_only ? "inspecting" : (cfg.dry_run ? "planning" : "synchronizing"), (unsigned long)count, cfg.manifest);
    for (i = 0; i < count; i++) {
        if (interrupted) set_outcome(&repos[i], OUT_SKIPPED, "not processed after interruption");
        else sync_repo(&cfg, &repos[i], cfg.source_root);
        totals[repos[i].outcome]++;
        printf("cogboot: %-15s %-20s %s\n", outcome_name(repos[i].outcome), repos[i].name, repos[i].detail);
        if (repos[i].required && (repos[i].outcome == OUT_FAILED || repos[i].outcome == OUT_DEGRADED ||
            (repos[i].outcome == OUT_SKIPPED && !cfg.dry_run && !cfg.status_only))) exit_status = 1;
    }
    printf("cogboot: summary: synchronized=%lu already-current=%lu cloned=%lu skipped=%lu degraded=%lu failed=%lu\n",
        (unsigned long)totals[OUT_SYNCED], (unsigned long)totals[OUT_CURRENT], (unsigned long)totals[OUT_CLONED],
        (unsigned long)totals[OUT_SKIPPED], (unsigned long)totals[OUT_DEGRADED], (unsigned long)totals[OUT_FAILED]);
    free_repos(repos, count);
    return interrupted ? 128 + interrupted : exit_status;
bad_number:
    diagnostic("ERROR", "invalid numeric option: %s", optarg); return 64;
}
