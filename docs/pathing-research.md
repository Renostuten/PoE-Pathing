# Pathing research

## Recovered previous-run work

Recovery inventory captured on 2026-08-01 from branch `main` at `1671c31`.
The existing `.gitignore` modification belongs to the user and is preserved.

| Final state | Recovered work | Resolution in this run |
|---|---|---|
| Completed and revalidated | Deterministic passive-tree analyser and snapshot. | Reused, corrected a rounding/output detail, added filename discovery, group value/boundary metrics and exact vertex-biconnected blocks, then regenerated the snapshot twice identically. |
| Completed and revalidated | Deliberately limited exhaustive Problem A solver and synthetic tests. | Reused unchanged in principle, expanded its comparisons, and confirmed all supported pruned/unpruned exact pairs have the same best objective. |
| Completed and extended | Current/exact/legacy-prefix/priority scenarios, harness, and quick JSON. | Reused the shared scenarios, added faithful current-search counters plus both pruning variants, added two pruning fixtures, and ran quick and full matrices. |
| Completed and extended | Synthetic, API-contract, scoring, real-tree validity, ranking, and state-width tests. | Revalidated before Phase 2 and expanded to 49 passing tests covering preprocessing, probe equivalence, special-node policy, and the four-strategy harness. |
| Retained as the one production correction | Objective-aware prefix filtering in `TreeOptimizer`. | Kept because unconditional filtering measurably returned an equal-score, higher-cost extension. Allocated-start sorting was deliberately not integrated, keeping this run to one production behaviour change. |
| Preserved research-only | Optimistic-bound priority-guided bounded search. | Kept isolated and included only in the bounded parameter comparison; it is not wired into the application. |
| Completed | Query-specific non-useful-leaf pruning was missing. | Added an isolated queue peel, proof conditions, 14 focused tests, conservative PoE special-node protection, and shared exact/current benchmarks. |
| Completed | Biconnected and official-group boundary analysis was missing. | Added exact Tarjan blocks, invariant checks, all required group metrics, value distributions, and representative narrow/wide examples. |
| Completed | Final report and reproducible commands were missing. | This document now contains the original four checkpoints plus the completed cached-score follow-on, measured results, restrictions, and final production decision. |
| Unchanged pre-existing issue, non-blocking | The legacy graph exporter cannot currently serialize `PassiveNode` objects and `NodeLookup.get` raises where some callers test for `None`. | It did not block this work and remains explicitly out of scope. |

No usable recovered artefact was discarded or found irreparably broken. The
valid partial solver, scenarios, scripts, and tests were finished in place.
The user-owned `.gitignore` modification remains untouched.

## Checkpoint 1: recovered baseline

### Current optimisation problem

This investigation benchmarks **Problem A** only. Given an undirected passive
graph, an allocated set `A`, per-node scores derived from desired-stat weights,
and an at-most point budget `B`, a feasible recommendation is an ordered simple
path whose first node is in `A`, whose consecutive nodes are adjacent and
traversable, and whose cost is the number of path nodes outside `A`. Allocated
nodes cost and score zero on the extension; every unallocated node costs one
and contributes its production `StatScorer` score. Positive and zero/negative
connector nodes may be traversed. Candidates require positive cost and positive
cumulative score. The endpoint ranks them lexicographically by:

1. maximum total weighted score;
2. maximum `score / cost` efficiency.

The output is one path, not a branching passive allocation. Complete-tree
optimisation (Problem B) and binary useful/not-useful pruning models (Problem C)
remain explicitly separate.

### API and frontend contract

`POST /api/recommend-paths` accepts `allocated`, `max_points`, and a JSON object
whose keys are JSON-encoded `[stat_type, modifier_type]` pairs. The unchanged
response is `{"recommendations": [...]}`. Each item contains `target`, ordered
`path`, `cost`, `score`, `efficiency`, and `stats_gained` split into `desired`
and `other`. `tests/test_api_contract.py` exercises this exact shape and the
frontend consumers in `Sidebar.jsx` and `PassiveTreeView.jsx` were inspected.

One existing semantic caveat is that the frontend label “Target value” is sent
as a linear score weight, not enforced as a target. Malformed stat keys are
silently skipped, and direct API calls are not currently constrained to a
connected allocated set or a practical point-budget range.

### Current optimiser behaviour

Inspection of `TreeOptimizer` confirms a bounded approximate LIFO simple-path
search, not exact dynamic programming and not best-first search:

- one initial state is created for every allocated node in Python set order;
  a capped multi-start result can therefore vary with `PYTHONHASHSEED`;
- each state copies its complete path and visited set;
- states are bucketed by `(current node, point cost)`;
- safe subset dominance is applied inside a bucket;
- at most three nondominated states remain in a bucket, ranked by current
  score only;
