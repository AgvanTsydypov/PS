WITH event_aggregates AS (
    -- Step 1: Calculate the core metrics per wallet, per event
    SELECT 
        proxy_wallet,
        MAX(event_id) AS event_id,
        event_slug,
        
        -- True USDC spent (Avg Price * Shares)
        SUM(avg_price * total_bought) AS event_volume_usdc,
        
        SUM(realized_pnl) AS event_pnl,
        
        -- Capital-Weighted VWAP: Sum of (Price * USDC) / Total USDC
        SUM(avg_price * (avg_price * total_bought)) / NULLIF(SUM(avg_price * total_bought), 0) AS capital_weighted_vwap,
        
        -- ROI is Total PnL / True USDC Spent
        SUM(realized_pnl) / NULLIF(SUM(avg_price * total_bought), 0) AS event_roi
        
    FROM 
        user_closed_positions
    GROUP BY 
        proxy_wallet, 
        event_slug
),
filtered_traders AS (
    -- Step 2: Expand the funnel and remove late-stage bot-like entries.
    SELECT * FROM event_aggregates
    WHERE capital_weighted_vwap >= 0.001
      AND capital_weighted_vwap < 0.97
),
ranked_traders AS (
    -- Step 3: Calculate dynamic percentiles for relative metrics within each event
    SELECT 
        *,
        PERCENT_RANK() OVER (PARTITION BY event_slug ORDER BY event_roi ASC) AS roi_percentile,
        PERCENT_RANK() OVER (PARTITION BY event_slug ORDER BY event_volume_usdc ASC) AS volume_percentile,
        PERCENT_RANK() OVER (PARTITION BY event_slug ORDER BY capital_weighted_vwap ASC) AS vwap_percentile
    FROM 
        filtered_traders
),
leaderboard_latest AS (
    -- Step 4: Latest known trader rank per wallet from leaderboard snapshots
    SELECT DISTINCT ON (proxy_wallet)
        proxy_wallet,
        rank
    FROM trader_leaderboard
    WHERE category = 'OVERALL'
      AND time_period = 'ALL'
      AND order_by = 'PNL'
    ORDER BY proxy_wallet, fetched_date DESC, fetched_at DESC
)
-- Step 5: Final Output & UI Mapping (4-metric signature)
SELECT 
    rt.proxy_wallet,
    rt.event_id,
    rt.event_slug,
    ROUND(rt.capital_weighted_vwap, 4) AS entry_cwap,
    ROUND(rt.event_volume_usdc, 2) AS total_volume,
    ROUND(rt.event_pnl, 2) AS total_pnl,
    ROUND(rt.event_roi * 100, 2) AS roi_percentage,
    
    -- 1) ENTRY (Absolute): What true odds/probability did they buy into?
    CASE 
        WHEN rt.capital_weighted_vwap <= 0.20 THEN 'Anomaly'
        WHEN rt.capital_weighted_vwap <= 0.40 THEN 'Oracle'
        WHEN rt.capital_weighted_vwap <= 0.60 THEN 'Outlier'
        WHEN rt.capital_weighted_vwap <= 0.80 THEN 'Vector'
        ELSE 'Harvester'
    END AS entry_bracket,

    -- 2) RISK (Relative): How early were they compared to the event cohort?
    CASE 
        WHEN rt.vwap_percentile <= 0.010 THEN 'P99'
        WHEN rt.vwap_percentile <= 0.100 THEN 'P90'
        WHEN rt.vwap_percentile <= 0.300 THEN 'P70'
        WHEN rt.vwap_percentile <= 0.500 THEN 'P50'
        ELSE 'Base'
    END AS edge,

    -- 3) SKILL: Capital Efficiency Percentile
    CASE 
        WHEN rt.roi_percentile >= 0.99 THEN 'P99'
        WHEN rt.roi_percentile >= 0.90 THEN 'P90'
        WHEN rt.roi_percentile >= 0.70 THEN 'P70'
        WHEN rt.roi_percentile >= 0.50 THEN 'P50'
        ELSE 'Base'
    END AS yield,

    -- 4) INFLUENCE: Capital Footprint Percentile
    CASE 
        WHEN rt.volume_percentile >= 0.99 THEN 'P99'
        WHEN rt.volume_percentile >= 0.90 THEN 'P90'
        WHEN rt.volume_percentile >= 0.70 THEN 'P70'
        WHEN rt.volume_percentile >= 0.50 THEN 'P50'
        ELSE 'Base'
    END AS gravity,
    ll.rank

FROM 
    ranked_traders rt
LEFT JOIN leaderboard_latest ll
    ON ll.proxy_wallet = rt.proxy_wallet
ORDER BY 
    rt.event_slug, 
    total_volume DESC;
