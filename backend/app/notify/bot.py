"""Discord bot — slash commands, runs alongside FastAPI in a daemon thread.

Commands:
  /scan          — trigger a full scan
  /status        — scan progress + last scan
  /stats         — pool overview (total/A/B/applied)
  /tier <A|B|C>  — top 5 jobs of a tier with links
  /applied <id>  — mark a job as applied
  /hide <id>     — hide a job

Starts only when DISCORD_BOT_TOKEN is set. Global command sync can take
up to ~1h to appear the first time (Discord limitation).
"""

from __future__ import annotations

import asyncio
import logging
import threading

log = logging.getLogger("hydrahunt.bot")


def start(token: str) -> None:
    def runner() -> None:
        asyncio.run(_run(token))

    threading.Thread(target=runner, daemon=True, name="discord-bot").start()
    log.info("discord bot thread started")


async def _run(token: str) -> None:
    import discord
    from discord import app_commands

    from app import scancontrol
    from app.config import load_config
    from app.db import POOL_WORTHY_SQL, connect

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    def db():
        return connect(load_config().db_path)

    @client.event
    async def on_ready():
        # guild-scoped sync = commands appear INSTANTLY in joined servers
        # (global sync can take up to an hour)
        await tree.sync()
        for guild in client.guilds:
            try:
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
                log.info("commands synced instantly to guild %s", guild.name)
            except Exception:
                log.warning("guild sync failed for %s", guild.name, exc_info=True)
        log.info("discord bot ready as %s", client.user)

    @client.event
    async def on_guild_join(guild: discord.Guild):
        # sync commands the moment the bot joins a server — no restart needed
        try:
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            log.info("bot joined %s — commands synced", guild.name)
        except Exception:
            log.warning("sync on join failed for %s", guild.name, exc_info=True)

    @tree.command(name="scan", description="Trigger a full HydraHunt scan")
    async def scan_cmd(interaction: discord.Interaction):
        if scancontrol.try_start_scan():
            await interaction.response.send_message(
                "🔍 Scan started — I'll hunt. Check /status in a few minutes."
            )
        else:
            await interaction.response.send_message("⏳ A scan is already running.")

    @tree.command(name="status", description="Current scan progress")
    async def status_cmd(interaction: discord.Interaction):
        s = scancontrol.state
        if s["running"]:
            await interaction.response.send_message(f"⏳ Scanning since {s['started_at'][:19]} UTC…")
        elif s["result"]:
            r = s["result"]
            await interaction.response.send_message(
                f"✅ Last scan: {r['jobs_found']} scanned · {r['jobs_new']} new · "
                f"{r['tier_a']} A-tier · {r['tier_b']} B-tier"
            )
        else:
            await interaction.response.send_message("No scan yet — run /scan")

    @tree.command(name="stats", description="Job pool overview")
    async def stats_cmd(interaction: discord.Interaction):
        conn = db()
        try:
            row = conn.execute(
                "SELECT COUNT(*) t, SUM(tier='A') a, SUM(tier='B') b, "
                f"SUM(status='applied') ap FROM jobs WHERE {POOL_WORTHY_SQL} "
                "AND status != 'hidden' AND COALESCE(location_tag,'') != 'EXCLUDED'"
            ).fetchone()
        finally:
            conn.close()
        await interaction.response.send_message(
            f"📊 Pool: {row['t']} jobs · 🟢 {row['a'] or 0} A-tier · "
            f"🟡 {row['b'] or 0} B-tier · ✉️ {row['ap'] or 0} applied"
        )

    @tree.command(name="tier", description="Top 5 jobs of a tier")
    @app_commands.describe(letter="A, B, or C")
    async def tier_cmd(interaction: discord.Interaction, letter: str):
        letter = letter.upper()
        if letter not in "ABC":
            await interaction.response.send_message("Tier must be A, B, or C.")
            return
        conn = db()
        try:
            rows = conn.execute(
                "SELECT id, title, company, source_url FROM jobs WHERE tier=? "
                "AND status='new' AND COALESCE(location_tag,'') != 'EXCLUDED' "
                f"AND {POOL_WORTHY_SQL} "
                "ORDER BY COALESCE(llm_score, rule_score) DESC LIMIT 5",
                (letter,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            await interaction.response.send_message(f"No new {letter}-tier jobs.")
            return
        lines = [f"**{letter}-tier top {len(rows)}:**"] + [
            f"`#{r['id']}` [{r['title']} @ {r['company']}]({r['source_url']})" for r in rows
        ]
        await interaction.response.send_message("\n".join(lines))

    async def _set_status(interaction: discord.Interaction, job_id: int, status: str) -> None:
        conn = db()
        try:
            cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
            conn.commit()
        finally:
            conn.close()
        if cur.rowcount:
            await interaction.response.send_message(f"✅ Job #{job_id} marked as **{status}**.")
        else:
            await interaction.response.send_message(f"❌ Job #{job_id} not found.")

    @tree.command(name="applied", description="Mark a job as applied")
    async def applied_cmd(interaction: discord.Interaction, job_id: int):
        await _set_status(interaction, job_id, "applied")

    @tree.command(name="hide", description="Hide a job")
    async def hide_cmd(interaction: discord.Interaction, job_id: int):
        await _set_status(interaction, job_id, "hidden")

    @tree.command(name="draft", description="Generate application draft for a job")
    async def draft_cmd(interaction: discord.Interaction, job_id: int):
        from app.apply.drafter import generate_draft, get_cached, save_draft

        await interaction.response.defer()  # LLM takes ~30-60s
        conn = db()
        try:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                await interaction.followup.send(f"❌ Job #{job_id} not found.")
                return
            draft = get_cached(conn, job_id)
            if not draft:
                cfg = load_config()
                draft = generate_draft(dict(job), cfg)
                if not draft:
                    await interaction.followup.send(
                        "⚠️ Draft failed (LLM unavailable) — try again later."
                    )
                    return
                save_draft(conn, job_id, draft)
        finally:
            conn.close()

        def trim(text: str, limit: int = 1024) -> str:
            return text[: limit - 1] + "…" if len(text) > limit else text

        embeds = [
            discord.Embed(
                title=f"✍️ Draft — {job['title'][:80]}",
                description=f"**{job['company']}** · model `{draft.get('_model', '?')}`",
                color=0x7B9FD4,
            )
            .add_field(name="Summary", value=trim(draft.get("summary", "")), inline=False)
            .add_field(name="Why this role", value=trim(draft.get("why_this_role", "")), inline=False)
            .add_field(name="Why you", value=trim(draft.get("why_you", "")), inline=False)
        ]
        gaps = draft.get("gaps") or []
        if gaps:
            embeds[0].add_field(name="Honest gaps", value=trim("\n".join(gaps)), inline=False)
        email = draft.get("cover_email", "")
        if email:
            embeds.append(discord.Embed(title="📧 Cover email", description=trim(email, 4000)))
        await interaction.followup.send(embeds=embeds)

    try:
        await client.start(token)
    except Exception:
        log.exception("discord bot died")


def start_if_configured(token: str | None) -> None:
    if token:
        start(token)
    else:
        log.info("DISCORD_BOT_TOKEN not set — bot commands disabled (webhook alerts still work)")
