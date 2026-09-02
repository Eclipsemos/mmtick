# BTC/ETH Support/Resistance V3 Walk-Forward

Research-only diagnostic; no execution integration or trading approval.

- Data: `{"BTCUSDT": {"bars": 14552, "first": "2020-01-01T08:00:00", "last": "2026-08-22T12:00:00"}, "ETHUSDT": {"bars": 14552, "first": "2020-01-01T08:00:00", "last": "2026-08-22T12:00:00"}}`
- Signal: closed `H4` bar; evaluation reads future bars only.
- Costs: `14` bps round trip; funding not included.
- Decision points: every `6` bars after `800` warmup.

## Overall (width <= 1.5 ATR)

| Detector | Levels | Touch | Decided | Hold | Net forward return | Positive net rate |
|---|---:|---:|---:|---:|---:|---:|
| V3_fusion | 18,960 | 46.6% | 7,489 | 87.2% | -0.150% | 49.5% |
| placebo_fixed | 27,468 | 40.1% | 10,191 | 88.3% | -0.207% | 50.1% |
| V3_shift | 18,367 | 47.7% | 7,480 | 87.2% | -0.093% | 49.6% |

## Placebo Comparisons

```json
[
  {
    "base": "placebo_fixed",
    "chal": "V3_fusion",
    "base_hold": 0.8825434206652929,
    "base_n": 10191,
    "chal_hold": 0.872079049272266,
    "chal_n": 7489,
    "lift_pp": -1.0464371393026917,
    "z": -2.101527849383978,
    "p_value": 0.035594655938438566,
    "base_edge_atr": -0.3682862037638563,
    "chal_edge_atr": -0.24683674074812242,
    "edge_lift": 0.12144946301573387
  },
  {
    "base": "V3_shift",
    "chal": "V3_fusion",
    "base_hold": 0.871524064171123,
    "base_n": 7480,
    "chal_hold": 0.872079049272266,
    "chal_n": 7489,
    "lift_pp": 0.05549851011429352,
    "z": 0.10155415330499735,
    "p_value": 0.9191105715900659,
    "base_edge_atr": -0.23061350064022185,
    "chal_edge_atr": -0.24683674074812242,
    "edge_lift": -0.016223240107900577
  }
]
```

## Distance-Neutral Comparison

The V3-vs-shift comparison controls for distance with six quantile strata; CMH p-values are diagnostic and do not account for overlapping events.

```json
{
  "base": "V3_shift",
  "chal": "V3_fusion",
  "kind": "all",
  "n_strata": 6,
  "base_hold_eq": 0.8681105983931308,
  "chal_hold_eq": 0.8661315834231623,
  "hold_lift_pp": -0.1979014969968329,
  "cmh_chi2": 0.11460557856707294,
  "cmh_p": 0.7349604653612761,
  "or_mh": 0.9820435841333504,
  "base_fwd_eq": 0.04530361628208088,
  "chal_fwd_eq": -0.0044841843887592145,
  "fwd_lift_pp": -0.04978780067084008,
  "base_edge_eq": -0.23485604864455492,
  "chal_edge_eq": -0.23827735707407297,
  "edge_lift": -0.003421308429518035
}
```

## Geometry

```json
{
  "V3_fusion": {
    "recall_hit_05": 0.37267941603252136,
    "recall_hit_10": 0.6035711516521007,
    "median_err_atr": 1.2307578519309161,
    "precision_hit_05": 0.1880042230959662
  },
  "V3_shift": {
    "recall_hit_05": 0.3265842362225789,
    "recall_hit_10": 0.5412478263777422,
    "median_err_atr": 1.3916413102957796,
    "precision_hit_05": 0.18961020036429874
  },
  "placebo_fixed": {
    "recall_hit_05": 0.5321912795624374,
    "recall_hit_10": 0.7270279639473076,
    "median_err_atr": 0.9077425730196489,
    "precision_hit_05": 0.18676277850589776
  }
}
```

## Limitations

- The target project's bundled probability models are not used.
- This is signal-quality evaluation, not a capital-aware portfolio replay.
- Funding, liquidation, market impact, and overlapping-level position netting are not modeled.
- The 14 bps adjustment covers two entry/exit fills only.

V3 support/resistance levels did not show statistically significant incremental hold-rate improvement over a distance-preserving placebo; net forward returns remain negative before funding.