- a LIFO frontier makes the largest production adjacency ID explore first;
- processing stops after 200,000 expanded states;
- only the best 350 generated candidates reach postprocessing;
- candidates are finally ranked by total score and efficiency.

The subset-dominance rule is safe under current additive, path-independent
scoring: at equal endpoint and cost, a state with at least as much score and a
visited-set subset can reproduce every continuation of the dominated state.
The three-state truncation is heuristic because other visited sets can have
different futures. States evicted from a bucket can also remain queued on the
frontier, so the bucket width is not a hard live-state bound.

The recovered targeted production correction makes prefix filtering consistent
with the declared objective: a strict prefix is removed only when an extension
has a strictly better `(score, efficiency)` rank. The former unconditional rule
removed Scion flat-life score 12/cost 1 in favour of score 12/cost 5. Sorting
allocated starts was evaluated but not retained, so capped multi-start set-order
sensitivity remains a documented limitation. The single retained correction
preserves response fields and path semantics; pruning remains disabled.

## Checkpoint 2: exact small-case comparison

### Exact baseline and guarantee

`ExactPathSolver` exhaustively enumerates every valid simple path from each
allocated start. It performs only structural rejection of repeated,
untraversable, and over-budget extensions. A completed run is therefore exact
for Problem A under the current additive scoring objective. Its worst-case time
and memory are exponential. The default 100,000-expanded-state safety limit
raises `ExactSearchLimitExceeded`; it never returns a partial result labelled
exact.

The result exposes expanded, generated, candidate, and structural-pruning
counters. Equal score/efficiency paths have a deterministic lexical tie-break.
For endpoint top-k comparisons the harness applies the production 350-candidate
pool and objective-aware prefix rule. Exact enumeration may additionally walk
through allocated zero-cost nodes after the budget is spent; that cannot
improve score but can add equal-rank terminal paths, so optimum-in-top-k is
compared by objective rank rather than exact path identity.

The standard-library tests cover zero-score connectors, competing branches,
cycles, distinct visited sets at a shared endpoint, multiple allocated starts,
class-start traversal, deterministic ties, useful prefixes, score-versus-
efficiency ordering, exact state-limit failure, real graph validity, and score
agreement with `PathEvaluator`.

### Reproducible benchmark methodology

Shared scenarios live in `benchmarks/pathing_scenarios.json` with seed
`20260728`. Run:

```text
$env:PYTHONHASHSEED = '0'
python scripts/benchmark_pathing.py --quick --repeats 3 --output docs/pathing-benchmark-results.json
python scripts/benchmark_pathing.py --full --no-memory --repeats 1
```

The committed quick report contains 14 scenarios and 76 strategy runs. It runs
unpruned/pruned exact enumeration, unpruned/pruned current search, a
legacy-prefix reproduction, and the recovered research-only optimistic-priority
strategy where applicable. The current-search records use an instrumented
probe whose recommendations are asserted equal to the actual optimiser before
its state counters are accepted.
Every returned top-k path is independently checked for adjacency, simplicity,
traversability, budget, score, cost, and efficiency. Three repeat fingerprints
and an additional `tracemalloc` run supply determinism and practical peak-memory
observations. Runtime and memory are explicitly machine-local; scores, paths,
gaps, validation, diagnostics, and fingerprints are deterministic.

### Baseline results

Representative committed quick results:

| Scenario | Exact best score / cost | Current best score / cost | Gap | Important comparison |
|---|---:|---:|---:|---|
| Zero-score connector | 12 / 2 | 12 / 2 | 0% | Connector retained. |
| Score-first vs efficiency-first | 10 / 2 | 10 / 2 | 0% | Score 10 wins over the more efficient score-9 branch. |
| Four visited sets at one bucket | 200 / 5 | 101 / 3 | 49.5% | Width 3 loses the only continuable visited set; width 4 recovers 200. |
| Adjacency/cap probe, deferred good branch | 50 / 1 | 3 / 3 | 94% | At cap 4, LIFO order misses the valuable branch. Reversing only start adjacency recovers 50. |
| Scion flat life, budget 5 | 12 / 1 | 12 / 1 | 0% | Legacy prefix filter returned 12 / 5. |
| Marauder physical attacker, budget 5 | 96 / 5 | 96 / 5 | 0% | Current optimum appears in top-k. |
| Witch elemental caster, budget 10 | 130 / 10 | 130 / 10 | 0% | Current default budget matches exact here. |

Across the final quick run, all 76 runs returned valid paths and all repeat
fingerprints were deterministic. Four of 48 exact-comparable heuristic runs had
a positive score gap: the default current search, its pruned variant, and the
priority search on the adversarial visited-set graph, plus the intentionally
tiny-cap LIFO probe. Six runs missed the exact objective in top-k; the other two
are the deliberately reproduced legacy prefix behaviour on synthetic and real
Scion cases. These examples prove bounded failure modes but do not estimate
their frequency on all PoE queries.

