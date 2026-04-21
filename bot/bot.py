import discord
from discord.ext import commands
import requests
import os
from dotenv import load_dotenv

# =========================
# 🔐 LOAD ENV VARS
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

API_BASE = "http://127.0.0.1:8000"

# =========================
# ⚙️ INTENTS
# =========================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)
bot.remove_command("help")


def get_bed_meta():
    try:
        res = requests.get(f"{API_BASE}/api/beds/meta", timeout=5)
        return res.json()
    except:
        return {}
# =========================
# 🌱 HELP COMMAND (FIXED + CLEAN)
# =========================
@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="🌱 Smart Garden System",
        description="Control and monitor your irrigation system in real time.",
        color=0x2ecc71
    )

    embed.add_field(
        name="📊 .status",
        value="Shows soil moisture + valve states for all beds",
        inline=False
    )

    embed.add_field(
        name="🛠️ System Info",
        value="ESP32 + FastAPI + Discord Bot integration",
        inline=False
    )

    embed.add_field(
        name="🌿 Status Meaning",
        value=(
            "💧 **Wet** = Low moisture\n"
            "🌱 **Healthy** = Optimal range\n"
            "🏜️ **Dry** = Needs watering"
        ),
        inline=False
    )

    embed.set_footer(text="Smart Garden • real-time irrigation monitoring")

    await ctx.send(embed=embed)


# =========================
# 🌱 API FUNCTION
# =========================
def get_bed_status():
    try:
        res = requests.get(f"{API_BASE}/api/beds/latest", timeout=5)
        return res.json()
    except:
        return None


# =========================
# 🚀 READY EVENT
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}")


# =========================
# 💬 STATUS COMMAND (IMPROVED)
# =========================
@bot.command()
async def status(ctx):
    data = get_bed_status()
    meta = get_bed_meta()

    if not data:
        await ctx.send("❌ Could not reach irrigation API.")
        return

    embed = discord.Embed(
        title="🌱 Smart Garden Status",
        description="Live soil moisture readings",
        color=0x3498db
    )

    active = []

    for bed_id, bed in data.items():

        avg = bed.get("average", 0)
        valve = bed.get("valve_state", "UNKNOWN")

        # 🌿 GET USER META (name + icon)
        bed_meta = meta.get(bed_id, {})
        name = bed_meta.get("name", bed_id)
        icon = bed_meta.get("icon", "🌱")

        # soil state
        if avg > 650:
            state = "🏜️ Dry"
        elif avg > 450:
            state = "🌱 Healthy"
        else:
            state = "💧 Wet"

        embed.add_field(
            name=f"{icon} {name} (`{bed_id}`)",
            value=(
                f"**Moisture:** `{avg:.1f}`\n"
                f"**State:** {state}\n"
                f"**Valve:** {'🚰 ON' if valve == 'ON' else '🔒 OFF'}"
            ),
            inline=True
        )

        if valve == "ON":
            active.append(name)

    if active:
        embed.set_footer(text=f"🚰 Watering: {', '.join(active)}")
    else:
        embed.set_footer(text="🟢 System idle — no watering active")

    await ctx.send(embed=embed)
# =========================
# ▶️ START BOT
# =========================
if not TOKEN:
    print("❌ Missing DISCORD_TOKEN in .env file")
else:
    bot.run(TOKEN)