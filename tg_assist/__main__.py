if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    import asyncio

    import dotenv

    dotenv.load_dotenv()

    from tg_assist.app import start_tg_assist

    asyncio.run(start_tg_assist())
