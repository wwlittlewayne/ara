# ISA suspect 2 — reduction starts before older seed commits (bug #8; root-caused, patched, gated)

Production integration: both trace-free guards applied on branch `fix/isa-reduction-guards` at base `91639161`. Integration commit decision and validation are recorded below. The investigation narrative below describes the earlier isolated A/B work.
Investigation: 2026-09-27. Baseline 4LxVLEN2048, tree `91639161bd06d61df475e697557461ffcd865361`. Evidence root: `/home/wenwu/isa_invest`. Initial A/B RTL work was in the private `ara_copy`; production integration is recorded below.

## Root cause and fix

This is a true RTL dependency deadlock. LMUL=4/8 makes the producer long enough to expose it, but the defect is not specific to wide LMUL: a repeated short m1 reduction also reproduces it.

The VALU commit path (`hardware/src/lane/valu.sv`, baseline lines 780–807) starts a queued reduction whenever an older instruction commits and the next issue entry is a reduction. It fails to verify that **all** older instructions have committed. One instruction can finish issuing before its result has reached the VRF, so the issue and commit pointers need not identify the same instruction.

When the producer finishes, the seed `vmv.s.x` can still be the commit-head instruction while the reduction is already the issue-head instruction. Entering INTRA_LANE_REDUCTION suppresses ordinary VRF result requests (`valu.sv:741–745`). The queued seed result can no longer write back, its dependency remains asserted, and the reduction never receives its operands. The reduction cannot finish to restore writeback, completing the cycle of dependencies.

Minimal proposal: [suspect2-reduction-commit-head.patch](/home/wenwu/isa_invest/integration_20260927/suspect2-reduction-commit-head.patch). Preserve the existing condition and add:

```systemverilog
&& (vinsn_queue_d.commit_pnt == vinsn_queue_d.issue_pnt)
```

The pointers are checked **after** the current commit/issue updates, so reduction mode starts only when the reduction becomes the commit head. Older seed writeback remains enabled until then. The direct acceptance path already requires an empty commit queue before starting a newly accepted reduction; this fixes the queued-instruction path to obey the same principle.

A small triggering body is:

```asm
vsetvli s0,a0,e16,m4,ta,ma  # a0=-1 -> vl=512 on VLEN2048
vmv.v.i v8,9
li t0,7
vmv.s.x v0,t0
vredmaxu.vs v0,v8,v0
vmv.x.s s3,v0
```

`elf/loop_m4_vl-1_simple.elf` and the m8 counterpart check scalar 9 and repeat four times; the baseline deadlocks at the **first** reduction. No vector memory operations, mask instructions, or mixed-width operations are required. The source is `reduction.S` with SIMPLE=1, SEED=v0, DEST=v0, AVL=-1, LOOPS=4, LMUL=m4/m8. The non-simple tests use vid+add and independently check max(vl+2,7).

## Clock-correspondent witness

All table entries are `%0t` timestamps. The aligned-seed failing trace is from `seed_only_simple/loop_m4_vl-1_simple/sim.log`; its only behavioral change affects unaligned seeds and is inactive here. The untouched trace-only binary reproduces the same state transition (`simple/loop_m4_vl-1/sim.log`). The successful trace is `lifecycle_evidence/loop_m4_vl-1_simple/sim.log`.

| Time | Original lifecycle | With commit-head guard |
|---:|---|---|
| 470–530 | Constant-vector producer drains to the VRF. | Identical. |
| 532 | Producer's final beat writes address 0x5f. `cp_next=1`, `ip_next=2`: seed is older than reduction. **Incorrectly enters reduction mode.** | Same pointers, but guard holds normal mode. |
| 534 | Seed result cannot request the VRF in reduction mode. | Seed id=1 writes scalar 7 to v0, byte enable 3; commit pointer advances to reduction; only now enters reduction mode. |
| 544 | No reduction operands consumed. | Lane 0 consumes seed 7 and first source word of four 9s. |
| 546 | Other lanes wait on the seed dependency. | Other lanes begin, with their neutral seed values. |
| 606 | No progress. | Lane 0 consumes its 32nd/final intra-lane word (128 e16 elements). |
| 608 onward | No inter-lane transfer. | Three inter-lane exchanges per lane proceed. |
| 632–638 | No progress. | Lane 0 completes SIMD reduction and returns to normal mode. |
| 640 | No scalar result. | Lane 0 writes scalar result 9, byte enable 3. |
| 20,000–180,000 | Samples remain INTRA, first=1, remaining=128, operand-valid=0; lane 0 has two pending commits. | Test has already completed all four reductions. |

The unmodified short m1 case completes its first reduction at time 556, accepts its second at 596, and makes the same premature transition at 606. Thus LMUL is not the fundamental predicate.

Beat accounting, preserved in `beat_accounting.json`: failing aligned-seed m4/m8 traces consume **zero** reduction source beats and perform zero inter-lane transfers. Fixed four-loop m4 consumes 128 source words **per lane** (4×32); m8 consumes 256 (4×64). Both show 12 TX and 12 RX handshakes per lane (4 reductions × 3 exchanges). The numerical result is checked after every iteration.

Original workload correspondence:

| Workload | Seed/reduction accepted by lane 0 | First premature state transition | Last retired PC |
|---|---|---:|---|
| quant_v3, m4 | 68966 / 68968 | 69032 | 0x800000b8, word 0x1a802057 |
| candidate_v2, m8 | 68252 / 68254 | 68382 | 0x800000b8, word 0x1b002057 |

