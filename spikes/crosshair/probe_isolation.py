"""Same docker flags as run_container.py; confirms network denied, source read-only, rootfs read-only, env clean, uid nobody."""
import subprocess, json, os
from run_container import IMAGE, HERE
code = r'''
import socket, os, json
r = {}
try: socket.create_connection(("1.1.1.1", 53), timeout=3); r["network"] = "REACHABLE"
except Exception as e: r["network"] = "denied: " + type(e).__name__
for p in ("/case/base/x_probe", "/usr/x_probe"):
    try: open(p, "w").write("x"); r["write " + p] = "ALLOWED"
    except Exception as e: r["write " + p] = "denied: " + type(e).__name__
try: open("/tmp/x", "w").write("x"); r["write /tmp"] = "allowed (tmpfs)"
except Exception as e: r["write /tmp"] = "denied"
r["uid"] = os.getuid(); r["env_keys"] = sorted(os.environ)
print(json.dumps(r))'''
d = os.path.join(HERE, "work", "boltons", os.listdir(os.path.join(HERE, "work", "boltons"))[0])
argv = ["docker","run","--rm","--pull","never","--network","none","--read-only","--tmpfs","/tmp:rw,noexec,nosuid,nodev,size=128m","--mount",f"type=bind,source={d},target=/case,readonly",
        "--user","65534:65534","--cap-drop","ALL","--security-opt","no-new-privileges","--env","HOME=/tmp","--entrypoint","python",IMAGE,"-c",code]
print(subprocess.run(argv, capture_output=True, text=True).stdout)
