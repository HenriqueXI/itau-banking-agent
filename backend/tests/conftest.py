import asyncio
import sys

# psycopg's async driver (checkpointer) requires the selector loop on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
