# ISA suspect 1 — reduction scalar seed rejected (bug #7; root-caused, patched, gated)

Production integration: both trace-free guards applied on branch `fix/isa-reduction-guards` at base `91639161`. Integration commit decision and validation are recorded below. The investigation narrative below describes the earlier isolated A/B work.
Investigation: 2026-09-27. Target: 4 lanes, VLEN=2048. Baseline tree: `91639161bd06d61df475e697557461ffcd865361`. All paths below are relative to `/home/wenwu/isa_invest` unless stated otherwise. At the investigation stage, no production sources had changed and no commits had been made.

## Root cause and fix

`vredmaxu.vs v8,v8,v1` (`0x1a80a457`) is legal under e16,m4, but the dispatcher rejects it with mcause=2. The minimal ELF traps at `0x800000a4`, with vl=32, vtype=0xca, vstart=0. The original GCC14.2 kernel reports the same word at `0x800000b8`.

RVV 1.0 defines the scalar seed and scalar destination of a reduction as element zero of individual vector registers, independently of LMUL. The vector source remains a register group and retains its alignment requirements. [RVV 1.0, Vector Reduction Operations](https://github.com/riscvarchive/riscv-v-spec/blob/master/v-spec.adoc#vector-reduction-operations)

In `hardware/src/ara_dispatcher.sv`, the decoder initializes `lmul_vs1` from `csr_vtype_q.vlmul` (baseline line 382). OPMVV reduction decoding sets destination `ara_req.emul=LMUL_1` (line 1460), but does not similarly constrain the scalar seed. The source guard at line 1919 therefore evaluates:

```text
lmul_vs1 = LMUL_4 (encoding 2)
rs1 = 1; use_vs1 = 1
(rs1 & 0b00011) != 0  ->  illegal_insn |= 1
```

`rs2=8` is correctly m4-aligned, destination EMUL is already 1, vtype is legal, and SEW=16 is supported. This is specifically a seed-alignment bug. The suspected destination rejection is **exonerated** for these single-width integer reductions: `vredmaxu.vs v3,v8,v0` passes under m4.

Minimal proposal: [suspect1-seed-alignment.patch](/home/wenwu/isa_invest/integration_20260927/suspect1-seed-alignment.patch). Before the OPMVV alignment checks, add:

```systemverilog
if (ara_req.op inside {[VREDSUM:VWREDSUM]}) lmul_vs1 = LMUL_1;
```

This changes only the reduction seed's alignment class. It preserves the vector source guard and destination bookkeeping. The experimental change is placed in OPMVV and validates the eight single-width integer reductions; it does **not** claim to repair the separate OPIVV widening or OPFVV floating-point decode paths.

Minimal sources are `reduction.S`, `report.c`, `crt0.S`, and `link.ld`; ELF/disassembly are `elf/seed_m4.elf` and `.dis`. `run_repros.py` records the exact compiler flags and independent numerical oracle. The test initializes v8[i]=i+3 and seed=7; expected max at vl=32 is 34.

## Clock-correspondent witness

Numbers below are actual `%0t` simulation timestamps, not retirement counts. Baseline trace: `trace/seed_m4/sim.log`; fixed trace: `seed_only/seed_m4/sim.log`. Extracts are in `witnesses/`.

| Time | Baseline event | Architectural interpretation |
|---:|---|---|
| 436 | Accept e16,m4 vsetvli | Legal vtype; vl becomes 32. |
| 502 | Accept seed `vmv.s.x v1,t0` | Scalar seed instruction is accepted. |
| 506 | `insn=1a80a457 op=38 vs1=1 vs2=8 vd=8 lmul1=2 lmul2=2 emul=0 use1=1 illegal=1` | **First contradiction:** scalar seed is subjected to m4 source-group alignment. |
| 508 onward | No op=38 backend transfer for the rejected instruction | Rejection precedes the reduction unit. |
| 512 | Seed write to lane-0 VRF address 8, byte enable 3, low element 7 | Seed initialization itself is sound. |
| 618–626 | Trap handler reads vl/vtype/vstart | UART reports mcause=2 and the exact rejected word. |

Under the seed-only guard, the same time-506 event has `lmul1=0 lmul2=2 emul=0 illegal=0`. At 508 the reduction transfers to the backend, at 512 it reaches VALU, at 556 lane 0 writes scalar 34 to address 0x40 with byte enable 3, and at 562 scalar readback completes. `ISA_PASS got=34 vl=32 cycles=72 loops=1 complete=1` and SUCCESS follow; the host exit event is timestamped 2718.

The m1 control has the same correct data/result sequence without the guard. The m4/v3 destination control also passes without it. Thus neither a datapath error nor an ISA restriction explains the rejection.

## Investigation validation (before integration)

- Original gate binary: `seed_m1` PASS, `seed_m4` TRAP, `dest_m4` PASS. Trace-only binary agrees.
- Seed-only guard: all three PASS. Three selected aligned-seed loop deadlocks persist; this fix does not conceal suspect 2.
- Lifecycle-only guard: `seed_m4` still TRAP. The defects are independent.
- Combined guards: 15/15 core reproducers PASS on 4Lx2048.
- Expanded matrix: 36/36 expected outcomes on 4Lx2048: eight integer reduction operators × m2/m4/m8 using seed v1/destination v3; nine extra SEW8/32/64 max cases; three misaligned **vector-source** v9 cases correctly trap on `0x1a90a1d7` with mcause=2.
- The negative cases are counted as expected-trap successes, not numeric completion. Positive cases require both `complete=1` and simulator SUCCESS; rc=0 at the cycle cap never passes.
- `regression_matrix/summary.json`, individual `result.json`, raw `sim.log`, and `uart_normalized.log` preserve the results. `log_utils.py` reconstructs UART text interleaved with mock retirement events using the verified clock relation, while retaining raw evidence.
- Additional isolated VLEN512 evidence is preserved in `combined_v512/` and `regression_matrix_v512/`. This investigation stage covered patch proposals; the subsequent clean production campaign results are recorded below. Separate widening/floating-point decode paths remain outside this OPMVV patch.

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