The final full sweep contains 18 scenarios and 151 valid runs; it adds bucket
widths 1 and 8 and real budgets 15 and 20. On sampled real Scion flat-life
cases, current width 1/3/8 all matched
exact total score through budget 20, while wider beams cost more time. The
exact baseline completed Scion budget 20 after 88,551 expansions and found
score 62/cost 18; the fixed current search returned the same objective, while
legacy prefix filtering returned score 62/cost 20. Exact enumeration exceeded
its deliberate 100,000-state limit both before and after pruning for the
Templar life/resistance budget-20 case, so no optimality claim is made there.

## Checkpoint 3: recovered passive-tree structure

`scripts/analyse_tree_data.py` loads the current raw export by discovered path,
reuses production filtering/parsing/scoring, and deterministically regenerates
`docs/tree-data-analysis.md`. Final measured facts are:

- source: `skilltree-export_3.28.0.json`, SHA-256
  `cbe09c477a294cf5116b930810a517d0001182ad9c8719aad06fa1f90ecfd6f5`;
- 3,338 raw nodes, 2,045 drawable nodes, 2,325 undirected edges;
- one 1,997-node component plus 48 drawable isolated nodes;
- average degree 2.2738, maximum degree 9;
- 776 raw groups and 652 groups containing drawable nodes;
- 66.49% of graph edges remain within an official export group;
- cycle rank 329, 1,317 nodes in the 2-core, 14 triangles;
- 687 articulation points and 806 bridges;
- 982 vertex-biconnected blocks under the explicit trivial-block convention:
  48 isolated singletons, 806 bridge blocks, and 128 blocks with at least three
  vertices; the largest has 660 vertices;
- 1,546 internal official-group edges and 779 cross-group edges; 473 groups
  have exactly one boundary node, 340 exactly one neighbouring group, and 343
  bridges cross group boundaries;
- parser coverage is 1,064 / 3,150 drawable stat occurrences (33.78%) and
  337 / 1,522 unique drawable lines (22.14%);
- representative supported profiles leave 80.49%–92.22% of drawable nodes at
  zero score.

The high score sparsity is critical context: query-specific pruning may remove
substantial structure partly because the current parser recognises few stat
families. Any measured speedup must therefore be reported with exact profile
weights and must not be generalized to richer future scoring.

Phase 1 status: **complete and reproducible**. The recovered tests, exact
solver, analyser, and quick harness all pass as of this checkpoint. Phase 2 was
then completed through the isolated experiment below without altering the
endpoint.

## Checkpoint 4: query-specific non-useful-leaf pruning

### Rule, guarantee, and limits

`prune_non_useful_leaves` repeatedly removes a node when it is unallocated,
not required, permitted by the special-node policy, currently degree zero or
one, and has production query score `<= 0`. A deterministic queue updates the
active degrees. The peel is `O(|V| + |E|)` after scores and the graph are
available; input canonicalisation and sorted immutable output add comparison-
sorting overhead.

**Algorithmic guarantee for the best Problem A objective.** Consider one
removal from the current reduced graph. A removable unallocated leaf cannot be
an internal vertex of a simple path: it can only be absent or be the endpoint.
If it is an endpoint, truncating it preserves traversability, removes one point
of cost, and subtracts a non-positive score. A negative leaf therefore raises
total score; a zero leaf preserves score and improves efficiency for a
positive-score candidate. Thus an equal-or-better path exists without that
leaf. Repeating the argument inductively proves that the best score-first,
efficiency-second objective is preserved after the whole peel.

That guarantee has deliberately narrow conditions:

| Complication | Treatment |
|---|---|
| At-most versus exact budget | Safe only for the current at-most budget. Removing a leaf can destroy an exactly-`B` path. |
| Zero versus negative score | Both are safe for the best objective; zero can remove a distinct equal-score extension, while negative strictly improves a truncated path. |
| Score/efficiency ranking | The proof uses score first, then efficiency. It is not a guarantee for a ranking that rewards length, target identity, or another path property. |
| Top-k and diversity | Not preserved. The adversarial-prefix fixture's exact and current top-10 path Jaccard is 0.5 after its zero-score extension disappears, even though the best objective is unchanged. |
| Allocated starts and fixed endpoints | Allocated nodes are always protected. The utility accepts `required_nodes`; the current API has no fixed endpoint, but any future one must be passed here. |
| Class starts | All class starts are protected. Production also refuses to traverse an unallocated class start. |
| Mastery/proxy/ascendancy/multiple-choice | Production graph construction already excludes these; the benchmark policy protects any defensively encountered instance. |
| Jewels and keystones | Retained jewel/expansion sockets and all keystones are protected because their semantic value is not represented by additive `StatScorer` output. |
| Prefix response expectations | A zero-score terminal extension may vanish. The endpoint contract is unchanged, and pruning is not connected to the endpoint. |

