-- One editable rating and optional comment per resident and completed task.
CREATE TABLE IF NOT EXISTS command_feedback (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(100) NOT NULL,
    user_id BIGINT NOT NULL,
    prompt TEXT NOT NULL,
    response_text TEXT NOT NULL,
    agent VARCHAR(100) NULL,
    result_status VARCHAR(20) NOT NULL,
    rating TINYINT UNSIGNED NOT NULL,
    comment TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_command_feedback_task_user (task_id, user_id),
    INDEX idx_command_feedback_updated (updated_at)
);

-- Corrections are separate from ratings so an older installation can add this
-- table without rewriting its existing feedback records.
CREATE TABLE IF NOT EXISTS command_feedback_corrections (
    task_id VARCHAR(100) NOT NULL,
    user_id BIGINT NOT NULL,
    correction TEXT NOT NULL,
    review_status VARCHAR(30) NOT NULL DEFAULT 'needs_human_review',
    evidence_note TEXT NULL,
    reviewer_id BIGINT NULL,
    reviewed_at TIMESTAMP NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (task_id, user_id),
    INDEX idx_corrections_review (review_status, user_id)
);
