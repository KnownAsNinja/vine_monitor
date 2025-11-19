import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
import fake_useragent

# Load environment variables from .env file
load_dotenv()

@dataclass(frozen=True)
class Config:
    # URLs
    INITIAL_PAGE: str = 'https://www.amazon.co.uk/vine/'
    RFY_URL: str = 'https://www.amazon.co.uk/vine/vine-items?queue=potluck'
    ADDITIONAL_ITEMS_URL: str = 'https://www.amazon.co.uk/vine/vine-items?queue=encore'
    AFA_URL: str = 'https://www.amazon.co.uk/vine/vine-items?queue=last_chance'
    
    # Files
    STATE_FILE: str = 'vine_monitor_state.json'
    PRIORITY_TERMS_FILE: str = 'priority_terms.json'
    LOG_FILE: str = 'vine_monitor.log'
    
    # User Agent
    USER_AGENT: str = fake_useragent.UserAgent().ff
    
    # Discord Webhooks (loaded from environment variables)
    DISCORD_WEBHOOK_RFY: Optional[str] = os.getenv('DISCORD_WEBHOOK_RFY')
    DISCORD_WEBHOOK_AFA: Optional[str] = os.getenv('DISCORD_WEBHOOK_AFA')
    DISCORD_WEBHOOK_AI: Optional[str] = os.getenv('DISCORD_WEBHOOK_AI')
    DISCORD_WEBHOOK_PRIORITY: Optional[str] = os.getenv('DISCORD_WEBHOOK_PRIORITY')
    
    # Browser
    BROWSER_TYPE: str = os.getenv('BROWSER_TYPE', 'firefox')

# Global config instance
config = Config()
