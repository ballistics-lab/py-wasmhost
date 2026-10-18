/*
 * lg2: a small git client over libgit2, built to WASI (wasm32-wasip1) and run by wasmhost.
 *
 * Files go through WASI (the host preopens the working directory as "/"). The network does not: WASI preview1 has no
 * sockets, so connections are a git_stream that is served by four functions the host imports from `wasmhost_net`.
 * libgit2's own HTTP client runs on top of it, and the host does the TCP and the TLS.
 *
 * Credentials come from the environment: GIT_USERNAME and GIT_PASSWORD (or GIT_TOKEN, a token used as the password).
 * The committer comes from GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL, else from the repository's user.name / user.email.
 */
#include <git2.h>
#include <git2/sys/errors.h>
#include <git2/sys/stream.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* stubs/socket.c: the plain connection is libgit2's own socket stream, replaced; TLS is registered here. */
extern int lg2_stream_new(git_stream **out, const char *host, const char *port, int tls);

static int tls_init(git_stream **out, const char *host, const char *port) { return lg2_stream_new(out, host, port, 1); }

static void register_streams(void)
{
	git_stream_registration tls = {GIT_STREAM_VERSION, tls_init, NULL};
	git_stream_register(GIT_STREAM_TLS, &tls);
}

/* ---- helpers ---- */

static int fail(int error, const char *what)
{
	const git_error *e = git_error_last();
	fprintf(stderr, "git %s: %s\n", what, e ? e->message : "error");
	if (e && strstr(e->message, "authentication"))
		fprintf(stderr, "set GIT_USERNAME and GIT_TOKEN (or GIT_PASSWORD) in the environment\n");
	return error ? 1 : 0;
}

#define CHECK(call, what) \
	do { if ((call) < 0) return fail(1, what); } while (0)

static int cred_cb(git_credential **out, const char *url, const char *user_from_url, unsigned int allowed, void *payload)
{
	const char *user = getenv("GIT_USERNAME"), *pass = getenv("GIT_PASSWORD"), *token = getenv("GIT_TOKEN");
	(void)url; (void)payload;
	if (!(allowed & GIT_CREDENTIAL_USERPASS_PLAINTEXT))
		return GIT_PASSTHROUGH;
	if (!pass)
		pass = token;
	if (!pass)
		return GIT_PASSTHROUGH;
	if (!user)
		user = user_from_url ? user_from_url : "x-access-token";
	return git_credential_userpass_plaintext_new(out, user, pass);
}

static int progress_cb(const git_indexer_progress *st, void *payload)
{
	(void)payload;
	if (st->total_objects) {
		fprintf(stderr, "\rReceiving objects: %u/%u", st->received_objects, st->total_objects);
		if (st->received_objects == st->total_objects)
			fprintf(stderr, ", resolving deltas: %u/%u", st->indexed_deltas, st->total_deltas);
	}
	return 0;
}

static int sideband_cb(const char *str, int len, void *payload)
{
	(void)payload;
	fwrite(str, 1, (size_t)len, stderr);
	return 0;
}

static void set_callbacks(git_remote_callbacks *cb)
{
	cb->credentials = cred_cb;
	cb->transfer_progress = progress_cb;
	cb->sideband_progress = sideband_cb;
}

static int open_repo(git_repository **repo)
{
	if (git_repository_open_ext(repo, ".", GIT_REPOSITORY_OPEN_CROSS_FS, NULL) < 0) {
		const git_error *e = git_error_last();
		fprintf(stderr, "fatal: not a git repository (%s)\n", e ? e->message : "?");
		return 1;
	}
	return 0;
}

static int signature(git_signature **sig, git_repository *repo)
{
	const char *name = getenv("GIT_AUTHOR_NAME"), *email = getenv("GIT_AUTHOR_EMAIL");
	if (name && email)
		return git_signature_now(sig, name, email);
	return git_signature_default(sig, repo);
}

/* ---- commands ---- */

static int cmd_init(int argc, char **argv)
{
	git_repository *repo;
	CHECK(git_repository_init(&repo, argc > 1 ? argv[1] : ".", 0), "init");
	printf("Initialized empty Git repository in %s/.git/\n", argc > 1 ? argv[1] : ".");
	git_repository_free(repo);
	return 0;
}

