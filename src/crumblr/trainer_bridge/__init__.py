"""Crumblr -> Trainer MODE_2 evidence bridge (TRAINER/TRADER/CRUMBLR V1 Slice 1).

Read-only on the Crumblr side: resolves one already-closed real trade from
Crumblr's own durable evidence, derives one deterministic `return_r`, and
posts it through the external Trainer's existing `/api/v1/campaigns/{id}/
agent-data` endpoint (MODE_2). Crumblr never writes to the Trainer's
database directly and the Trainer never gets Crumblr database or broker
access -- this package only ever makes one outbound HTTP POST of an already-
computed, already-immutable result.

No `StrategyMaterializer`, no artifact promotion, no `TradingAssignment`
change, no permit, no broker mutation -- deliberately out of scope for this
slice (see `review/FEEDBACK.md`/`status.md` for the accepted integration
map this implements).
"""
