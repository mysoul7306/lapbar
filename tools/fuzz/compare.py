"""Join hook decisions with the acorn oracle. Usage: python3 -I compare.py <hook.py> corpus.jsonl oracle.jsonl <out-prefix>

bypass  = acorn parses, sees a call that must be denied (haiku / fable / missing or non-literal model or effort / effort max /
          spread or computed opts) or an agent alias, and the hook passes.
fp      = acorn parses, sees nothing to deny, and the hook denies (bucketed by reason head).
syntax  = acorn cannot parse (JS would not run it either) - counted, not judged.
"""
import collections
import importlib.util
import json
import sys

hook_path, corpus_path, oracle_path, prefix = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("g", hook_path)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

oracle = {}
for line in open(oracle_path, encoding="utf-8"):
    o = json.loads(line); oracle[o["id"]] = o

SAFE_MODELS = {"opus", "sonnet"}
SAFE_EFFORTS = {"low", "medium", "high", "xhigh"}


def must_deny(o):
    if o["alias"] > 0:
        return "alias"
    for c in o["calls"]:
        if c["shape"] != "object":
            return "opts-" + c["shape"]
        if c["model"] not in SAFE_MODELS:
            return "model-" + c["model"]
        if c["effort"] not in SAFE_EFFORTS:
            return "effort-" + c["effort"]
    return None


stats = collections.Counter()
fp_reasons = collections.Counter()
bypass, fps, syntax_pass = [], [], []
for line in open(corpus_path, encoding="utf-8"):
    rec = json.loads(line)
    o = oracle[rec["id"]]
    try:
        reason = g.decide({"tool_name": "Workflow", "tool_input": {"script": rec["script"]}})
    except Exception as e:  # the hook's main would deny on an internal exception
        reason = "EXC " + type(e).__name__ + ": " + str(e)[:80]
    denied = bool(reason)
    if o["parse"] == "error":
        stats["syntax"] += 1
        stats["syntax-" + ("deny" if denied else "pass")] += 1
        if not denied and rec["kind"] in ("code", "seed"):
            syntax_pass.append((rec, o))
        continue
    need = must_deny(o)
    if need and not denied:
        stats["BYPASS"] += 1
        bypass.append({"id": rec["id"], "kind": rec["kind"], "name": rec.get("name"), "why": need, "calls": o["calls"], "script": rec["script"]})
    elif need and denied:
        stats["deny-ok"] += 1
    elif not need and denied:
        stats["fp"] += 1
        head = reason.split(" - ")[0].split(" (")[0][:70]
        fp_reasons[head] += 1
        fps.append({"id": rec["id"], "kind": rec["kind"], "reason": reason[:160], "script": rec["script"]})
    else:
        stats["pass-ok"] += 1
    stats["kind-" + rec["kind"]] += 1

print("hook:", hook_path)
for k in sorted(stats):
    print("  %-14s %d" % (k, stats[k]))
print("fp reasons:")
for k, v in fp_reasons.most_common(12):
    print("  %5d  %s" % (v, k))
json.dump(bypass, open(prefix + "-bypass.json", "w"), ensure_ascii=False, indent=1)
json.dump(fps[:400], open(prefix + "-fp.json", "w"), ensure_ascii=False, indent=1)
json.dump([{"id": r["id"], "err": o.get("err"), "script": r["script"]} for r, o in syntax_pass[:200]], open(prefix + "-syntaxpass.json", "w"), ensure_ascii=False, indent=1)
print("bypass examples:", len(bypass), "->", prefix + "-bypass.json")
for b in bypass[:12]:
    print("  [%s] %s %s | %s" % (b["id"], b["kind"], b.get("name") or "", b["why"]), "|", json.dumps(b["script"])[:140])
