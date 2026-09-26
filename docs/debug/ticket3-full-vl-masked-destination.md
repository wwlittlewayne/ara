# Ticket 3: full-VL masked destination layout

The dispatcher skipped the destination reshuffle whenever VL covered the full
register group. A masked write is not a full overwrite: inactive elements must
remain in the destination's new EEW layout. Require vm=1 for that optimization.

Witness: dispatch3-p4b/elf/case-000.elf, VLEN=128, two lanes.
At PC 0x80001694 a whole-register load restores v1 with EW8 layout. At
0x800016d0, `vadd.vv v1,v2,v3,v0.t` uses EW16 with VL=8. The old condition
skipped reshuffling v1 because VL=VLMAX. The inactive odd elements consequently
read 0xfbfb (-1029) instead of 0xfb2e (-1234). VLEN=256 uses VL=15 < VLMAX=16
and already reshuffles this destination.

Baseline: `P4B_MASK lane=1 vl=8 got=-1029 expected=-1234`.
Candidate: `P4B_OUTDUMP 3 -1234 5 -1234 7 -1234 9 -1234` and
`P4B_BARE_PASS comparisons=13364 canaries=52 masks=41 complete=1`.

P4B_MASKBITS is a separate informational store of the vid/vand/vmseq-generated
mask, not the mask used by this gate. The gate uses a scalar-generated mask
loaded with vlm. Its remaining generated-mask discrepancy is not fixed here.

Evidence on the test host:
- ~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket3_baseline/
- ~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket3_witness/
- ~/bbchain/.test-build/dispatch3-p4b/ara-results/ticket3_regress/

Clean rebuilds: hardware/make verilate config=2_lanes vlen=128 (and 256),
with build/verilator removed before each build. Archived binaries:
/tmp/Vara_v128_t3 and /tmp/Vara_v256_t3. Full-suite parallelism: xargs -P 32.
