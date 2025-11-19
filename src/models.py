from dataclasses import dataclass

@dataclass(frozen=True, eq=True)
class VineItem:
    """A class to hold information about a Vine item."""
    asin: str
    title: str
    url: str
    image_url: str
    queue_url: str
