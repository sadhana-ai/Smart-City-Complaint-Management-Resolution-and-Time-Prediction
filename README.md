# Smart Complaint Management & Resolution Time Prediction

A Flask + HTML/CSS/JavaScript project with user registration/login, complaint submission and tracking, category management, staff assignment, resolution workflow, dashboard analytics, CSV reporting, and an ML-based estimated resolution time.

## Quick start (Windows / VS Code)
1. Install Python 3.10+ and open this folder in VS Code.
2. Open **Terminal → New Terminal**.
3. Run `py -m venv .venv`
4. Run `.venv\Scripts\activate`
5. Run `python -m pip install --upgrade pip`
6. Run `pip install -r requirements.txt`
7. Run `python app.py`
8. Open `http://127.0.0.1:5000` in your browser.

Alternatively, double-click `run_windows.bat`.

The SQLite database (`complaints.db`) and initial category rows are created automatically on first launch.

## Demo accounts
- Admin: `admin@complaintdesk.local` / `Admin@123`
- Staff: `staff@complaintdesk.local` / `Staff@123`
- Register a normal account using the Register page.

Change demo passwords and `SECRET_KEY` before deploying. This is a college/demo project; do not expose it publicly without production hardening, CSRF protection, rate limiting, secure cookie settings, and a production server.

## Features
- Role-aware user, staff, and admin views
- Complaint creation, category and priority selection, status tracking
- Admin category management and staff assignment
- Resolution notes and timestamps
- Estimated resolution hours saved to prediction history
- Dashboard KPIs, category/status/month charts, CSV report export
- Responsive custom CSS and interactive JavaScript
- `mysql_schema.sql` gives a MySQL 8+ schema matching the screenshot's tables

## About the prediction
The app trains a deterministic Random Forest regression model on synthetic demonstration examples when scikit-learn is installed. It uses category priority, complaint priority, and open-workload indicators. Because the included training data is synthetic, predictions are illustrative—not operational guarantees. For a real deployment, replace it with anonymized historical complaint records and evaluate MAE/RMSE/R² on a held-out test set. If scikit-learn is unavailable, a transparent formula fallback keeps the application usable.

## Project structure
- `app.py` — routes, authentication, SQLite data layer, prediction logic
- `templates/` — Jinja HTML pages
- `static/style.css` — custom responsive styling
- `static/app.js` — menu, charts and small interactions
- `mysql_schema.sql` — MySQL schema