The conservative benchmark policy protects 73 special nodes in the current
1,997-node production adjacency: seven class starts, 45 keystones, and 21 jewel
or expansion sockets. This is stricter than the literal mathematical proof,
which could remove a zero-score keystone or socket under today's simplified
objective, but it avoids claiming safety for game semantics the scorer ignores.
The graph builder omits another 48 drawable zero-degree raw nodes entirely, so
they are analysed structurally but are not inputs to production search or this
benchmark peel.

### Experimental implementation and benchmark profiles

The utility lives under `poe_pathing.research`; no container, route, or frontend
code enables it. The harness scores the full query graph, peels it, then builds
a reduced `PathFinder` while preserving the original order among retained
neighbours. This prevents bounded LIFO differences from being attributed to
an accidental adjacency sort. Preprocessing time includes query-wide scoring,
the queue peel, special-node inspection, and reduced-graph materialisation.
Search time is measured separately; total time is their sum.

Every listed key has weight `1.0`:

| Profile | Production-supported desired-stat keys |
|---|---|
| `strength` | `strength/flat` |
| `flat_life` | `maximum_life/flat` |
| `attributes` | `strength/flat`, `dexterity/flat`, `intelligence/flat` |
| `elemental_caster` | `fire_damage/increased_percent`, `cold_damage/increased_percent`, `lightning_damage/increased_percent`, `cast_speed/increased_percent`, `maximum_mana/increased_percent`, `intelligence/flat` |
| `life_and_resistances` | `maximum_life/flat`, `maximum_life/increased_percent`, `fire_resistance/flat_percent`, `cold_resistance/flat_percent`, `lightning_resistance/flat_percent`, `chaos_resistance/flat_percent` |
| `physical_attacker` | `physical_damage/increased_percent`, `attack_speed/increased_percent`, `strength/flat`, `dexterity/flat` |

Minion, projectile/bow, armour, and evasion profiles were not invented because
the current parser does not expose those stat keys. Mana is represented in the
caster profile; energy shield is parseable but was not added merely to enlarge
the bounded matrix. The high zero-score rates in the structural report are a
parser/scorer property, not evidence that those game nodes lack value.

The faithful current-search probe reports seeded, generated, expanded, and
processed states plus dominance rejections/evictions, bucket-width
rejections/evictions, states processed after bucket eviction, cap discard, and
candidate-pool discard. These categories explain bounded retention and are not
collapsed into one potentially double-counted `pruned_states` number. Exact
enumeration separately reports structural visited, traversal, and budget
rejections and their non-overlapping total.

### Measured pruning results

The final full matrix ran 151 strategy/scenario combinations over 18 scenarios;
all returned paths were valid. Seventeen exact baseline/pruned pairs completed
and had **zero best-objective mismatches**. Both Templar life/resistance
budget-20 exact runs hit the explicit 100,000-state limit, so that scenario has
no exact claim. The bounded current search happened to return the same best
objective before and after pruning in all 18 scenarios, but this observation is
heuristic evidence, not a proof about all capped searches.

Current-search generated-state count fell in 9/18 scenarios and search-only
median time fell in 13/18. Once query-wide preprocessing was charged, total
time improved in only 1/18: Scion flat-life at budget 20. The table below is the
fixed-hash-seed, one-repeat, no-`tracemalloc` full run; milliseconds are local to
this machine and should not be treated as stable performance constants.

| Scenario | Nodes removed | Current generated states | Search ms, base → pruned | Preprocess ms | Pruned total ms | Best score, base/pruned | Exact preservation |
|---|---:|---:|---:|---:|---:|---:|---|
| Branch-heavy synthetic | 8/12 (66.67%) | 12 → 4 | 0.088 → 0.054 | 0.472 | 0.526 | 20 / 20 | Yes |
| No-useful-node synthetic | 5/6 (83.33%) | 6 → 1 | 0.045 → 0.029 | 0.126 | 0.155 | none / none | Yes |
| Scion flat life, budget 5 | 608/1,997 (30.45%) | 94 → 85 | 1.033 → 0.891 | 25.106 | 25.996 | 12 / 12 | Yes |
| Scion flat life, budget 10 | 608/1,997 (30.45%) | 932 → 698 | 11.541 → 9.059 | 26.950 | 36.009 | 37 / 37 | Yes |
| Scion flat life, budget 20 | 608/1,997 (30.45%) | 40,374 → 31,464 | 329.766 → 272.547 | 34.197 | 306.744 | 62 / 62 | Yes |
| Marauder physical, budget 5 | 487/1,997 (24.39%) | 28 → 28 | 0.581 → 0.564 | 24.619 | 25.183 | 96 / 96 | Yes |
| Witch elemental, budget 10 | 542/1,997 (27.14%) | 1,083 → 937 | 20.755 → 21.464 | 24.462 | 45.926 | 130 / 130 | Yes |
| Ranger attributes, budget 15 | 583/1,997 (29.19%) | 8,626 → 6,698 | 74.394 → 67.719 | 39.540 | 107.259 | 180 / 180 | Yes |
| Templar life/resistance, budget 20 | 572/1,997 (28.64%) | 55,085 → 46,565 | 450.922 → 428.248 | 32.500 | 460.748 | 126 / 126 | Not established; both exact runs capped |

