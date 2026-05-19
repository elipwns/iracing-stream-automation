import asyncio
import os
from dotenv import load_dotenv
from twitchio.ext import commands
import betting

load_dotenv()
betting.init_db()

CHANNEL = os.getenv("TWITCH_CHANNEL", "")


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=os.getenv("TWITCH_TOKEN"),
            prefix="!",
            initial_channels=[CHANNEL],
        )

    async def event_ready(self):
        print(f"[bot] Connected as {self.nick} in #{CHANNEL}")
        asyncio.create_task(self._drain_queue())

    async def event_message(self, message):
        if message.echo:
            return
        await self.handle_commands(message)

    async def _drain_queue(self):
        channel = self.get_channel(CHANNEL)
        while True:
            if channel:
                for msg in betting.dequeue_messages():
                    await channel.send(msg)
            await asyncio.sleep(3)

    @commands.command()
    async def bet(self, ctx, outcome: str = None, amount: str = None):
        if not outcome or not amount:
            await ctx.send(f"@{ctx.author.name} Usage: !bet [win/podium/finish/crash] [amount or all]")
            return
        ok, msg = betting.place_bet(str(ctx.author.id), ctx.author.name, outcome.lower(), amount.lower())
        await ctx.send(f"@{ctx.author.name} {msg}")

    @commands.command()
    async def points(self, ctx):
        pts = betting.get_points(str(ctx.author.id), ctx.author.name)
        await ctx.send(f"@{ctx.author.name} You have {pts:,} points")

    @commands.command()
    async def bets(self, ctx):
        active = betting.get_active_bets()
        if not active:
            await ctx.send(f"@{ctx.author.name} No bets placed yet this race.")
            return
        parts = [f"{b['username']}: {b['amount']:,} on {b['outcome']} ({b['multiplier']}x)" for b in active[:6]]
        await ctx.send(" | ".join(parts))

    @commands.command()
    async def leaderboard(self, ctx):
        top = betting.get_leaderboard(5)
        if not top:
            await ctx.send(f"@{ctx.author.name} No data yet.")
            return
        parts = [f"{i+1}. {e['username']} {e['points']:,}" for i, e in enumerate(top)]
        await ctx.send(" | ".join(parts))


bot = Bot()
bot.run()
