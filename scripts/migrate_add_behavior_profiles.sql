-- Stores a regenerated, evidence-backed household behavior profile.
CREATE TABLE IF NOT EXISTS behavior_profiles (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    profile_key VARCHAR(100) NOT NULL,
    profile JSON NOT NULL,
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_behavior_profile (profile_key),
    INDEX idx_behavior_profile_generated (generated_at)
);