static int cmd_clone(int argc, char **argv)
{
	git_repository *repo = NULL;
	git_clone_options opts = GIT_CLONE_OPTIONS_INIT;
	const char *url, *dir;
	char buf[256];
	if (argc < 2) {
		fprintf(stderr, "usage: git clone <url> [dir]\n");
		return 2;
	}
	url = argv[1];
	if (argc > 2) {
		dir = argv[2];
	} else {
		const char *slash = strrchr(url, '/');
		size_t n;
		dir = slash ? slash + 1 : url;
		n = strlen(dir);
		if (n > 4 && !strcmp(dir + n - 4, ".git"))
			n -= 4;
		snprintf(buf, sizeof buf, "%.*s", (int)n, dir);
		dir = buf;
	}
	set_callbacks(&opts.fetch_opts.callbacks);
	printf("Cloning into '%s'...\n", dir);
	CHECK(git_clone(&repo, url, dir, &opts), "clone");
	fprintf(stderr, "\n");
	git_repository_free(repo);
	return 0;
}

static int cmd_status(void)
{
	git_repository *repo;
	git_status_list *list;
	git_status_options opts = GIT_STATUS_OPTIONS_INIT;
	size_t i, n;
	if (open_repo(&repo))
		return 1;
	opts.show = GIT_STATUS_SHOW_INDEX_AND_WORKDIR;
	opts.flags = GIT_STATUS_OPT_INCLUDE_UNTRACKED | GIT_STATUS_OPT_RENAMES_HEAD_TO_INDEX;
	CHECK(git_status_list_new(&list, repo, &opts), "status");
	{
		git_reference *head;
		if (git_repository_head(&head, repo) == 0) {
			printf("On branch %s\n", git_reference_shorthand(head));
			git_reference_free(head);
		}
	}
	n = git_status_list_entrycount(list);
	for (i = 0; i < n; i++) {
		const git_status_entry *e = git_status_byindex(list, i);
		char x = ' ', y = ' ';
		const char *path;
		unsigned s = e->status;
		if (s & GIT_STATUS_INDEX_NEW) x = 'A';
		else if (s & GIT_STATUS_INDEX_MODIFIED) x = 'M';
		else if (s & GIT_STATUS_INDEX_DELETED) x = 'D';
		else if (s & GIT_STATUS_INDEX_RENAMED) x = 'R';
		if (s & GIT_STATUS_WT_NEW) { x = '?'; y = '?'; }
		else if (s & GIT_STATUS_WT_MODIFIED) y = 'M';
		else if (s & GIT_STATUS_WT_DELETED) y = 'D';
		if (s & GIT_STATUS_IGNORED) continue;
		path = e->index_to_workdir ? e->index_to_workdir->old_file.path : e->head_to_index->old_file.path;
		printf("%c%c %s\n", x, y, path);
	}
	if (!n)
		printf("nothing to commit, working tree clean\n");
	git_status_list_free(list);
	git_repository_free(repo);
	return 0;
}

static int cmd_add(int argc, char **argv)
{
	git_repository *repo;
	git_index *index;
	git_strarray ps;
	int i;
	if (open_repo(&repo))
		return 1;
	CHECK(git_repository_index(&index, repo), "add");
	{
		/* paths are the user's, relative to the current directory; the index wants them from the root */
		char cwd[1024], prefix[1024] = "";
		const char *workdir = git_repository_workdir(repo);
		if (getcwd(cwd, sizeof cwd) && workdir && !strncmp(cwd, workdir, strlen(workdir) - 1) && strlen(cwd) >= strlen(workdir))
			snprintf(prefix, sizeof prefix, "%s", cwd + strlen(workdir));
		for (i = 1; i < argc; i++) {
			char path[2048];
			if (!strcmp(argv[i], "-A") || !strcmp(argv[i], "--all"))
				snprintf(path, sizeof path, "*");
			else if (!strcmp(argv[i], "."))
				snprintf(path, sizeof path, "%s", *prefix ? prefix : "*");
			else
				snprintf(path, sizeof path, "%s%s%s", prefix, *prefix ? "/" : "", argv[i]);
			if (!strcmp(path, "*") || !strcmp(argv[i], ".")) {
				char *spec = path;
				ps.strings = &spec;
				ps.count = 1;
				CHECK(git_index_add_all(index, &ps, GIT_INDEX_ADD_DEFAULT, NULL, NULL), "add");
				CHECK(git_index_update_all(index, &ps, NULL, NULL), "add");
			} else {
				CHECK(git_index_add_bypath(index, path), "add");
			}
		}
	}
	CHECK(git_index_write(index), "add");
	git_index_free(index);
	git_repository_free(repo);
	return 0;
}

