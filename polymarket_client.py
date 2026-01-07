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
    
    def get_markets(self, limit: int = 10, offset: int = 0) -> List[Dict]:
        """
        Fetch markets from Polymarket
        
        Args:
            limit: Number of markets to return (default: 10)
            offset: Offset for pagination (default: 0)
            
        Returns:
            List of market dictionaries
        """
        params = {'limit': limit, 'offset': offset}
        return self._make_request('markets', params)
    
    def get_market(self, market_id: str) -> Dict:
        """
        Fetch a specific market by ID
        
        Args:
            market_id: The market ID or slug
            
        Returns:
            Market dictionary
        """
        return self._make_request(f'markets/{market_id}')
    
    def get_events(self, limit: int = 10, offset: int = 0) -> List[Dict]:
        """
        Fetch events from Polymarket
        
        Args:
            limit: Number of events to return (default: 10)
            offset: Offset for pagination (default: 0)
            
        Returns:
            List of event dictionaries
        """
        params = {'limit': limit, 'offset': offset}
        return self._make_request('events', params)
    
    def get_event(self, event_id: str) -> Dict:
        """
        Fetch a specific event by ID
        
        Args:
            event_id: The event ID or slug
            
        Returns:
            Event dictionary
        """
        return self._make_request(f'events/{event_id}')
    
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