The committed quick report uses three timed repeats and an additional
`tracemalloc` run. It has 76 valid runs, 14/14 exact pruning matches, no current
best-objective changes, and no total-time wins. For example, Witch budget 10
falls from 1,083 to 937 generated states and from 34.103 ms to 24.686 ms of
search, but 37.294 ms of preprocessing makes the pruned total 61.980 ms.
Preprocessing peak traced memory for the three quick real-tree queries is
approximately 0.95 MB; separate search peaks are present in the JSON.

Eight full scenarios remove nothing, including the positive cycle and the
visited-set adversary. The branch-heavy fixture proves that repeated peeling
can remove several rounds and reduce both exact and current generated states.
The all-zero fixture peels every unallocated tree node. A zero-score connector
to a valuable node, a positive leaf, an allocated leaf, required starts, and
cycles are retained by their respective tests.

### Current-search parameter sweep

The bounded sweep answers two specific questions rather than exploring every
combination. On the visited-set adversary, width 1 and width 3 return score 101,
while width 4 and width 8 recover the exact score 200; priority width 3 also
misses and priority width 4 recovers it. A cap-four pair differing only in start
adjacency returns scores 3 and 50, confirming LIFO order sensitivity. On sampled
real Scion budget 20, width 1/3/8 all return exact score 62, but generated states
rise from 15,037 to 40,374 to 67,690. This sample does not make width 1 safe.
Leaf pruning reduces default-width states on that query by 22.1%, but its value
is not equivalent to simply widening the beam and does not cure the visited-set
counterexample.

## Narrow groups and candidate clusters

**Measured facts.** The filtered graph has 806 bridges, 687 articulation points,
and 982 vertex-biconnected blocks under the documented block-cut convention.
The 660-vertex largest block shows that much of the tree is one cyclic core,
while 806 two-vertex bridge blocks expose many tree-like appendages.

Official export groups are often, but not universally, narrow. Across 652
retained groups, the median boundary-node ratio is 0.3333 and the median defined
edge-boundary ratio is 0.5. There are 473 groups with exactly one boundary node,
340 with exactly one neighbouring group, 445 incident to a boundary bridge,
and 343 cross-group bridges. Group 551 (`Fusillade`) is a representative narrow
region: 12 retained nodes, 12 internal edges, and one boundary node/edge. Group
519 is the counterexample: four nodes, all boundary nodes, and 18 boundary
edges. The analysis also records total/mean/median/minimum/maximum node scores
for every retained group and four profiles; only 63 groups contain any positive
life/resistance node, versus 207 for physical attacker.

**Interpretation, not a guarantee.** These measurements justify further
entry/exit-aware cluster research, but official visual groups are not a valid
automatic contraction. Wide groups exist, and the 660-node biconnected core
cannot be decomposed by articulation points alone. Contracting a branching
same-value group to one weighted node could credit more reward than any simple
path can collect. Any exact group summary would need at least best score by
entry boundary, exit boundary, and point cost. Restricted leaf-group peeling is
plausible only for a single attachment relation, no positive unallocated node,
and no allocated/required node; it was not implemented because node-level proof
does not automatically establish safety for an arbitrary multi-node group.

## Earlier pruning recommendation and limitations (superseded)

### Earlier recommendation: continue research before integration

Keep the current bounded search and its single objective-aware prefix correction.
Do **not** enable leaf pruning in production yet. The best-objective proof and
17 exact pairs support the restricted rule, but top-k identity is not preserved,
special PoE semantics require conservative exclusions, and full-graph scoring
dominates practical low-budget searches. Only one of 18 full scenarios improved
total time, so the present evidence does not satisfy the bar for production
integration. The API response contract remains unchanged and no pruning flag is
registered in the application container.

Limitations of these conclusions:

- the exact solver is exponential and deliberately capped; one full real-tree
  scenario has no exact result;
- the real-tree matrix samples seven class/profile/budget combinations rather
  than estimating all query distributions;