static int cmd_commit(int argc, char **argv)
{
	git_repository *repo;
	git_index *index;
	git_oid tree_id, commit_id;
	git_tree *tree;
	git_signature *sig;
	git_commit *parent = NULL;
	const char *msg = NULL;
	int i;
	for (i = 1; i + 1 < argc; i++)
		if (!strcmp(argv[i], "-m"))
			msg = argv[i + 1];
	if (!msg) {
		fprintf(stderr, "usage: git commit -m <message>\n");
		return 2;
	}
	if (open_repo(&repo))
		return 1;
	CHECK(signature(&sig, repo), "commit (set GIT_AUTHOR_NAME and GIT_AUTHOR_EMAIL)");
	CHECK(git_repository_index(&index, repo), "commit");
	CHECK(git_index_write_tree(&tree_id, index), "commit");
	CHECK(git_tree_lookup(&tree, repo, &tree_id), "commit");
	{
		git_oid head;
		if (git_reference_name_to_id(&head, repo, "HEAD") == 0)
			CHECK(git_commit_lookup(&parent, repo, &head), "commit");
	}
	CHECK(git_commit_create_v(&commit_id, repo, "HEAD", sig, sig, NULL, msg, tree, parent ? 1 : 0, parent), "commit");
	{
		char hex[8];
		git_oid_tostr(hex, sizeof hex, &commit_id);
		printf("[%s] %s\n", hex, msg);
	}
	git_commit_free(parent);
	git_tree_free(tree);
	git_index_free(index);
	git_signature_free(sig);
	git_repository_free(repo);
	return 0;
}

static int cmd_log(int argc, char **argv)
{
	git_repository *repo;
	git_revwalk *walk;
	git_oid oid;
	int limit = -1, i;
	for (i = 1; i + 1 < argc; i++)
		if (!strcmp(argv[i], "-n"))
			limit = atoi(argv[i + 1]);
	if (open_repo(&repo))
		return 1;
	CHECK(git_revwalk_new(&walk, repo), "log");
	CHECK(git_revwalk_push_head(walk), "log");
	git_revwalk_sorting(walk, GIT_SORT_TIME);
	while (limit != 0 && git_revwalk_next(&oid, walk) == 0) {
		git_commit *c;
		const git_signature *a;
		char hex[GIT_OID_MAX_HEXSIZE + 1];
		if (git_commit_lookup(&c, repo, &oid) < 0)
			break;
		a = git_commit_author(c);
		git_oid_tostr(hex, sizeof hex, &oid);
		printf("commit %s\nAuthor: %s <%s>\n\n    %s\n", hex, a->name, a->email, git_commit_summary(c));
		git_commit_free(c);
		limit--;
	}
	git_revwalk_free(walk);
	git_repository_free(repo);
	return 0;
}

static int print_line(const git_diff_delta *d, const git_diff_hunk *h, const git_diff_line *l, void *p)
{
	(void)d; (void)h; (void)p;
	if (l->origin == GIT_DIFF_LINE_CONTEXT || l->origin == GIT_DIFF_LINE_ADDITION || l->origin == GIT_DIFF_LINE_DELETION)
		fputc(l->origin, stdout);
	fwrite(l->content, 1, l->content_len, stdout);
	return 0;
}

static int cmd_diff(int argc, char **argv)
{
	git_repository *repo;
	git_diff *diff;
	git_tree *tree = NULL;
	git_object *obj;
	int cached = argc > 1 && !strcmp(argv[1], "--cached");
	git_diff_options opts = GIT_DIFF_OPTIONS_INIT;
	if (open_repo(&repo))
		return 1;
	if (git_revparse_single(&obj, repo, "HEAD^{tree}") == 0)
		tree = (git_tree *)obj;
	if (cached) {
		CHECK(git_diff_tree_to_index(&diff, repo, tree, NULL, &opts), "diff");
	} else {
		git_index *index;
		CHECK(git_repository_index(&index, repo), "diff");
		CHECK(git_diff_index_to_workdir(&diff, repo, index, &opts), "diff");
		git_index_free(index);
	}
	CHECK(git_diff_print(diff, GIT_DIFF_FORMAT_PATCH, print_line, NULL), "diff");
	git_diff_free(diff);
	git_tree_free(tree);
	git_repository_free(repo);
	return 0;
}

