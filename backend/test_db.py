import asyncio
import asyncpg

async def test():
    c = await asyncpg.connect("postgresql://fundquant:123456@127.0.0.1:5432/fundquant")
    r = await c.fetchval("SELECT count(*) FROM funds")
    print(f"OK: {r} funds")
    await c.close()

asyncio.run(test())
