from flask import Flask, jsonify, render_template, request
from polymarket_client import PolymarketClient

app = Flask(__name__)
polymarket = PolymarketClient()


@app.route('/')
def index():
    """Home page with API information"""
    return render_template('index.html')


@app.route('/api/markets')
def get_markets():
    """Fetch all markets from Polymarket with optional filters"""
    try:
        # Get query parameters
        limit = request.args.get('limit', default=10, type=int)
        offset = request.args.get('offset', default=0, type=int)
        closed = request.args.get('closed', default=None, type=str)
        tag_id = request.args.get('tag_id', default=None, type=str)
        order = request.args.get('order', default='id', type=str)
        ascending = request.args.get('ascending', default='false', type=str)
        
        # Convert string to bool for closed parameter
        closed_bool = None
        if closed:
            closed_bool = closed.lower() == 'true'
        
        ascending_bool = ascending.lower() == 'true'
        
        markets = polymarket.get_markets(
            limit=limit, 
            offset=offset, 
            closed=closed_bool,
            tag_id=tag_id,
            order=order,
            ascending=ascending_bool
        )
        return jsonify(markets)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/markets/slug/<slug>')
def get_market_by_slug(slug):
    """Fetch a specific market by slug (Official API method)"""
    try:
        market = polymarket.get_market_by_slug(slug)
        return jsonify(market)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events')
def get_events():
    """Fetch all events from Polymarket with optional filters"""
    try:
        # Get query parameters
        limit = request.args.get('limit', default=10, type=int)
        offset = request.args.get('offset', default=0, type=int)
        closed = request.args.get('closed', default=None, type=str)
        tag_id = request.args.get('tag_id', default=None, type=str)
        order = request.args.get('order', default='id', type=str)
        ascending = request.args.get('ascending', default='false', type=str)
        
        # Convert string to bool for closed parameter
        closed_bool = None
        if closed:
            closed_bool = closed.lower() == 'true'
        
        ascending_bool = ascending.lower() == 'true'
        
        events = polymarket.get_events(
            limit=limit, 
            offset=offset,
            closed=closed_bool,
            tag_id=tag_id,
            order=order,
            ascending=ascending_bool
        )
        return jsonify(events)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/slug/<slug>')
def get_event_by_slug(slug):
    """Fetch a specific event by slug (Official API method)"""
    try:
        event = polymarket.get_event_by_slug(slug)
        return jsonify(event)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tags')
def get_tags():
    """Fetch all available tags"""
    try:
        tags = polymarket.get_tags()
        return jsonify(tags)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sports')
def get_sports():
    """Fetch all sports tags and metadata"""
    try:
        sports = polymarket.get_sports()
        return jsonify(sports)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

