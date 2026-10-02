-- Adds compact, durable Home Assistant transition records.
-- Safe to run more than once on an existing EcoNest MySQL database.
CREATE TABLE IF NOT EXISTS home_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    occurred_at TIMESTAMP NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    device_id INT NULL,
    room_id INT NULL,
    event_type VARCHAR(64) NOT NULL,
    previous_state VARCHAR(64) NULL,
    new_state VARCHAR(64) NULL,
    metadata JSON NOT NULL,
    source VARCHAR(64) NOT NULL DEFAULT 'home_assistant',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_home_event (entity_id, occurred_at, event_type),
    INDEX idx_home_events_time (occurred_at),
    INDEX idx_home_events_type_time (event_type, occurred_at),
    INDEX idx_home_events_device_time (device_id, occurred_at),
    INDEX idx_home_events_room_time (room_id, occurred_at)
);
