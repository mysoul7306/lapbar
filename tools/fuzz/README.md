# Differential fuzz for the model guard

Maintainer tool, not part of the package. It checks the hook's hand-written JS reader against a real parser: node ships acorn
internally, so no npm install is needed.

```
cd tools/fuzz
python3 -I gen.py 20000 1 > corpus.jsonl                      # random scripts built from tricky lexical pieces + known attack seeds
node --expose-internals oracle.js < corpus.jsonl > oracle.jsonl   # acorn: does JS really see an agent() call, and with which model / effort?
python3 -I compare.py ../../src/lapbar/assets/hooks/subagent-model-guard.py corpus.jsonl oracle.jsonl run
```

`compare.py` prints `BYPASS` (JS executes a call the hook must deny, hook passes - the number that has to stay 0), `fp` (JS sees nothing
to deny, hook denies - the fail-closed constructs listed in the README), `syntax` (acorn rejects the script, so it would not run anyway)
and writes the examples to `run-bypass.json`, `run-fp.json`, `run-syntaxpass.json`. Run it against an older hook as a control: the
harness is only trusted when it finds the shapes that older version was known to miss.

2026-10-28, 60,000 scripts (seeds 1 and 2): 0 bypasses on 0.2.0, 1,364 on 0.1.1.