- timings and traced memory are machine-local and the full run has one timing
  repeat; the committed quick run provides repeat determinism, not universal
  performance stability;
- the parser recognises only 33.78% of retained stat-line occurrences, which
  makes current query scores unusually sparse;
- the production search remains approximate, adjacency/order/cap sensitive,
  and capped multi-start seed order depends on `PYTHONHASHSEED`;
- the pruning proof preserves only the best current objective, not exact-budget
  feasibility, candidate identity, target preferences, or top-k diversity;
- cluster metrics are descriptive and do not prove a safe contraction or a
  globally optimal cluster-level algorithm.

The cached score-vector follow-on proposed here has now been completed in the
section below. Its final production decision supersedes this earlier next-step
statement; no broader algorithm experiment is proposed by the follow-on.

## Reproduction and validation for the original pruning phase

Run from the repository root with the project's Python environment:

```powershell
$env:PYTHONHASHSEED = '0'
python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/analyse_tree_data.py
python scripts/analyse_tree_data.py --format json --output "$env:TEMP\poe-tree-analysis.json"
python scripts/benchmark_pathing.py --quick --repeats 3 --output docs/pathing-benchmark-results.json
python scripts/benchmark_pathing.py --full --no-memory --repeats 1 --output "$env:TEMP\poe-pathing-full.json"
cd frontend
npm run lint
npm run build
cd ..
git diff --check
```

Final results on 2026-08-01:

- Python: 49/49 standard-library tests passed; `compileall` passed.
- Analysis: two Markdown generations and two JSON generations were byte-identical;
  the final Markdown SHA-256 is
  `d3bc8fcf8c922f073ff201f9453f910c6563ce4758495bdad8e8272cd6b4a0ec`.
- Quick benchmark: 14 scenarios, 76 runs, zero invalid paths, zero exact
  pruning mismatches, and deterministic fingerprints for every repeated run.
- Full benchmark: 18 scenarios, 151 runs, zero invalid paths, 17 completed
  exact pruning comparisons with zero mismatches, and two expected exact-cap
  records for the same Templar scenario.
- Frontend: ESLint passed and the Vite production build passed. The first
  sandboxed build attempt could not spawn Vite's helper (`EPERM`); the approved
  rerun outside that process restriction succeeded.
- Repository checks: `git diff --check` passed. The pre-existing user-owned
  `.gitignore` modification was not changed by this work.

There is no configured Python formatter or linter in this repository; syntax
compilation, the standard-library test suite, deterministic generator checks,
path-by-path benchmark validation, frontend lint/build, and `git diff --check`
form the documented minimal validation workflow for this run.

## Cached per-node score-vector experiment

### Checkpoint 1: resumed scope and uncached baseline

This follow-on is deliberately limited to the cached scoring experiment named
above. It will not add a pathfinding algorithm, alter group/cluster search, or
change frontend behaviour. The completed exact, pruning, structural, and
benchmark work remains the baseline rather than being replaced.

Resume inspection on 2026-08-01 reconfirmed branch `main` at `1671c31`, the
user-owned `.gitignore` modification, and all recovered research artefacts.
`StatScorer.score_node` currently reparses every raw stat line the first time a
node is encountered in each search. Query-wide pruning reparses every retained
node before search, while `PathEvaluator.stats_gained` parses returned-path
lines again to produce the unchanged API detail fields. The existing scorer is
therefore retained as the equivalence oracle.

Baseline command and result:

```text
$env:PYTHONHASHSEED = '0'
python -m unittest discover -s tests -v
```

Result: **49/49 tests passed** before cached scoring changes. The next concrete
step is an immutable per-node tuple of successfully parsed `ParsedStat`
contributions, preserving raw-line order and one multiply/add per parsed line
so duplicate keys and floating-point accumulation retain current semantics.

### Checkpoint 2: cached-scoring equivalence

The cache is implemented as a separate `CachedStatScorer`; the original
`StatScorer` remains unchanged as the uncached oracle. Each vector is built
only by calling the existing parser and stores an ordered tuple of complete
`ParsedStat` values. It does not aggregate duplicate keys, reinterpret signs,
drop the `unknown` modifier, or discard raw text. `PathEvaluator.stats_gained`
uses those same cached parsed values when the scorer exposes them and retains
its original parsing fallback for existing and synthetic scorers. Cache misses
still resolve through `NodeLookup.get`, preserving the existing missing-node
`ValueError`.

