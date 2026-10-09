import sys, os
sys.argv_ = sys.argv
from common import *
from measure import hunks
def changed(repo, sha, files, pkg=None):
    parent = sh("git","rev-parse",sha+"^",cwd=f"{CORPUS}/{repo}").decode().strip()
    out=[]
    for f in files:
        old,new=hunks(repo,parent,sha,f)
        tab=func_table(f"{WORK}/{repo}/{sha[:10]}/patch/{f}")
        qs=sorted({enclosing(tab,l) for l in new})
        out.append((f,qs))
    return out
if __name__=="__main__":
    import json
    repo=sys.argv[1]
    for x in json.load(open(f"results_{repo}.json"))["results"][:int(sys.argv[2])]:
        print(x["sha"][:10], changed(repo,x["sha"],x["files"]))
