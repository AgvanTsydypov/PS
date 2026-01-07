from flask import Flask, jsonify, render_template
from polymarket_client import PolymarketClient

app = Flask(__name__)
polymarket = PolymarketClient()


@app.route('/')
def index():
    """Home page with API information"""
    return render_template('index.html')


@app.route('/api/markets')
def get_markets():
    """Fetch all markets from Polymarket"""
    try:
        markets = polymarket.get_markets()
        return jsonify(markets)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/markets/<market_id>')
def get_market(market_id):
    """Fetch a specific market by ID"""
    try:
        market = polymarket.get_market(market_id)
        return jsonify(market)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events')
def get_events():
    """Fetch all events from Polymarket"""
    try:
        events = polymarket.get_events()
        return jsonify(events)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events/<event_id>')
def get_event(event_id):
    """Fetch a specific event by ID"""
    try:
        event = polymarket.get_event(event_id)
        return jsonify(event)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