The equivalence matrix covers all **2,045 drawable nodes**, all **1,064
supported parsed contributions**, and all **38 observed supported stat keys**
in the current export. Exact numeric equality held for all six representative
profiles and 12 fixed-seed generated weight maps: all-zero, all-positive,
all-negative, alternating positive/zero/negative, and eight sparse
combinations drawn from `-3.0`, `-1.25`, `0.0`, `0.125`, `0.5`, and `2.75`.
Focused fixtures also cover duplicate parsed keys,
unsupported lines, `unknown` modifiers, negative parsed values, raw-line order,
zero and negative desired weights, `stats_gained` payload equality, and
missing-node exception parity. A counting parser proves repeated node scoring
and response stat aggregation perform no parses after eager materialisation.

Commands and results:

```text
C:\Users\BrendanHe\AppData\Local\Python\bin\python.exe -m unittest tests.test_cached_stat_scorer tests.test_stat_scoring tests.test_api_contract -v
```

Result: **13/13 focused tests passed**, including exact cached-versus-uncached
`PathEvaluator.stats_gained` API-detail equality. This checkpoint establishes
semantic equivalence of the score vectors, but it does not yet justify
production selection. The existing `StatScorer` remains the oracle and the
production container has not switched to `CachedStatScorer` pending the
four-case benchmark measurements. No production recommendation is made at this
checkpoint.

### Checkpoint 3: authoritative four-case benchmark and measured gate

The existing shared harness ran the required current-search cross-product on
every scenario:

1. uncached scoring without pruning (`U`);
2. cached scoring without pruning (`C`);
3. uncached scoring with pruning (`UP`);
4. cached scoring with pruning (`CP`).

The baseline explicitly constructs the original `StatScorer`, independently of
the production container default. Real-tree cached runs reuse one eagerly built
`CachedStatScorer`; its startup construction is outside per-query totals.
Synthetic fixed-score fixtures emit the same four labels as controls but mark
the parsed-vector cache inapplicable. Cached and uncached variants are asserted
to return identical complete response payloads and current-search state
counters within each pruning setting. Their pruning diagnostics and reduced-
graph fingerprints must also match exactly.

Search-node scoring is measured in separate output-equivalent instrumented
runs so clock-read overhead does not distort normal search samples. It remains
a component of inclusive search time and is not added to it again. Query-wide
pruning scoring, leaf peeling/reduced-graph materialisation, search, and paired
total wall time are recorded separately. Fixed adjacency, scenario order, and
`PYTHONHASHSEED=0` retain deterministic bounded-search inputs.

Authoritative commands:

```powershell
$env:PYTHONHASHSEED = '0'
python scripts/benchmark_pathing.py --quick --repeats 4 --output docs/pathing-benchmark-results.json
python scripts/benchmark_pathing.py --full --no-memory --repeats 4 --output "$env:TEMP\poe-cache-full-final.json"
```

The quick run included traced-memory measurements; the full run omitted them to
make four timing repeats practical. Results:

- quick: **14 scenarios / 104 runs**, zero invalid paths, zero cache-
  equivalence failures, 14 completed exact pruning pairs with zero optimum
  mismatches, and zero deterministic-fingerprint failures;
- full: **18 scenarios / 187 runs**, zero invalid paths, zero cache-
  equivalence failures, 17 completed exact pruning pairs with zero optimum
  mismatches, and zero deterministic-fingerprint failures;
- the two full exact-cap records are the expected unpruned/pruned Templar
  life/resistance budget-20 runs; neither is presented as an exact result;
- every full real cached/uncached pair had identical objective, cost, validity,
  complete response, and state counters within its pruning setting;
- every full real cached pruned/unpruned pair happened to have the same top-k
  fingerprint. This is measured behaviour, not a general top-k guarantee.

The eager cache contained **2,622 node vectors** and **1,205 parsed
contributions**. Construction measured **31.1788 ms** in the quick process and
**20.4772 ms** in the full process. This variation is a machine-local startup
measurement; neither value is charged to steady-state query totals.

Full real-tree medians are milliseconds. Generated-state counts are identical
between cached and uncached scoring, so the final column shows the shared
unpruned-to-pruned comparison.

| Real scenario | Budget | U total | C total | UP total | CP total | Generated, base -> pruned | Best score / cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Scion flat life | 5 | 0.989 | 0.610 | 30.939 | 16.511 | 94 -> 85 | 12 / 1 |
| Scion flat life | 10 | 14.169 | 12.773 | 40.377 | 26.621 | 932 -> 698 | 37 / 9 |
| Scion flat life | 20 | 349.899 | 332.496 | 263.242 | 249.154 | 40,374 -> 31,464 | 62 / 18 |
| Marauder physical | 5 | 0.621 | 0.353 | 28.866 | 18.356 | 28 -> 28 | 96 / 5 |
| Witch elemental | 10 | 23.463 | 21.088 | 47.654 | 38.602 | 1,083 -> 937 | 130 / 10 |
| Ranger attributes | 15 | 71.843 | 72.179 | 89.652 | 86.279 | 8,626 -> 6,698 | 180 / 15 |
| Templar life/resistance | 20 | 519.797 | 516.572 | 451.380 | 436.894 | 55,085 -> 46,565 | 126 / 20 |

