-- Adds the durable, device-level hourly energy analytics cache to an existing
-- EcoNest MySQL database. Safe to run more than once.
CREATE TABLE IF NOT EXISTS energy_hourly_analytics (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    device_id INT NOT NULL,
    room_id INT NOT NULL,
    hour_start DATETIME NOT NULL,
    sample_count INT UNSIGNED NOT NULL DEFAULT 0,
    avg_power_w FLOAT NULL,
    peak_power_w FLOAT NULL,
    metered_energy_kwh FLOAT NULL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_energy_hourly_device (device_id, hour_start),
    INDEX idx_energy_hourly_room_time (room_id, hour_start),
    INDEX idx_energy_hourly_time (hour_start),
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