The next scalar readback at 0x800000bc is blocked. Retirement of a decoupled vector instruction indicates acceptance, not completion of its vector result. `.dasm` files close and flush at the cycle cap, so these last PCs replace the earlier buffered-watchdog approximation. Clock mapping is independently calibrated at the actual CVA6 Verilator commit site: `$time = 2*cycles + 20`, for example cycle=81/time=182. Tables use actual RTL timestamps, never unchecked conversion of retirement indices.

## Investigation validation (before integration)

- Baseline: all 12 non-simple loop cases at LMUL1/4/8 and vl=16/128/129/VLMAX reach 50,000 cycles without completion. Nine full-length single-reduction cases also remain incomplete. Extended simple m4/m8 runs at 100,000 cycles retain the same deadlocked state.
- Seed-only guard leaves this defect unchanged. Lifecycle-only guard fixes all 12 loop cases and leaves the unrelated seed rejection intact.
- Four-loop corrected runtimes (rdcycle interval, not `$time`): constant-vector m4=416, m8=672; vid+add m4=1321, m8=2473. Every result is exact and all tests emit complete=1 plus SUCCESS.
- Both original frozen kernel ELFs reproduce the deadlock with the trace-only binary at a 100,000-cycle cap. The lifecycle-only guard then allows both to finish with `maxerr=0`, `double_maxerr_x1e6=0`, `numeric_pass=1`, and simulator SUCCESS.
- Corrected quant_v3: kernel interval 9695 cycles; whole simulation 0x3e4ab = 255147 cycles. Corrected candidate_v2: kernel interval 9098 cycles; whole simulation 0x3e045 = 254021 cycles. Logs: `lifecycle_extended/`; recorded wall times about 108 and 117 seconds.
- Their first fixed reruns at a 150,000-cycle cap were correctly marked INCOMPLETE despite continuing scalar-checker retirement. The 1,000,000-cycle reruns establish actual completion. This separates the original RTL deadlock from a later, genuine harness-budget limit.
- Combined guards pass 15/15 core reproducers plus 36/36 expanded expected outcomes. Additional isolated VLEN512 evidence is preserved in `combined_v512/` and `regression_matrix_v512/`.
- Regression requirements: back-to-back reductions, reduction after a long vector producer plus scalar seed move, overlapping/nonoverlapping seed/destination, LMUL1/2/4/8, short/tail/full vl, multiple SEWs, and exact completion/beat counts. Preserve source-alignment rejection tests. Add an assertion that a transition into reduction mode cannot strand an older commit entry.
- These results describe the isolated investigation. The subsequent clean production campaign, completion-checker results, and focused reruns are recorded below.

## Production integration — 2026-09-27

Status: **root-caused, patched, gated**.

Production validation completed 2026-09-27T07:55:36.223143+00:00.

| Configuration | Completed P4B cases | Ledger prefix |
|---|---:|---|
| 4Lx2048 | 148/148 | `p1isa_4l2048` |
| 2Lx128 | 148/148 | `p1isa_v128_2l` |
| 2Lx256 | 148/148 | `p1isa_v256_2l` |

Focused production-binary checks: **55/55** (15 core reduction probes, 36 matrix outcomes including three intentional vector-source traps, two simplified deadlock probes, and two original kernels).

- `kernel_quant_v3`: **PASS**. `KERNEL=block_quant cycles=9695 maxerr=0 double_maxerr_x1e6=0 numeric_pass=1 variant=3`; `Executed cycles:  3e4ab`
- `kernel_candidate_v2`: **PASS**. `KERNEL=block_quant cycles=9098 maxerr=0 double_maxerr_x1e6=0 numeric_pass=1 variant=2`; `Executed cycles:  3e045`

Case-146 at **16,000,000 cycles**:

- `p1isa_4l2048`: **PASS**, rc=0, complete=True, host_success=True, cycle_timeout=False, executed_cycles=14798356, last_retired_cycle=14798344; `P4B_ENV vlen=2048`; `P4B_CSR vstart=0 vtype=8000000000000000 vlenb=256`; `P4B_BARE_PASS comparisons=16475 canaries=541 masks=49 complete=1`; `Executed cycles:  e1ce14`
- `p1isa_v128_2l`: **PASS**, rc=0, complete=True, host_success=True, cycle_timeout=False, executed_cycles=11693937, last_retired_cycle=11693927; `P4B_ENV vlen=128`; `P4B_CSR vstart=0 vtype=8000000000000000 vlenb=16`; `P4B_BARE_PASS comparisons=16475 canaries=541 masks=41 complete=1`; `Executed cycles:  b26f71`
- `p1isa_v256_2l`: **PASS**, rc=0, complete=True, host_success=True, cycle_timeout=False, executed_cycles=10387047, last_retired_cycle=10387037; `P4B_ENV vlen=256`; `P4B_CSR vstart=0 vtype=8000000000000000 vlenb=32`; `P4B_BARE_PASS comparisons=16475 canaries=541 masks=49 complete=1`; `Executed cycles:  9e7e67`

The patched 4Lx2048 build completes case-146 within the requested 16M-cycle budget. The prior production gate did not complete this case. This is the combined-guard result; it is not a fresh commit-guard-only A/B test.

The existing `scripts/verify_p4b_completion.py` was run against canonical-label symlink views of the v128/v256 logs. Return code: **0**. Its printed 12M-cycle wording is a historical constant; these preserved commands use 16M for case-146.

Evidence: `/home/wenwu/isa_invest/integration_20260927/campaign-summary.json`, `focused-summary.json`, `repository-completion-check.log`, `production-guards.diff`, the per-config `build.sha256`, `build.log`, `driver.log`, `summary.log`, and per-case `result.json`/raw simulation logs. Build directories are unique per configuration, runtime VLEN is verified, and no trace defines were enabled.

All requested gates are green. Two separate local commits are authorized; no push.
