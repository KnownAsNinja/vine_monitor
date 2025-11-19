import json
import logging
import time
import datetime
import urllib.request
import urllib.error
from typing import Optional

from config import config
from models import VineItem

def send_discord_notification(webhook_url: str, item: VineItem, queue_name: str):
    """Sends a notification to a Discord webhook using an embed."""
    logging.info("Sending Discord notification for: %s", item.title)

    # Use a placeholder if the title is empty, as Discord requires a non-empty title
    notification_title = item.title if item.title else f"New Item (ASIN: {item.asin})"
    try:
        data = {
            "embeds": [
                {
                    "title": notification_title,
                    "url": item.url,
                    "description": f"<@312951812401659905> - New item found in **{queue_name}**!",
                    "color": 5814783,  # Hex color #58D68D (a nice green)
                    "thumbnail": {"url": item.image_url},
                    "fields": [
                        {"name": "QUEUE URL", "value": item.queue_url, "inline": True},                         
                    ],
                    "footer": {"text": "Vine Monitor"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            ]
        }
        payload = json.dumps(data).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': config.USER_AGENT
        }
        req = urllib.request.Request(webhook_url, data=payload, headers=headers)
        with urllib.request.urlopen(req) as response:
            if response.status not in [200, 204]:
                logging.error("Discord webhook failed with status: %d", response.status)
                time.sleep(2)
            else:
                time.sleep(2)  # Wait a bit to avoid hitting rate limits
    except Exception as e:
        logging.error("Failed to send Discord notification: %s", e)
