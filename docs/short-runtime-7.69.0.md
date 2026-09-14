# Restore the existing SHORT correction in the deployed assembly

The observed WSL runtime on 2026-09-14 selected definition 7.60.0 and
Intraday 7.0.0 / strategy 1.3.0. Intraday 8.0.0 and its 1.4.0 strategy
were already installed and registered, but were not selected.

Definition 7.69.0 copies the observed 7.60.0 assembly and changes only
Intraday to the existing 8.0.0 implementation / 1.4.0 strategy. The Windows
copy of the Linux launcher now defaults to this definition. This fixes the
existing SHORT circuit; it introduces no new engine or execution capability.
Alert 3.10.0 retains the ASTS/NBIS scope and the existing Swing confirmation.

## Verification

- 74 focused tests passed, including the real ASTS SIP minute-bar replay
  from 2026-09-09 through the selected Intraday implementation, Alert,
  monitor rendering and the sound callback. Duplicate delivery sounds once.
- The 14:14 UTC bar, available after completion around 14:15 UTC, changes
  from WATCH to SHORT CONFIRMED with entry 64.255, invalidation 64.4156
  and objective 64.0140. Swing fields in this integration test are reconstructed.
- NBIS uses the same ASTS fixture relabeled to test allowed-symbol handling;
  it is not a historical NBIS replay. ASTN remains excluded from SHORT alerts.
- Full suite: 1607 passed, 4 skipped, 79.07% coverage. Ruff and Pyright passed.
- Read-only validation using the WSL interpreter successfully assembled 7.69.0
  from the Windows file. The installed v8 implementation, strategy adapter,
  exports, strategy artifact and confirmed monitor match Windows.

This validates the identified missed setup and delivery code, not profitability
or a guarantee that every bearish session produces an entry.

## Pending activation in WSL

The workspace explicitly requires approval before modifying WSL checkout files.
No WSL checkout files or running processes were changed by this fix.

The deployment requires only:

1. Add `configs/marketbot/7.69.0.yaml` to the WSL checkout.
2. Change the WSL launcher's default definition and help text from 7.60.0
   to 7.69.0 (two lines). Do not replace the whole launcher: the Windows
   working copy contains concurrent, unrelated dashboard edits.
3. Restart the existing runtime with the explicit 7.69.0 definition and
   verify Intraday readiness reports implementation 8.0.0, strategy 1.4.0,
   plus the existing confirmed monitor's subscription and enabled bell.

No dependency installation or engine source synchronization is needed.
Rollback selects 7.60.0 and restores the launcher's two version strings.
