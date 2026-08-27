-- Migration: add Home Assistant metadata to legacy devices.
-- Run once after migrate_extend_rooms.sql. Legacy device types are retained.

ALTER TABLE devices
    ADD COLUMN ip_address VARCHAR(50) NULL AFTER name,
    ADD COLUMN ha_device_id VARCHAR(64) NULL AFTER room_id,
    ADD COLUMN ha_entity_id VARCHAR(255) NULL AFTER ha_device_id,
    ADD COLUMN ha_platform VARCHAR(100) NULL AFTER ha_entity_id,
    ADD COLUMN manufacturer VARCHAR(100) NULL AFTER ha_platform,
    ADD COLUMN model VARCHAR(100) NULL AFTER manufacturer;

ALTER TABLE devices
    MODIFY COLUMN device_type ENUM(
        'energy', 'motion', 'sound',
        'smart_plug', 'motion_sensor', 'sound_sensor', 'light', 'switch',
        'cover', 'climate', 'valve', 'fan', 'media_player', 'sensor', 'other'
    ) NOT NULL,
    ADD UNIQUE INDEX unique_ha_entity_id (ha_entity_id),
    ADD INDEX idx_devices_ha_device_id (ha_device_id);
