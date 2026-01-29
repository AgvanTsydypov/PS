import requests
from typing import Dict, List, Optional


class PolymarketClient:
    """Client for interacting with Polymarket Gamma API"""
    
    BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
        })
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make a GET request to the Polymarket API"""
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to {url}: {e}")
            raise
    
    def get_markets(self, 
                    limit: int = 10, 
                    offset: int = 0, 
                    closed: Optional[bool] = None,
                    tag_id: Optional[str] = None,
                    order: str = 'id',
                    ascending: bool = False) -> List[Dict]:
        """
        Fetch markets from Polymarket
        
        Args:
            limit: Number of markets to return (default: 10)
            offset: Offset for pagination (default: 0)
            closed: Filter by closed status (None = all, True = closed, False = active)
            tag_id: Filter by tag ID
            order: Order by field (default: 'id')
            ascending: Sort order (default: False = newest first)
            
        Returns:
            List of market dictionaries
        """
        params = {'limit': limit, 'offset': offset, 'order': order, 'ascending': str(ascending).lower()}
        if closed is not None:
            params['closed'] = str(closed).lower()
        if tag_id:
            params['tag_id'] = tag_id
        return self._make_request('markets', params)
    
    def get_market_by_slug(self, slug: str) -> Dict:
        """
        Fetch a specific market by slug (OFFICIAL API METHOD)
        
        Args:
            slug: The market slug from URL
            
        Returns:
            Market dictionary
            
        Example:
            >>> client.get_market_by_slug('will-bitcoin-hit-100k')
        """
        return self._make_request(f'markets/slug/{slug}')
    
    def get_events(self, 
                   limit: int = 10, 
                   offset: int = 0,
                   closed: Optional[bool] = None,
                   tag_id: Optional[str] = None,
                   order: str = 'id',
                   ascending: bool = False) -> List[Dict]:
        """
        Fetch events from Polymarket
        
        Args:
            limit: Number of events to return (default: 10)
            offset: Offset for pagination (default: 0)
            closed: Filter by closed status (None = all, True = closed, False = active)
            tag_id: Filter by tag ID
            order: Order by field (default: 'id')
            ascending: Sort order (default: False = newest first)
            
        Returns:
            List of event dictionaries
        """
        params = {'limit': limit, 'offset': offset, 'order': order, 'ascending': str(ascending).lower()}
        if closed is not None:
            params['closed'] = str(closed).lower()
        if tag_id:
            params['tag_id'] = tag_id
        return self._make_request('events', params)
    
    def get_event_by_slug(self, slug: str) -> Dict:
        """
        Fetch a specific event by slug (OFFICIAL API METHOD)
        
        Args:
            slug: The event slug from URL
            
        Returns:
            Event dictionary
            
        Example:
            >>> client.get_event_by_slug('venezuela-leader-end-of-2026')
        """
        return self._make_request(f'events/slug/{slug}')
    
    def get_tags(self) -> List[Dict]:
        """
        Fetch all available tags
        
        Returns:
            List of tag dictionaries
        """
        return self._make_request('tags')
    
    def get_sports(self) -> List[Dict]:
        """
        Fetch all sports tags and metadata
        
        Returns:
            List of sports dictionaries with tag IDs, images, and metadata
        """
        return self._make_request('sports')
    
    def search_markets(self, query: str) -> List[Dict]:
        """
        Search for markets by query
        
        Args:
            query: Search query string
            
        Returns:
            List of matching markets
        """
        params = {'query': query}
        return self._make_request('search', params)

