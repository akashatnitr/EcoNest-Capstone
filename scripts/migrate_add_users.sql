-- Migration: add auth, access-control, and Home Assistant inventory tables
-- Issue #7

CREATE TABLE IF NOT EXISTS households (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    address VARCHAR(255),
    home_assistant_url VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    role ENUM('guest', 'family_member', 'homeowner', 'service_account', 'superadmin') NOT NULL DEFAULT 'guest',
    household_id INT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_users_household_id (household_id),
    INDEX idx_users_role (role),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    refresh_token VARCHAR(512) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_refresh_token (refresh_token),
    INDEX idx_user_sessions_user_id (user_id),
    INDEX idx_user_sessions_expires_at (expires_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_room_access (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    room_id INT NOT NULL,
    permission VARCHAR(50) NOT NULL DEFAULT 'room:read',
    allowed_start_hour TINYINT,
    allowed_end_hour TINYINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_user_room_access (user_id, room_id),
    INDEX idx_user_room_access_room_id (room_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_device_access (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    device_id INT NOT NULL,
    permission VARCHAR(50) NOT NULL DEFAULT 'device:read',
    allowed_start_hour TINYINT,
    allowed_end_hour TINYINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_user_device_access (user_id, device_id),
    INDEX idx_user_device_access_device_id (device_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS home_assistant_entities (
    id INT AUTO_INCREMENT PRIMARY KEY,
    household_id INT NOT NULL,
    ha_entity_id VARCHAR(255) NOT NULL,
    ha_device_id VARCHAR(64),
    ha_area_id VARCHAR(100),
    domain VARCHAR(50) NOT NULL,
    platform VARCHAR(100),
    friendly_name VARCHAR(255),
    original_name VARCHAR(255),
    entity_category VARCHAR(100),
    disabled_by VARCHAR(100),
    metadata JSON,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_household_ha_entity (household_id, ha_entity_id),
    INDEX idx_ha_entities_device_id (ha_device_id),
    INDEX idx_ha_entities_area_id (ha_area_id),
    FOREIGN KEY (household_id) REFERENCES households(id) ON DELETE CASCADE
) ENGINE=InnoDB;
