-- Adds periodic, explainable comfort-learning observations to an existing DB.
CREATE TABLE IF NOT EXISTS comfort_observations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    climate_device_id INT NULL,
    room_id INT NOT NULL,
    climate_entity_id VARCHAR(255) NOT NULL,
    observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    target_temperature FLOAT NULL,
    current_temperature FLOAT NULL,
    humidity_percent FLOAT NULL,
    hvac_mode VARCHAR(64) NULL,
    hvac_action VARCHAR(64) NULL,
    target_changed BOOLEAN NOT NULL DEFAULT FALSE,
    change_origin VARCHAR(32) NOT NULL DEFAULT 'observed',
    INDEX idx_comfort_entity_time (climate_entity_id, observed_at),
    INDEX idx_comfort_room_time (room_id, observed_at),
    FOREIGN KEY (climate_device_id) REFERENCES devices(id) ON DELETE SET NULL,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
