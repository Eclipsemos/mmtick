# Support/Resistance Integration Assessment

## Current Decision

Do not connect the detector to `engine.py`, `live_futures.py`, or paper execution.
The H4 causal walk-forward is a research diagnostic only. V3 hold rate is 87.2%,
but mean forward return is -0.0096% before funding (-0.1496% after the 14 bps
round-trip assumption). Against the distance-neutral shifted placebo, the lift is
-0.20 percentage points with CMH p=0.735. The geometry control is also weak:
V3 hit@0.5 ATR is 37.3%, below the fixed-distance placebo's 53.2%.

## What Can Be Integrated

1. **Offline factor input.** Keep `scripts/import_mmtick_ohlcv.py` as the data
   boundary. Generate one causal H4 feature row per closed bar and align it to
   MMTICK `ResearchBar` timestamps. Candidate fields are signed nearest-level
   distance (ATR), zone width, `edge_score`, `log1p(n_events)`, stale score, and
   `p_stall`. Do not import the target project's stock-trained `p_touch`/`p_hold`
   models.
2. **Research-only overlay.** Test the feature as a low-weight gate on existing
   BTC/ETH factors: reduce exposure near an opposing level, or require a confirmed
   breakout before enabling a trend signal. Evaluate with next-bar-open fills,
   funding, overlap netting, and stress costs.
3. **Alternative signal family.** A support rejection and a resistance rejection
   can become long/short candidates, but only after a fresh, untouched holdout
   and a comparison against distance and volatility controls.

## Overlay Result

The first MMTICK integration test used the frozen six-bar BTC reversal score as the
baseline and replayed closed H4 signals at the next H4 open with realized funding.
The 1.0 ATR location gate reduced the reused-2026 loss from `-55.3%` to `-36.0%`
for BTC and from `-69.8%` to `-31.2%` for ETH. This is loss reduction, not positive
alpha: both remain negative under normal and 30 bps stress costs. No configuration
improved both assets in both development and confirmation while preserving acceptable
drawdown. The baseline itself is an IC candidate, not an approved executable strategy.

Detailed replay: `overlay/btc-eth-4h-reversal-support-resistance-overlay-v1.md`.

## Integration Boundary and Gates

The target repository is GPL-3.0. Until license compatibility is reviewed, use a
process-level adapter or reimplement only the documented feature contract; do not
copy detector source into MMTICK. A candidate must show positive net expectancy
after funding and costs, stable sign across BTC/ETH and calendar years, and a
statistically defensible placebo advantage before it can be frozen for forward
paper observation. The current report passes none of those promotion gates.
