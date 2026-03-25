WITH event_aggregates AS (
    -- Step 1: Calculate the core metrics per wallet, per event
    SELECT 
        proxy_wallet,
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
    -- Step 2: Expand the funnel. Floor set at 0.001 to remove broken data/0-tick glitches.
    SELECT * FROM event_aggregates
    WHERE capital_weighted_vwap >= 0.001
),
ranked_traders AS (
    -- Step 3: Calculate dynamic percentiles for ROI and Volume within each event
    SELECT 
        *,
        PERCENT_RANK() OVER (PARTITION BY event_slug ORDER BY event_roi ASC) AS roi_percentile,
        PERCENT_RANK() OVER (PARTITION BY event_slug ORDER BY event_volume_usdc ASC) AS volume_percentile
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
-- Step 5: Final Output & 1:1 UI Mapping
SELECT 
    rt.proxy_wallet,
    rt.event_slug,
    ROUND(rt.capital_weighted_vwap, 4) AS entry_cwap,
    ROUND(rt.event_volume_usdc, 2) AS total_volume,
    ROUND(rt.event_pnl, 2) AS total_pnl,
    ROUND(rt.event_roi * 100, 2) AS roi_percentage,
    
    -- Risk: The Behavioral Archetype
    CASE 
        WHEN rt.capital_weighted_vwap <= 0.20 THEN 'Oracle'
        WHEN rt.capital_weighted_vwap <= 0.50 THEN 'Outlier'
        WHEN rt.capital_weighted_vwap <= 0.70 THEN 'Momentum'
        WHEN rt.capital_weighted_vwap <= 0.90 THEN 'Validator'
        ELSE 'Harvester'
    END AS risk,

    -- Skill: Capital Efficiency Percentile
    CASE 
        WHEN rt.roi_percentile >= 0.999 THEN 'P999'
        WHEN rt.roi_percentile >= 0.99 THEN 'P99'
        WHEN rt.roi_percentile >= 0.95 THEN 'P95'
        WHEN rt.roi_percentile >= 0.80 THEN 'P80'
        WHEN rt.roi_percentile >= 0.50 THEN 'P50'
        ELSE 'Base'
    END AS skill,

    -- Volume: Capital Footprint Percentile
    CASE 
        WHEN rt.volume_percentile >= 0.999 THEN 'P999'
        WHEN rt.volume_percentile >= 0.99 THEN 'P99'
        WHEN rt.volume_percentile >= 0.95 THEN 'P95'
        WHEN rt.volume_percentile >= 0.80 THEN 'P80'
        WHEN rt.volume_percentile >= 0.50 THEN 'P50'
        ELSE 'Base'
    END AS influence,
    ll.rank

FROM 
    ranked_traders rt
LEFT JOIN leaderboard_latest ll
    ON ll.proxy_wallet = rt.proxy_wallet
ORDER BY 
    rt.event_slug, 
    total_volume DESC;
