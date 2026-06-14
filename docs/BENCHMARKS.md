# Token benchmarks

This documents what `scripts/benchmark_tokens.py` measures and the numbers from a
run on this Linux host. The point is to make one design property observable: the
accordion gateway keeps the per-turn token cost roughly bounded as the underlying
UI tree grows, instead of serializing the whole tree into the agent's context.

Reproduce:

```bash
PYTHONPATH=src python3 scripts/benchmark_tokens.py
PYTHONPATH=src python3 scripts/benchmark_tokens.py --sizes 10,50,200,1000
```

## Methodology

- **Synthetic snapshots.** For each requested element count the script builds a
  plausible UI tree as the `(element_data, depth, parent_row_id)` stream that
  `cerebellum_cua.matrix.builder.build_snapshot` consumes: a desktop root, a few
  top-level windows, nested panes, and interactive leaves (buttons / edits /
  text). The tree is deterministic for a given size — no randomness — and hits
  the requested element count exactly. It is *not* captured from real
  applications.
- **Persistence.** Each snapshot is persisted to an in-memory SQLite backend via
  `get_backend(None)`, the same backend the dev/Linux path uses, then read back
  through the real `Accordion` and storage read paths.
- **Token estimate.** Every number is `cerebellum_cua.gateway.budget.estimate_tokens`
  applied to the serialized response. That is a heuristic — compact JSON length
  divided by ~4 characters per token, rounded up. It is **not** a model-exact
  tokenizer count; a real tokenizer (tiktoken, a llama.cpp vocabulary) would
  differ. The same heuristic is applied to every column so the columns are
  comparable to each other.

### What each column measures

| Column        | Measured object                                                        |
|---------------|------------------------------------------------------------------------|
| `elements`    | Number of elements in the snapshot (the input size).                   |
| `full_matrix` | Every element serialized to a dict (`get_all_elements` -> `to_dict`).  |
| `initial_ctx` | `Accordion.get_initial_context` — the first per-turn payload.          |
| `load_child`  | `Accordion.load_children` on one mid-tree parent pane (one expansion). |
| `read_text`   | The `read_text` aggregate: every on-screen text run + its bbox.        |
| `get_element` | `Accordion.get_element` for a single element.                          |

## Results

Measured on this host (Linux, CPython). `estimate_tokens` is the ~4-chars/token
heuristic described above; counts are estimates, not tokenizer-exact.

```
      elements   full_matrix   initial_ctx    load_child     read_text   get_element
------------------------------------------------------------------------------------
            10          1164            91           280           131           202
            50          6046            91           949           709           378
           200         24386            91           962          2906           381
          1000        122631            91           961         14773           368
          5000        618663            91           973         76110           386
```

### Reading the table

- `initial_ctx` is flat at ~91 across a 500x range of element counts. The
  initial context returns the root plus its depth-≤1 children, so its size does
  not track the total tree size.
- `load_child` (a single expansion) and `get_element` stay bounded — within a
  narrow band — as the tree grows by orders of magnitude.
- `full_matrix` grows roughly linearly with the element count (≈1.2k → ≈619k
  tokens, ~530x), as expected for serializing the whole tree.
- `read_text` also grows with the tree, because it deliberately aggregates every
  text run in the snapshot — it is a full-tree read, not a per-turn accordion
  slice, and is included to show the contrast.

So the bounded quantities are the per-turn accordion responses (`initial_ctx`,
`load_child`, `get_element`); the full-tree reads (`full_matrix`, `read_text`)
are the ones that scale with size.

## Caveats

- **Heuristic, not exact.** `estimate_tokens` approximates token count from JSON
  character length. A real tokenizer will produce different absolute numbers; the
  *shape* (bounded per-turn vs. full-tree growth) is what this measures.
- **Synthetic data.** The trees are generated, not captured from real Windows or
  Linux applications. Real UI trees have different breadth/depth distributions,
  longer names, and richer property bags, which would shift absolute counts.
- **Not a comparison.** These numbers describe this gateway's own behavior. They
  are not a benchmark against any other tool, approach, or product, and no such
  comparison is implied.
- **One expansion.** `load_child` measures a single mid-tree expansion. An agent
  that expands many nodes in one turn accumulates more tokens; the bounded claim
  is per response, not per session.
