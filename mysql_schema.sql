-- MySQL 8+ schema equivalent to the SQLite schema used by the runnable demo.
CREATE DATABASE IF NOT EXISTS smart_complaint_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_complaint_db;
CREATE TABLE users (user_id BIGINT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(120) NOT NULL, email VARCHAR(190) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role ENUM('user','staff','admin') NOT NULL DEFAULT 'user', created_at DATETIME NOT NULL);
CREATE TABLE categories (category_id BIGINT PRIMARY KEY AUTO_INCREMENT, category_name VARCHAR(100) NOT NULL UNIQUE, priority_level TINYINT NOT NULL DEFAULT 1);
CREATE TABLE complaints (complaint_id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT NOT NULL, category_id BIGINT NOT NULL, title VARCHAR(200) NOT NULL, description TEXT NOT NULL, priority ENUM('Low','Medium','High','Critical') NOT NULL DEFAULT 'Medium', created_at DATETIME NOT NULL, status ENUM('Open','In Progress','On Hold','Resolved','Closed') NOT NULL DEFAULT 'Open', FOREIGN KEY(user_id) REFERENCES users(user_id), FOREIGN KEY(category_id) REFERENCES categories(category_id));
CREATE TABLE assignments (assignment_id BIGINT PRIMARY KEY AUTO_INCREMENT, complaint_id BIGINT NOT NULL, staff_id BIGINT NOT NULL, assigned_at DATETIME NOT NULL, FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id), FOREIGN KEY(staff_id) REFERENCES users(user_id));
CREATE TABLE resolutions (resolution_id BIGINT PRIMARY KEY AUTO_INCREMENT, complaint_id BIGINT NOT NULL UNIQUE, resolved_at DATETIME NOT NULL, resolution_text TEXT NOT NULL, FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id));
CREATE TABLE predictions (prediction_id BIGINT PRIMARY KEY AUTO_INCREMENT, complaint_id BIGINT NOT NULL, predicted_hours DECIMAL(8,2) NOT NULL, model_name VARCHAR(100) NOT NULL, predicted_at DATETIME NOT NULL, FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id));
INSERT INTO categories(category_name,priority_level) VALUES ('Electricity',3),('Water Supply',3),('Roads & Infrastructure',2),('Waste Management & Sanitation',2),('Streetlights & Public Safety',3);
-- Create admin/staff through the app or a secure password-hashing utility. Do not store plaintext passwords.
