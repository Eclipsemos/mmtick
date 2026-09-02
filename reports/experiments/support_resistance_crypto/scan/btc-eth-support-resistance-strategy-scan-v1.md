# BTC/ETH Support/Resistance Strategy Scan

Research-only causal H4 scan; no paper/live execution integration.

- Data: `{"btc_perp": {"bars_15m": 232836, "bars_h4": 14552, "first": "2020-01-01T00:00:00+00:00", "funding_events": 7277, "last": "2026-08-22T04:00:00+00:00"}, "eth_perp": {"bars_15m": 232836, "bars_h4": 14552, "first": "2020-01-01T00:00:00+00:00", "funding_events": 7277, "last": "2026-08-22T04:00:00+00:00"}}`
- Strategies: `298` across `breakout, breakout_confirm, factor, factor_location, fade, fade_confirm`.
- Selection uses 2024–2025 validation; 2026 confirmation is reported separately.
- Funding included; normal costs are 14 bps round trip and stress costs are 30 bps.

## Validation Leaders

| Name | Asset | Family | Validation return | Parameters |
|---|---|---|---:|---|
| fade_confirm_btc_perp_l2_h18_th0.0025 | btc_perp | fade_confirm | 184.20% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 18, "location_cap": 2.0, "threshold": 0.0025}` |
| fade_confirm_btc_perp_l1_h18_th0.0025 | btc_perp | fade_confirm | 130.25% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 18, "location_cap": 1.0, "threshold": 0.0025}` |
| fade_eth_perp_l1_e2.5_h18 | eth_perp | fade | 88.17% | `{"asset": "eth_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 18, "location_cap": 1.0, "threshold": 0.0}` |
| fade_confirm_btc_perp_l1_h18_th0 | btc_perp | fade_confirm | 81.73% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 18, "location_cap": 1.0, "threshold": 0.0}` |
| breakout_eth_perp_b0_h18 | eth_perp | breakout | 77.59% | `{"asset": "eth_perp", "buffer_atr": 0.0, "factor_confirm": false, "hold_bars": 18, "threshold": 0.0}` |
| fade_confirm_btc_perp_l1_h24_th0 | btc_perp | fade_confirm | 73.60% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 24, "location_cap": 1.0, "threshold": 0.0}` |
| fade_confirm_btc_perp_l2_h18_th0 | btc_perp | fade_confirm | 67.37% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 18, "location_cap": 2.0, "threshold": 0.0}` |
| fade_btc_perp_l0.75_e0_h12 | btc_perp | fade | 66.51% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": false, "hold_bars": 12, "location_cap": 0.75, "threshold": 0.0}` |
| breakout_eth_perp_b0_h12 | eth_perp | breakout | 62.71% | `{"asset": "eth_perp", "buffer_atr": 0.0, "factor_confirm": false, "hold_bars": 12, "threshold": 0.0}` |
| breakout_eth_perp_b0_h3 | eth_perp | breakout | 52.60% | `{"asset": "eth_perp", "buffer_atr": 0.0, "factor_confirm": false, "hold_bars": 3, "threshold": 0.0}` |
| fade_confirm_btc_perp_l1.5_h24_th0 | btc_perp | fade_confirm | 49.35% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": true, "hold_bars": 24, "location_cap": 1.5, "threshold": 0.0}` |
| fade_btc_perp_l2_e2.5_h12 | btc_perp | fade | 49.35% | `{"asset": "btc_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 12, "location_cap": 2.0, "threshold": 0.0}` |
| fade_eth_perp_l1_e2_h24 | eth_perp | fade | 47.32% | `{"asset": "eth_perp", "edge_floor": 2.0, "factor_confirm": false, "hold_bars": 24, "location_cap": 1.0, "threshold": 0.0}` |
| fade_btc_perp_l2_e2.5_h6 | btc_perp | fade | 42.44% | `{"asset": "btc_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 6, "location_cap": 2.0, "threshold": 0.0}` |
| fade_btc_perp_l1_e2.5_h12 | btc_perp | fade | 40.96% | `{"asset": "btc_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 12, "location_cap": 1.0, "threshold": 0.0}` |
| fade_btc_perp_l0.75_e0_h18 | btc_perp | fade | 39.70% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": false, "hold_bars": 18, "location_cap": 0.75, "threshold": 0.0}` |
| fade_eth_perp_l1_e2.5_h24 | eth_perp | fade | 39.01% | `{"asset": "eth_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 24, "location_cap": 1.0, "threshold": 0.0}` |
| fade_btc_perp_l1_e2.5_h24 | btc_perp | fade | 37.19% | `{"asset": "btc_perp", "edge_floor": 2.5, "factor_confirm": false, "hold_bars": 24, "location_cap": 1.0, "threshold": 0.0}` |
| fade_btc_perp_l1_e0_h12 | btc_perp | fade | 34.39% | `{"asset": "btc_perp", "edge_floor": 0.0, "factor_confirm": false, "hold_bars": 12, "location_cap": 1.0, "threshold": 0.0}` |
| breakout_eth_perp_b0_h24 | eth_perp | breakout | 25.27% | `{"asset": "eth_perp", "buffer_atr": 0.0, "factor_confirm": false, "hold_bars": 24, "threshold": 0.0}` |

## Confirmation of Validation Leaders

| Name | Asset | 2026 return | 2026 stress |
|---|---|---:|---:|
| fade_confirm_btc_perp_l2_h18_th0.0025 | btc_perp | -17.91% | -25.70% |
| fade_confirm_btc_perp_l1_h18_th0.0025 | btc_perp | -33.85% | -38.88% |
| fade_eth_perp_l1_e2.5_h18 | eth_perp | 30.22% | 25.31% |
| fade_confirm_btc_perp_l1_h18_th0 | btc_perp | -47.15% | -51.57% |
| breakout_eth_perp_b0_h18 | eth_perp | 27.14% | 21.79% |
| fade_confirm_btc_perp_l1_h24_th0 | btc_perp | -30.36% | -34.95% |
| fade_confirm_btc_perp_l2_h18_th0 | btc_perp | -1.06% | -10.54% |
| fade_btc_perp_l0.75_e0_h12 | btc_perp | -35.37% | -41.91% |
| breakout_eth_perp_b0_h12 | eth_perp | 22.42% | 16.87% |
| breakout_eth_perp_b0_h3 | eth_perp | 0.69% | -4.97% |
| fade_confirm_btc_perp_l1.5_h24_th0 | btc_perp | 6.09% | -1.94% |
| fade_btc_perp_l2_e2.5_h12 | btc_perp | 4.54% | -1.94% |
| fade_eth_perp_l1_e2_h24 | eth_perp | -50.60% | -53.29% |
| fade_btc_perp_l2_e2.5_h6 | btc_perp | -5.64% | -12.77% |
| fade_btc_perp_l1_e2.5_h12 | btc_perp | -8.83% | -13.26% |
| fade_btc_perp_l0.75_e0_h18 | btc_perp | -44.17% | -48.76% |
| fade_eth_perp_l1_e2.5_h24 | eth_perp | -12.75% | -15.53% |
| fade_btc_perp_l1_e2.5_h24 | btc_perp | -40.84% | -43.13% |
| fade_btc_perp_l1_e0_h12 | btc_perp | 40.48% | 23.65% |
| breakout_eth_perp_b0_h24 | eth_perp | 21.07% | 16.50% |

The confirmation table above is intentionally limited to candidates ranked on validation. No result grants trading approval; the full per-asset results and stress metrics are in JSON.

