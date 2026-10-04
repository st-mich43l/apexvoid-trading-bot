# XAU fixed-RR policy

XAU non-scalp technique plans use the configured fixed-RR instrument policy:

| Layer | Behavior |
| --- | --- |
| Stop | Structure-derived stop constrained by the configured instrument envelope |
| Targets | 1R / 2R / 3R / 4R with configured close ratios |
| Break-even | After the configured first target using the group weighted fill |
| Fallback | Single-target close when the configured opposing room cannot hold the ladder |
| Pack | `instrument_packs.xau_fixed_4r_v1` |

M1 scalps retain their own configured 1R/2R execution ladder. Analysis Engine
provides the setup; Algo Bot selects the applicable execution policy based on
the opportunity strategy and instrument.

The active settings are in `config/instruments.yml` and the resolved
configuration artifacts. The execution-policy implementation is in
`algo-bot/app/autotrade/execution_policy.py`.