static int cmd_branch(int argc, char **argv)
{
	git_repository *repo;
	if (open_repo(&repo))
		return 1;
	if (argc == 1) {
		git_branch_iterator *it;
		git_reference *ref;
		git_branch_t type;
		CHECK(git_branch_iterator_new(&it, repo, GIT_BRANCH_ALL), "branch");
		while (git_branch_next(&ref, &type, it) == 0) {
			const char *name;
			git_branch_name(&name, ref);
			printf("%c %s\n", git_branch_is_head(ref) ? '*' : ' ', name);
			git_reference_free(ref);
		}
		git_branch_iterator_free(it);
	} else if (!strcmp(argv[1], "-d") && argc > 2) {
		git_reference *ref;
		CHECK(git_branch_lookup(&ref, repo, argv[2], GIT_BRANCH_LOCAL), "branch");
		CHECK(git_branch_delete(ref), "branch");
		git_reference_free(ref);
	} else {
		git_oid head;
		git_commit *c;
		git_reference *ref;
		CHECK(git_reference_name_to_id(&head, repo, "HEAD"), "branch");
		CHECK(git_commit_lookup(&c, repo, &head), "branch");
		CHECK(git_branch_create(&ref, repo, argv[1], c, 0), "branch");
		git_reference_free(ref);
		git_commit_free(c);
	}
	git_repository_free(repo);
	return 0;
}

static int cmd_checkout(int argc, char **argv)
{
	git_repository *repo;
	git_object *obj;
	git_checkout_options opts = GIT_CHECKOUT_OPTIONS_INIT;
	char refname[256];
	if (argc < 2) {
		fprintf(stderr, "usage: git checkout <branch|commit>\n");
		return 2;
	}
	if (open_repo(&repo))
		return 1;
	opts.checkout_strategy = GIT_CHECKOUT_SAFE;
	CHECK(git_revparse_single(&obj, repo, argv[1]), "checkout");
	CHECK(git_checkout_tree(repo, obj, &opts), "checkout");
	snprintf(refname, sizeof refname, "refs/heads/%s", argv[1]);
	{
		git_reference *ref;
		if (git_reference_lookup(&ref, repo, refname) == 0) {
			git_reference_free(ref);
			CHECK(git_repository_set_head(repo, refname), "checkout");
		} else {
			CHECK(git_repository_set_head_detached(repo, git_object_id(obj)), "checkout");
		}
	}
	git_object_free(obj);
	git_repository_free(repo);
	return 0;
}

static int cmd_remote(int argc, char **argv)
{
	git_repository *repo;
	if (open_repo(&repo))
		return 1;
	if (argc > 3 && !strcmp(argv[1], "add")) {
		git_remote *r;
		CHECK(git_remote_create(&r, repo, argv[2], argv[3]), "remote");
		git_remote_free(r);
	} else {
		git_strarray names;
		size_t i;
		CHECK(git_remote_list(&names, repo), "remote");
		for (i = 0; i < names.count; i++) {
			git_remote *r;
			if (git_remote_lookup(&r, repo, names.strings[i]) == 0) {
				printf("%s\t%s\n", names.strings[i], git_remote_url(r));
				git_remote_free(r);
			}
		}
		git_strarray_dispose(&names);
	}
	git_repository_free(repo);
	return 0;
}

static int fetch_remote(git_repository *repo, const char *name)
{
	git_remote *remote;
	git_fetch_options opts = GIT_FETCH_OPTIONS_INIT;
	set_callbacks(&opts.callbacks);
	CHECK(git_remote_lookup(&remote, repo, name), "fetch");
	CHECK(git_remote_fetch(remote, NULL, &opts, NULL), "fetch");
	fprintf(stderr, "\n");
	git_remote_free(remote);
	return 0;
}

static int cmd_fetch(int argc, char **argv)
{
	git_repository *repo;
	int rc;
	if (open_repo(&repo))
		return 1;
	rc = fetch_remote(repo, argc > 1 ? argv[1] : "origin");
	git_repository_free(repo);
	return rc;
}

