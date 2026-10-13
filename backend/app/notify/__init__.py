"""Notification channels — both entirely optional.

Webhook alerts (`discord.py`) and the slash-command bot (`bot.py`) are
independent: you can run one without the other, and with neither the
pipeline is unaffected. Neither is imported at module load, so a missing
or broken Discord install never stops the server from booting.

Caveat: `_post` makes a single attempt with no retry and swallows
failures, so a failed send still counts as "sent" and the row's
`notified_at` gets stamped.
"""
