"""RelayForge by Ragequits — Python entry point."""
import logging
import os
from dotenv import load_dotenv
from relayforge.bot import Bridge

if __name__ == '__main__':
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    required = ('PTERODACTYL_URL', 'PTERODACTYL_SERVER_ID', 'PTERODACTYL_API_KEY', 'DISCORD_TOKEN', 'DISCORD_CHANNEL_ID')
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit('Missing environment settings: ' + ', '.join(missing))
    Bridge().run(os.environ['DISCORD_TOKEN'], log_handler=None)
