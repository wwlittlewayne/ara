# Ticket 1: mask destination alignment and EEW metadata

## Root cause and fix

`0x6ac8c157` at PC 0x8000248e is `vmsltu.vx v2,v12,a7`. The
source is an e64/m4 group, but the mask destination is one register. The
OPIVV/OPIVX/OPIVI alignment guards incorrectly require destination alignment
to source LMUL. Exempt integer compare/carry mask destinations from that
check; retain source alignment checks.

Removing only that guard exposes a second bug: EEW bookkeeping labels
`vd..vd+LMUL-1` as written, even though MASKU writes only vd. This corrupts
metadata for adjacent live registers. Limit the EEW update to one register
for compare/carry mask results. Do not change source-group EMUL, which is
still needed by source reshuffling.

## Clock-correspondent witness (guard-only experiment)

Case-130, VLEN=256, first mismatch:
`P4B_MISMATCH op=rsb case=761 offset=1028 row=18 component=0 got=65537 expected=1 injected=0`

Times below are simulation $time, not scalar retirement cycle numbers:

- 146792: VMSLTU id=0, vl=6, vd=2, vs2=12, source EW64,
  destination layout EW16, LMUL=4. EEW update incorrectly relabels v4/v5.
- 146804/146806/146808: MASKU consumes correct comparison slices at bit
  positions 0/2/4. The active comparison bits are 0x10.
- 146810: MASKU writes v2 with low byte 0xd0 (tail bits agnostic).
- 146834/146836: dispatcher injects unnecessary EW16-to-EW64 reshuffles
  of v4/v5, the count accumulator. The actual v4 data was already EW64.
- 146852: lane 0 VRF write, bank=1 addr=1, data=0x0000000000010001.
  This is the first corrupt write of the element later reported as row 18.
- 146882: VADD consumes b=0x0000000000010001, a=0, returning the same
  corrupt value. Comparison and addition arithmetic are not the cause.

Guard-only trace evidence:
`~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket1_trace/`
(case-130 log and 130/trace_hart_0.dasm). This experiment was not committed.
The experiment after ticket 3 produced 5 passes and 8 mismatches among the
13 witnesses, rather than the prior session's 6/7 split.

## Validation

All full suites used xargs -P 32. Removed hardware/build/verilator before
each VLEN build. Final binaries: /tmp/Vara_v128_t1final and
/tmp/Vara_v256_t1final. Build logs: /tmp/build_t1final_{128,256}.log.

Evidence root: ~/bbchain/.test-build/dispatch3-p4b/ara-results/

- ticket3_regress: 135 runner PASS + 13 VMSLTU traps per VLEN; no assertions.
- ticket1_final: 148 runner PASS per VLEN; zero traps/mismatches/assertions.
- The supplied runner treats a cycle-limit exit (rc=0) as PASS. Case-146
  exceeds 8,000,000 cycles on both configurations (also in old v256 baseline).
  It must not be counted as a completed test from that ledger alone.
- ticket1_final_extended: reran case-146 on the same final binaries with
  -c 12000000 and a 3600-second wall limit; both completed successfully:
  - v128: 11,693,937 cycles, BARE_PASS comparisons=16475 canaries=541 masks=41
  - v256: 10,387,047 cycles, BARE_PASS comparisons=16475 canaries=541 masks=49
- Combining completed full-suite tests and the two extended witnesses gives
  **148/148 completed BARE_PASS per VLEN**, with no debug assertion errors.

Recheck the evidence with:

    python3 scripts/verify_p4b_completion.py \
      ~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket1_final \
      ~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket1_final_extended

`ticket1_regress` is an incomplete, superseded experiment, not final evidence.

## Remaining non-gating defect

The generated vid/vand/vmseq mask probe still reports 0xbb at VLEN=128,
and P4B_INPLACE is wrong there. P4B_MASKBITS/P4B_INPLACE are non-fatal
informational probes; the gating masks use scalar-generated masks via vlm.
VLEN=256's 0xd5 versus 0x55 second byte differs only at tail bit 15 for VL=15.
These results do not establish general correctness of generated masks.
