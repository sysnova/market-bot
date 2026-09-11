# SHORT scope tied to Order Flow

Definition 7.58.0 uses Alert 3.10.0 and Entry Opportunity 15.0.0.
The assembly supplies both engines with the intersection of Order Flow
`tracked_symbols` and the underlying symbols in the selected Leveraged Thesis
pairs. Only ASTS and NBIS currently qualify. ASTX, ASTN and NBIZ remain in Order
Flow but cannot generate SHORT confirmations or new SHORT opportunities.
Unclassified symbols are excluded until registered as underlyings; tracking an
instrument in Order Flow alone never authorizes a SHORT. Policy changes take
effect when the engines are constructed again. If Order Flow is not active,
the scope is empty.

Alert applies the scope before promoting a bearish consensus to SHORT CONFIRMED.
Entry Opportunity independently rejects new out-of-scope SHORT confirmations,
including replayed or externally supplied alerts. Ordinary LONG and bearish
analysis behavior is retained. Previously opened SHORT opportunities continue
to receive price marks, stop/target exits and session expiry handling even if
their ticker is no longer in the allowed scope.

Earlier definitions and implementations remain available for rollback. The
Windows default now selects 7.58.0; explicitly pinned definitions must be changed
by the operator to adopt this scope. The Windows copy of the tmux launcher also
defaults to 7.58.0. Its confirmation pane is labeled COMPRAS Y SHORT CONFIRMADOS.
Tests feed synthetic candles through Swing and Intraday, then deliver the
resulting SHORT alert to the monitor subscription and assert visible output
and sound for ASTS and NBIS. This verifies code behavior with an in-memory bus,
not the running market-data/NATS processes. No WSL runtime or checkout was modified.