Cached unpruned search improved **6/7** full real scenarios and saved
**24.70995 ms** in aggregate. Ranger budget 15 regressed by 0.336 ms, which is
small relative to run-to-run timing variation and did not change any search
result or counter. In the quick matrix caching improved 2/3 real cases and
saved **0.7622 ms** in aggregate; Witch budget 10 regressed by 0.216 ms at the
same noise scale. These results support caching through semantic equivalence
and consistent scoring-work reduction, not a claim that every individual
wall-clock sample must improve.

For full-graph pruning preparation, uncached query scoring was approximately
**10.0-12.9 ms** per real scenario, versus **0.546-1.049 ms** with cached
vectors. Peeling and reduced-graph materialisation still cost approximately
**11.8-20.6 ms**. Caching therefore removes most raw parsing cost but cannot
make preprocessing free; low-budget queries remain dominated by graph-wide
materialisation.

#### Conditional-pruning gate

The research-only gate is deliberately cheap and uses only information known
before search:

```text
graph_node_count >= 1,000
and graph_node_count * point_budget >= 39,940
```

`39,940` is the current 1,997-node graph times budget 20. It was locked from
the prior full-matrix frontier before these authoritative reruns; it was not
chosen after observing their timings. The quick matrix is a subset of the full
matrix and contains no budget-20 query, so it tested only the disabled side and
is not independent validation. The decision itself cost **0.126211 microseconds**
per call in the quick 100,000-call microbenchmark and **0.170444 microseconds**
in the full one.

The harness also reports the observed threshold frontier plus a `never`
sentinel. Every lower threshold selected at least one regression and failed
the integration bar. The locked threshold selected only the two budget-20 real
queries:

| Selected query | C total | CP total | Saving | Relative saving | Objective/fingerprint/validity issue | Generated states increased |
|---|---:|---:|---:|---:|---|---|
| Scion flat life, budget 20 | 332.496 | 249.154 | 83.34195 ms | 25.1% | none | no |
| Templar life/resistance, budget 20 | 516.572 | 436.894 | 79.67855 ms | 15.4% | none | no |

The locked rule therefore saved **163.02050 ms** across its two selected rows
and satisfied the harness's measured validity, objective, fingerprint,
generated-state, and materiality checks. That is positive evidence for these
specific queries. It is not enough by itself to establish that a production
gate generalises.

### Checkpoint 4: final production decision

**Binary recommendation: integrate cached scoring only.** Keep non-useful-leaf
pruning research-only and disabled by default.

Cached scoring meets the production bar independently of pruning:

- all drawable-node/profile/generated-weight equivalence tests pass under the
  exact existing scoring semantics, including zero and negative weights;
- cached and uncached full responses, objectives, costs, path validity, and
  state counters match throughout both benchmark matrices;
- raw stat parsing is removed from repeated query scoring and response detail
  materialisation;
- six of seven full real cases improved, aggregate full and quick timings
  improved, and the remaining regressions were noise-scale;
- the frontend-facing response shape and endpoint behaviour are unchanged.

The production container has consequently been switched to the eager
`CachedStatScorer`, while retaining `uncached_stat_scorer` as the equivalence
oracle and compatibility path. `PathEvaluator` consumes cached `ParsedStat`
values when available and keeps its original fallback for uncached and
synthetic scorers. The original scorer has not been removed.

Conditional pruning is not integrated despite the two measured budget-20
wins. Its enabled side contains only two queries, the quick matrix exercises
only its disabled side, and the locked rule was rerun on the same scenario
family that produced the earlier frontier rather than on an independent
holdout. More importantly, `node_count * budget` cannot estimate the
profile-specific positive-score distribution, removable-leaf count, or graph
reduction before paying preprocessing cost. The low-threshold regressions show
that budget and graph size alone are an incomplete proxy. Enabling this gate
would therefore generalise beyond the evidence even though its two selected
rows passed the measured bar.

Current limitations remain explicit: timings are machine-local; parser coverage
is sparse and may change; exact enumeration remains capped on the Templar
budget-20 case; the bounded production search is still heuristic; and safe
leaf pruning preserves the best current objective under its stated conditions,
not arbitrary top-k identity or future endpoint semantics.

Final scoped validation includes **63/63 standard-library tests passing**, the
authoritative quick and full matrices above, zero cached/uncached response or
state-counter mismatches, zero invalid benchmark paths, and the unchanged API
contract tests. Pruning remains unreachable from the production endpoint and
its default gate value remains disabled.

This completes the cached per-node score-vector experiment with a production
decision. **No additional investigation is proposed.**
