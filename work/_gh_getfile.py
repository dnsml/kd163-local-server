# -*- coding: utf-8 -*-
"""按 ref 从 GitHub 拉单个文件（base64 内容 API），写到本地路径。

用法：python work/_gh_getfile.py <ref> <repo_path> <local_path>
"""
import base64
import json
import os
import ssl
import sys
import urllib.request

REPO = "dnsml/kd163-local-server"
TOKEN = open("D:/66666/.gh_pat", encoding="utf-8").read().strip()
PROXY = (os.environ.get("KD163_GH_PROXY")
         or os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or "").strip()


def opener(verify=True, proxy=""):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    hs = [urllib.request.HTTPSHandler(context=ctx),
          urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    return urllib.request.build_opener(*hs)


def fetch(ref, path):
    url = ("https://api.github.com/repos/%s/contents/%s?ref=%s"
           % (REPO, urllib.parse.quote(path), ref))
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "kd163")
    last = None
    for proxy in ([PROXY, ""] if PROXY else [""]):
        for verify in (True, False):
            try:
                with opener(verify, proxy).open(req, timeout=60) as r:
                    d = json.loads(r.read().decode())
                return base64.b64decode(d["content"])
            except Exception as e:      # noqa: BLE001
                last = e
    raise last


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    ref, path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    raw = fetch(ref, path)
    d = os.path.dirname(os.path.abspath(out))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(out, "wb") as f:
        f.write(raw)
    print("%-55s %7dB" % (path, len(raw)))
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    raise SystemExit(main())
