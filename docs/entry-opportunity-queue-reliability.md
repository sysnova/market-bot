# Entry Opportunity Queue Recovery

The September 30 incident began with PostgreSQL aborting a COMMIT after an
interrupted WAL write. Database recovery completed approximately 48 seconds later.
The original queue worker did not observe failures in claim/commit and could die
while the parent process continued receiving inputs and advertising readiness.

The corrected writer holds a transaction-scoped PostgreSQL advisory lock while
claiming one command, applying aggregate changes, creating outbox events, and
marking that command processed. A disconnect rolls back all four operations.
Multiple writer processes cannot apply commands concurrently. Startup reclaims
legacy PROCESSING rows under the same lock. Queue order is receipt order
(`created_at`, then UUID), not analytical event time. A delayed or FAILED head
cannot be overtaken. Reconciliation is also a queued command.

Infrastructure failures back off from one to 60 seconds and withdraw readiness.
Other command failures are persisted and retried up to five times; a FAILED head
stops the service for investigation, preserving all subsequent inputs. The parent
awaits its worker tasks and removes readiness on exit. A successful database check
refreshes the readiness timestamp every 30 seconds. At 10,000 outstanding commands
the service withdraws readiness and pauses ingress while the writer drains.
JetStream keeps those deliveries unacknowledged and renews their deadlines.

Apply `supabase/migrations/20260930233000_entry_opportunity_fifo_index.sql` to
the configured local PostgreSQL database before resuming. This is an additive
partial index; rollback of the application does not require dropping it.
Do not purge the command table or delete durable consumers to clear this incident.
Check PENDING/PROCESSING/FAILED counts and the oldest command before restarting.
FAILED commands require investigation and explicit requeue after the cause is fixed.

Options Gamma scheduled refreshes run during regular weekday session hours using
the existing session classifier (not an exchange holiday calendar). Explicit
one-shot runs remain available outside that window. Open interest is reused for
one hour, expires across UTC dates, and is evicted when a symbol leaves the universe.
Live option-chain data continues to refresh on every permitted cycle.
Rotation uses completed daily bars, so automatic calculation runs once per
exchange-local date; explicit one-shot runs still recalculate immediately.
Redis cache leases renew at most once per 30 seconds instead of on every read.

Operational limitations: the underlying host/storage interruption needs separate
host diagnosis if it recurs. These changes handle a transient outage; they do not
claim to repair the filesystem. Existing stream history and command evidence are
preserved. Historical request totals are cumulative logs, not a measured billing
delta or proof that every request was redundant.

## Verified Recovery on September 30

- Applied the additive FIFO index to local PostgreSQL and deployed the changed
  runtime files to the WSL checkout.
- Recovered the original 40,250 PENDING commands and one orphaned PROCESSING
  command. Final snapshot: 264,825 PROCESSED; no PENDING, PROCESSING, or FAILED rows.
- Published the seven recovery outbox events to local JetStream; unpublished
  outbox count returned to zero. No external market-data polling was started.
- Confirmed the active Entry Opportunity durables use max_ack_pending=1 without
  deleting consumers or resetting their positions.
- Ruff and Pyright passed in WSL. Full suite: 1,905 passed, 20 skipped, 79.69%
  coverage. Two additional PostgreSQL tests passed against a disposable isolated
  database, including terminating a backend mid-transaction and concurrent FIFO
  writers. The test database was then removed.
- All MarketBot application processes were stopped after recovery; infrastructure
  containers remain available. No commit or push was performed for this repair.