static int cmd_pull(int argc, char **argv)
{
	git_repository *repo;
	git_reference *head, *upstream, *updated;
	git_annotated_commit *theirs;
	git_merge_analysis_t analysis;
	git_merge_preference_t pref;
	const char *remote = argc > 1 ? argv[1] : "origin";
	if (open_repo(&repo))
		return 1;
	if (fetch_remote(repo, remote))
		return 1;
	CHECK(git_repository_head(&head, repo), "pull");
	CHECK(git_branch_upstream(&upstream, head), "pull (the branch has no upstream)");
	CHECK(git_annotated_commit_from_ref(&theirs, repo, upstream), "pull");
	CHECK(git_merge_analysis(&analysis, &pref, repo, (const git_annotated_commit **)&theirs, 1), "pull");
	if (analysis & GIT_MERGE_ANALYSIS_UP_TO_DATE) {
		printf("Already up to date.\n");
	} else if (analysis & GIT_MERGE_ANALYSIS_FASTFORWARD) {
		git_object *target;
		git_checkout_options co = GIT_CHECKOUT_OPTIONS_INIT;
		co.checkout_strategy = GIT_CHECKOUT_SAFE;
		CHECK(git_object_lookup(&target, repo, git_annotated_commit_id(theirs), GIT_OBJECT_COMMIT), "pull");
		CHECK(git_checkout_tree(repo, target, &co), "pull");
		CHECK(git_reference_set_target(&updated, head, git_annotated_commit_id(theirs), "pull: fast-forward"), "pull");
		git_reference_free(updated);
		git_object_free(target);
		printf("Fast-forward\n");
	} else {
		fprintf(stderr, "pull: not a fast-forward; merge is not supported yet\n");
		return 1;
	}
	git_annotated_commit_free(theirs);
	git_reference_free(upstream);
	git_reference_free(head);
	git_repository_free(repo);
	return 0;
}

static int cmd_push(int argc, char **argv)
{
	git_repository *repo;
	git_remote *remote;
	git_push_options opts = GIT_PUSH_OPTIONS_INIT;
	git_reference *head;
	char spec[512];
	char *specs[1] = {spec};
	git_strarray refspecs = {specs, 1};
	if (open_repo(&repo))
		return 1;
	CHECK(git_remote_lookup(&remote, repo, argc > 1 ? argv[1] : "origin"), "push");
	if (argc > 2) {
		snprintf(spec, sizeof spec, "refs/heads/%s:refs/heads/%s", argv[2], argv[2]);
	} else {
		CHECK(git_repository_head(&head, repo), "push");
		snprintf(spec, sizeof spec, "%s:%s", git_reference_name(head), git_reference_name(head));
		git_reference_free(head);
	}
	opts.callbacks.credentials = cred_cb;
	CHECK(git_remote_push(remote, &refspecs, &opts), "push");
	git_remote_free(remote);
	git_repository_free(repo);
	return 0;
}

int main(int argc, char **argv)
{
	const char *cmd;
	int rc = 2;
	if (argc < 2) {
		fprintf(stderr, "usage: git <init|clone|status|add|commit|log|diff|branch|checkout|remote|fetch|pull|push> ...\n");
		return 2;
	}
	if (getenv("LG2_CWD"))
		chdir(getenv("LG2_CWD")); /* the host preopens the repository root; this is where in it the user is */
	git_libgit2_init();
	git_libgit2_opts(GIT_OPT_SET_OWNER_VALIDATION, 0);
	register_streams();
	cmd = argv[1];
	argc--;
	argv++;
	if (!strcmp(cmd, "init")) rc = cmd_init(argc, argv);
	else if (!strcmp(cmd, "clone")) rc = cmd_clone(argc, argv);
	else if (!strcmp(cmd, "status")) rc = cmd_status();
	else if (!strcmp(cmd, "add")) rc = cmd_add(argc, argv);
	else if (!strcmp(cmd, "commit")) rc = cmd_commit(argc, argv);
	else if (!strcmp(cmd, "log")) rc = cmd_log(argc, argv);
	else if (!strcmp(cmd, "diff")) rc = cmd_diff(argc, argv);
	else if (!strcmp(cmd, "branch")) rc = cmd_branch(argc, argv);
	else if (!strcmp(cmd, "checkout")) rc = cmd_checkout(argc, argv);
	else if (!strcmp(cmd, "remote")) rc = cmd_remote(argc, argv);
	else if (!strcmp(cmd, "fetch")) rc = cmd_fetch(argc, argv);
	else if (!strcmp(cmd, "pull")) rc = cmd_pull(argc, argv);
	else if (!strcmp(cmd, "push")) rc = cmd_push(argc, argv);
	else fprintf(stderr, "git: '%s' is not a supported command\n", cmd);
	git_libgit2_shutdown();
	return rc;
}
