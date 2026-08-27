-- Migration: add household and Home Assistant room metadata to legacy rooms.
-- Run once after migrate_add_users.sql. Docker runs it automatically for new volumes.

ALTER TABLE rooms
    ADD COLUMN household_id INT NULL AFTER id,
    ADD COLUMN description VARCHAR(255) NULL AFTER name,
    ADD COLUMN ha_area_id VARCHAR(100) NULL AFTER description;

ALTER TABLE rooms
    ADD INDEX idx_rooms_household_id (household_id),
    ADD UNIQUE INDEX unique_household_room_name (household_id, name),
    ADD UNIQUE INDEX unique_household_ha_area (household_id, ha_area_id);
