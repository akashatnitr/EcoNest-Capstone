CREATE TABLE IF NOT EXISTS weather_forecasts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_entity_id VARCHAR(255) NOT NULL,
    forecast_at TIMESTAMP NOT NULL,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    condition_name VARCHAR(64),
    temperature_f FLOAT,
    humidity_percent FLOAT,
    precipitation_in FLOAT,
    wind_speed_mph FLOAT,
    UNIQUE KEY unique_weather_forecast (source_entity_id, forecast_at),
    INDEX idx_weather_forecast_time (forecast_at)
);
