"""Phase-1 re-run of the step-1 survey against the fixed change detection (writes changes_survey_phase1.json)."""
import json, sys, time
from changes import REPOS, recent_commits, snapshot, pkg_info, changed_functions, existing_impact_modified

out = []
for name in REPOS:
    for c in recent_commits(name, 30):
        t0 = time.time()
        b, p = snapshot(name, c["parent"]), snapshot(name, c["commit"])
        pf = {f.path: f.raw for f in p.files}
        pkg, pyroot = pkg_info(name, pf)
        ch, meta = changed_functions(b, p, pkg, REPOS[name]["exclude"])
        try:
            from invariantguard.snapshots.capture import capture_git_revision
            from invariantguard.orchestration import SEMANTICS_PROFILE
            from changes import repo
            r = repo(name)
            bb = capture_git_revision(r, c["parent"], r.toplevel, [pkg], [".git", "test"], SEMANTICS_PROFILE)
            pp = capture_git_revision(r, c["commit"], r.toplevel, [pkg], [".git", "test"], SEMANTICS_PROFILE)
            ex = existing_impact_modified(bb, pp)
        except Exception as e:  # noqa
            ex = {"error": repr(e)}
        mine = {f"{x.path}::{x.qualname}" for x in ch if x.status != "deleted"}
        exist = set(ex.get("modified", [])) | set(ex.get("added", []))
        rec = {"repo": name, **c, "pkg": pkg, "pyroot": pyroot, "changes": [x.__dict__ for x in ch],
               "existing_modified_or_added": sorted(exist), "existing_key_changed": ex.get("key_changed"),
               "missed_by_existing": sorted(mine - exist), "secs": round(time.time() - t0, 2)}
        out.append(rec)
        print(round(time.time()-t0,1), name, c["commit"][:8], c["subject"][:50], "| changed", len(mine), "| existing", len(exist & mine), "| missed", len(mine - exist), file=sys.stderr, flush=True)
json.dump(out, open("results/changes_survey_phase1.json", "w"), indent=1)
