
import os
import sqlite3
import hashlib
import secrets
import csv
import io

from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, jsonify, Response
)


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "complaints.db")

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-development-secret-key"
)

CATEGORIES = [
    ("Electricity", 3),
    ("Water Supply", 3),
    ("Roads & Infrastructure", 2),
    ("Waste Management & Sanitation", 2),
    ("Streetlights & Public Safety", 3),
]


# ============================================================
# DATABASE SCHEMA
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL UNIQUE,
    priority_level INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS complaints (
    complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Medium',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',
    FOREIGN KEY(user_id) REFERENCES users(user_id),
    FOREIGN KEY(category_id) REFERENCES categories(category_id)
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL,
    staff_id INTEGER NOT NULL,
    assigned_at TEXT NOT NULL,
    FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id),
    FOREIGN KEY(staff_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS resolutions (
    resolution_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL UNIQUE,
    resolved_at TEXT NOT NULL,
    resolution_text TEXT NOT NULL,
    FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL,
    predicted_hours REAL NOT NULL,
    model_name TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    FOREIGN KEY(complaint_id) REFERENCES complaints(complaint_id)
);
"""


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)

    if conn is not None:
        conn.close()


# ============================================================
# PASSWORD SECURITY
# ============================================================

def hash_password(password):
    salt = secrets.token_hex(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        180000
    ).hex()

    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, digest = stored.split("$", 1)

        calculated = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            180000
        ).hex()

        return secrets.compare_digest(calculated, digest)

    except (ValueError, AttributeError):
        return False


# ============================================================
# DATABASE INITIALIZATION AND MIGRATION
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(SCHEMA)

    # Add is_active to older databases if it does not exist.
    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    }

    if "is_active" not in columns:
        conn.execute(
            """
            ALTER TABLE users
            ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
            """
        )

    # Insert default categories without duplicating existing ones.
    for name, priority in CATEGORIES:
        conn.execute(
            """
            INSERT OR IGNORE INTO categories
            (category_name, priority_level)
            VALUES (?, ?)
            """,
            (name, priority)
        )

    # Consolidate legacy Complaint Desk categories into the Smart City taxonomy.
    # Existing complaints are retained and reassigned to the closest civic category.
    category_map = {
        "Academic": "Roads & Infrastructure",
        "Administration": "Roads & Infrastructure",
        "Infrastructure": "Roads & Infrastructure",
        "IT Support": "Streetlights & Public Safety",
        "Other": "Roads & Infrastructure",
        "Safety": "Streetlights & Public Safety",
        "Sanitation": "Waste Management & Sanitation",
        "transport": "Roads & Infrastructure",
    }
    current_names = {name for name, _priority in CATEGORIES}
    for old_name, new_name in category_map.items():
        if old_name in current_names:
            continue
        old_row = conn.execute(
            "SELECT category_id FROM categories WHERE category_name = ?",
            (old_name,)
        ).fetchone()
        new_row = conn.execute(
            "SELECT category_id FROM categories WHERE category_name = ?",
            (new_name,)
        ).fetchone()
        if old_row and new_row:
            conn.execute(
                "UPDATE complaints SET category_id = ? WHERE category_id = ?",
                (new_row["category_id"], old_row["category_id"])
            )
            conn.execute(
                "DELETE FROM categories WHERE category_id = ?",
                (old_row["category_id"],)
            )

    # Create the default administrator if missing.
    admin = conn.execute(
        "SELECT user_id FROM users WHERE email = ?",
        ("admin@complaintdesk.local",)
    ).fetchone()

    if not admin:
        conn.execute(
            """
            INSERT INTO users
            (name, email, password_hash, role, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                "System Admin",
                "admin@complaintdesk.local",
                hash_password("Admin@123"),
                "admin",
                datetime.now().isoformat(timespec="seconds")
            )
        )

    # Create the default support staff account if missing.
    staff = conn.execute(
        "SELECT user_id FROM users WHERE email = ?",
        ("staff@complaintdesk.local",)
    ).fetchone()

    if not staff:
        conn.execute(
            """
            INSERT INTO users
            (name, email, password_hash, role, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                "Support Staff",
                "staff@complaintdesk.local",
                hash_password("Staff@123"),
                "staff",
                datetime.now().isoformat(timespec="seconds")
            )
        )

    conn.commit()
    conn.close()


# ============================================================
# AI RESOLUTION-TIME PREDICTION
# ============================================================

def predict_hours(category, priority, open_count=0, staff_load=0):
    cat_priority = {
        "Safety": 3,
        "Infrastructure": 2,
        "IT Support": 2,
        "Academic": 2,
        "Administration": 2,
        "Electricity": 3,
        "Water Supply": 3,
        "Roads & Infrastructure": 2,
        "Waste Management & Sanitation": 2,
        "Streetlights & Public Safety": 3,
        "Sanitation": 1,
        "Other": 1
    }.get(category, 1)

    priority_num = {
        "Low": 1,
        "Medium": 2,
        "High": 3,
        "Critical": 4
    }.get(priority, 2)

    try:
        import numpy as np
        from sklearn.ensemble import RandomForestRegressor

        X, y = [], []

        for c in range(1, 4):
            for p in range(1, 5):
                for load in range(0, 9):
                    for hist in range(0, 12, 3):
                        X.append([c, p, load, hist])

                        y.append(max(
                            2,
                            10 + c * 8 + (5 - p) * 7
                            + load * 2.8 + hist * 0.9
                        ))

        model = RandomForestRegressor(
            n_estimators=70,
            random_state=42,
            max_depth=8
        )

        model.fit(np.array(X), np.array(y))

        value = float(model.predict(np.array([[
            cat_priority,
            priority_num,
            min(open_count, 8),
            min(staff_load, 11)
        ]]))[0])

        return round(max(2, value), 1), "RandomForestRegressor"

    except Exception:
        # Fallback when ML dependencies are unavailable.
        value = (
            10 + cat_priority * 8
            + (5 - priority_num) * 7
            + min(open_count, 8) * 2.8
            + min(staff_load, 11) * 0.9
        )

        return round(max(2, value), 1), "Rule-based regression fallback"


# ============================================================
# AUTHENTICATION AND ROLE PROTECTION
# ============================================================

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))

        account = db().execute(
            """
            SELECT role, name, email, is_active
            FROM users
            WHERE user_id = ?
            """,
            (session["user_id"],)
        ).fetchone()

        if not account or not account["is_active"]:
            session.clear()

            flash(
                "Your account is inactive or unavailable. "
                "Contact the administrator.",
                "error"
            )

            return redirect(url_for("login"))

        # Refresh role and profile details from the database.
        session["role"] = account["role"]
        session["name"] = account["name"]
        session["email"] = account["email"]

        return fn(*args, **kwargs)

    return wrapped


def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if session.get("role") not in roles:
                flash(
                    "You do not have permission to access that page.",
                    "error"
                )
                return redirect(url_for("dashboard"))

            return fn(*args, **kwargs)

        return wrapped

    return deco


@app.context_processor
def inject_globals():
    return {"current_year": datetime.now().year}


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    return render_template("landing.html")


# ============================================================
# USER REGISTRATION
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 8:
            flash(
                "Enter your name, email, and a password with "
                "at least 8 characters.",
                "error"
            )
        else:
            try:
                db().execute(
                    """
                    INSERT INTO users
                    (name, email, password_hash, role, created_at, is_active)
                    VALUES (?, ?, ?, 'user', ?, 1)
                    """,
                    (
                        name,
                        email,
                        hash_password(password),
                        datetime.now().isoformat(timespec="seconds")
                    )
                )

                db().commit()

                flash(
                    "Account created. You can now sign in.",
                    "success"
                )

                return redirect(url_for("login"))

            except sqlite3.IntegrityError:
                flash("That email is already registered.", "error")

    return render_template("auth.html", mode="register")


# ============================================================
# LOGIN AND LOGOUT
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = db().execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if user and verify_password(password, user["password_hash"]):
            if not user["is_active"]:
                flash(
                    "Your account is inactive. Contact the administrator.",
                    "error"
                )
                return render_template("auth.html", mode="login")

            session.clear()
            session["user_id"] = user["user_id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            session["email"] = user["email"]

            return redirect(url_for("dashboard"))

        flash("Email or password is incorrect.", "error")

    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("index"))


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required

def dashboard():
    conn = db()
    role = session.get("role")
    uid = session.get("user_id")

    if role == "admin":
        where = ""
        args = ()
        allwhere = ""

    elif role == "staff":
        where = """WHERE c.complaint_id IN (
            SELECT a.complaint_id
            FROM assignments a
            WHERE a.staff_id = ?
              AND a.assignment_id = (
                  SELECT MAX(a2.assignment_id)
                  FROM assignments a2
                  WHERE a2.complaint_id = a.complaint_id
              )
        )"""
        args = (uid,)
        allwhere = """WHERE complaint_id IN (
            SELECT a.complaint_id
            FROM assignments a
            WHERE a.staff_id = ?
              AND a.assignment_id = (
                  SELECT MAX(a2.assignment_id)
                  FROM assignments a2
                  WHERE a2.complaint_id = a.complaint_id
              )
        )"""

    else:
        where = "WHERE c.user_id = ?"
        args = (uid,)
        allwhere = "WHERE user_id = ?"

    # Backfill a resolution record for complaints that were already marked
    # Resolved/Closed before resolution timestamps were being saved. This keeps
    # legacy closed complaints visible in the resolved and average-time metrics.
    legacy_resolution_time = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT OR IGNORE INTO resolutions
            (complaint_id, resolved_at, resolution_text)
        SELECT c.complaint_id, ?,
               'Resolution record added for a previously closed complaint.'
        FROM complaints c
        WHERE c.status IN ('Resolved', 'Closed')
          AND NOT EXISTS (
              SELECT 1 FROM resolutions r
              WHERE r.complaint_id = c.complaint_id
          )
        """,
        (legacy_resolution_time,)
    )
    conn.commit()

    rows = conn.execute(
        f"""
        SELECT c.*, cat.category_name, u.name AS reporter,
            (
                SELECT a.staff_id
                FROM assignments a
                WHERE a.complaint_id = c.complaint_id
                ORDER BY a.assignment_id DESC
                LIMIT 1
            ) AS staff_id,
            (
                SELECT u2.name
                FROM assignments a
                JOIN users u2 ON u2.user_id = a.staff_id
                WHERE a.complaint_id = c.complaint_id
                ORDER BY a.assignment_id DESC
                LIMIT 1
            ) AS staff_name
        FROM complaints c
        JOIN categories cat ON cat.category_id = c.category_id
        JOIN users u ON u.user_id = c.user_id
        {where}
        ORDER BY c.created_at DESC
        LIMIT 100
        """,
        args
    ).fetchall()

    total = conn.execute(
        f"SELECT COUNT(*) n FROM complaints {allwhere}",
        args
    ).fetchone()["n"]

    pending = conn.execute(
        f"""
        SELECT COUNT(*) n FROM complaints {allwhere}
        {"AND" if allwhere else "WHERE"}
        status NOT IN ('Resolved', 'Closed')
        """,
        args
    ).fetchone()["n"]

    resolved = conn.execute(
        f"""
        SELECT COUNT(*) n FROM complaints {allwhere}
        {"AND" if allwhere else "WHERE"}
        status IN ('Resolved', 'Closed')
        """,
        args
    ).fetchone()["n"]

    avg_sql = """
        SELECT AVG(
            MAX(0, (julianday(r.resolved_at) - julianday(c.created_at)) * 24)
        ) n
        FROM complaints c
        JOIN resolutions r ON r.complaint_id = c.complaint_id
        WHERE c.status IN ('Resolved', 'Closed')
    """

    if role == "user":
        avg_sql += " AND c.user_id = ?"

    elif role == "staff":
        avg_sql += """ AND c.complaint_id IN (
            SELECT a.complaint_id
            FROM assignments a
            WHERE a.staff_id = ?
              AND a.assignment_id = (
                  SELECT MAX(a2.assignment_id)
                  FROM assignments a2
                  WHERE a2.complaint_id = a.complaint_id
              )
        )"""

    avg = conn.execute(avg_sql, args).fetchone()["n"]

    category_sql = """
        SELECT cat.category_name name,
               COUNT(c.complaint_id) count
        FROM complaints c
        JOIN categories cat ON cat.category_id = c.category_id
    """

    if role == "user":
        category_sql += " WHERE c.user_id = ?"

    elif role == "staff":
        category_sql += """ WHERE c.complaint_id IN (
            SELECT a.complaint_id
            FROM assignments a
            WHERE a.staff_id = ?
              AND a.assignment_id = (
                  SELECT MAX(a2.assignment_id)
                  FROM assignments a2
                  WHERE a2.complaint_id = a.complaint_id
              )
        )"""

    category_sql += """
        GROUP BY cat.category_name
        ORDER BY count DESC
    """

    categories = conn.execute(
        category_sql, args
    ).fetchall()

    return render_template(
        "dashboard.html",
        complaints=rows,
        total=total,
        pending=pending,
        resolved=resolved,
        avg=round(avg, 1) if avg else 0,
        categories=categories
    )

# ============================================================
# CREATE A COMPLAINT
# STAFF ARE NOT ALLOWED TO CREATE COMPLAINTS
# ============================================================

@app.route("/complaints/new", methods=["GET", "POST"])
@login_required
@roles_required("user", "admin")
def new_complaint():
    cats = db().execute(
        "SELECT * FROM categories ORDER BY category_name"
    ).fetchall()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category_id = request.form.get("category_id")
        priority = request.form.get("priority", "Medium")

        if priority not in ["Low", "Medium", "High", "Critical"]:
            priority = "Medium"

        category = (
            db().execute(
                """
                SELECT category_id, category_name
                FROM categories
                WHERE category_id = ?
                """,
                (category_id,)
            ).fetchone()
            if category_id and category_id.isdigit()
            else None
        )

        if not title or len(description) < 10 or not category:
            flash(
                "Add a title, category, and description of at least "
                "10 characters.",
                "error"
            )
        else:
            cur = db().execute(
                """
                INSERT INTO complaints
                (user_id, category_id, title, description, priority,
                 created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    category["category_id"],
                    title,
                    description,
                    priority,
                    datetime.now().isoformat(timespec="seconds"),
                    "Open"
                )
            )

            cid = cur.lastrowid
            db().commit()

            open_count = db().execute(
                """
                SELECT COUNT(*) n FROM complaints
                WHERE status NOT IN ('Resolved', 'Closed')
                """
            ).fetchone()["n"]

            hours, model = predict_hours(
                category["category_name"],
                priority,
                open_count,
                open_count // 2
            )

            db().execute(
                """
                INSERT INTO predictions
                (complaint_id, predicted_hours, model_name, predicted_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    cid,
                    hours,
                    model,
                    datetime.now().isoformat(timespec="seconds")
                )
            )

            db().commit()

            flash(
                f"Complaint #{cid} submitted. Initial estimated "
                f"resolution: {hours:g} hours.",
                "success"
            )

            return redirect(
                url_for("complaint_detail", complaint_id=cid)
            )

    return render_template("complaint_form.html", categories=cats)


# ============================================================
# COMPLAINT DETAILS
# ============================================================

@app.route("/complaints/<int:complaint_id>")
@login_required
def complaint_detail(complaint_id):
    c = db().execute(
        """
        SELECT c.*, cat.category_name,
               u.name reporter, u.email reporter_email
        FROM complaints c
        JOIN categories cat ON cat.category_id = c.category_id
        JOIN users u ON u.user_id = c.user_id
        WHERE c.complaint_id = ?
        """,
        (complaint_id,)
    ).fetchone()

    if not c:
        flash("Complaint not found.", "error")
        return redirect(url_for("dashboard"))

    # Users can only view their own complaints.
    if (
        session["role"] == "user"
        and c["user_id"] != session["user_id"]
    ):
        flash("You cannot view that complaint.", "error")
        return redirect(url_for("dashboard"))

    assignment = db().execute(
        """
        SELECT a.*, u.name staff_name
        FROM assignments a
        JOIN users u ON u.user_id = a.staff_id
        WHERE complaint_id = ?
        ORDER BY assignment_id DESC
        LIMIT 1
        """,
        (complaint_id,)
    ).fetchone()

    resolution = db().execute(
        "SELECT * FROM resolutions WHERE complaint_id = ?",
        (complaint_id,)
    ).fetchone()

    prediction = db().execute(
        """
        SELECT * FROM predictions
        WHERE complaint_id = ?
        ORDER BY prediction_id DESC
        LIMIT 1
        """,
        (complaint_id,)
    ).fetchone()

    staff = db().execute(
        """
        SELECT user_id, name FROM users
        WHERE role = 'staff' AND is_active = 1
        ORDER BY name
        """
    ).fetchall()

    return render_template(
        "complaint_detail.html",
        c=c,
        assignment=assignment,
        resolution=resolution,
        prediction=prediction,
        staff=staff
    )


# ============================================================
# ADMIN: DELETE A COMPLAINT
# ============================================================

@app.route("/complaints/<int:complaint_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_complaint(complaint_id):
    conn = db()
    complaint = conn.execute(
        "SELECT complaint_id FROM complaints WHERE complaint_id = ?",
        (complaint_id,)
    ).fetchone()

    if not complaint:
        flash("Complaint not found or already deleted.", "error")
        return redirect(url_for("dashboard"))

    # Remove dependent records first to satisfy SQLite foreign-key constraints.
    conn.execute("DELETE FROM assignments WHERE complaint_id = ?", (complaint_id,))
    conn.execute("DELETE FROM resolutions WHERE complaint_id = ?", (complaint_id,))
    conn.execute("DELETE FROM predictions WHERE complaint_id = ?", (complaint_id,))
    conn.execute("DELETE FROM complaints WHERE complaint_id = ?", (complaint_id,))
    conn.commit()

    flash("Complaint deleted successfully.", "success")
    return redirect(url_for("dashboard"))


# ============================================================
# UPDATE COMPLAINT / ASSIGN STAFF
# ============================================================

@app.route(
    "/complaints/<int:complaint_id>/update",
    methods=["POST"]
)
@login_required
@roles_required("admin", "staff")
def update_complaint(complaint_id):
    c = db().execute(
        "SELECT * FROM complaints WHERE complaint_id = ?",
        (complaint_id,)
    ).fetchone()

    if not c:
        flash("Complaint not found.", "error")
        return redirect(url_for("dashboard"))

    status = request.form.get("status", c["status"])
    allowed = ["Open", "In Progress", "On Hold", "Resolved", "Closed"]

    if status not in allowed:
        status = c["status"]

    db().execute(
        "UPDATE complaints SET status = ? WHERE complaint_id = ?",
        (status, complaint_id)
    )

    # staff_id = request.form.get("staff_id", "").strip()



    # Only administrators can assign or reassign staff.
    staff_id = request.form.get("staff_id", "").strip()

    if session.get("role") == "admin" and staff_id:
        staff_member = db().execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
              AND role = 'staff'
              AND is_active = 1
            """,
            (staff_id,)
        ).fetchone()

        if not staff_member:
            db().rollback()
            flash("Selected staff account is unavailable.", "error")
            return redirect(
                url_for(
                    "complaint_detail",
                    complaint_id=complaint_id
                )
            )

        db().execute(
            """
            INSERT INTO assignments
                (complaint_id, staff_id, assigned_at)
            VALUES (?, ?, ?)
            """,
            (
                complaint_id,
                staff_member["user_id"],
                datetime.now().isoformat(timespec="seconds")
            )
        )










    if status in ("Resolved", "Closed"):
        text = (
            request.form.get("resolution_text", "").strip()
            or ("Complaint closed by support staff." if status == "Closed"
                else "Marked resolved by support staff.")
        )
        existing_resolution = db().execute(
            "SELECT resolution_id FROM resolutions WHERE complaint_id = ?",
            (complaint_id,)
        ).fetchone()

        if existing_resolution:
            # Preserve the original resolution timestamp when only editing notes.
            db().execute(
                "UPDATE resolutions SET resolution_text = ? WHERE complaint_id = ?",
                (text, complaint_id)
            )
        else:
            db().execute(
                """
                INSERT INTO resolutions
                    (complaint_id, resolved_at, resolution_text)
                VALUES (?, ?, ?)
                """,
                (
                    complaint_id,
                    datetime.now().isoformat(timespec="seconds"),
                    text
                )
            )

    db().commit()
    flash("Complaint updated successfully.", "success")

    return redirect(
        url_for("complaint_detail", complaint_id=complaint_id)
    )


# ============================================================
# ADMIN: USER MANAGEMENT
# ============================================================

@app.route("/users")
@login_required
@roles_required("admin")
def users_page():
    search = request.args.get("q", "").strip()

    if search:
        pattern = f"%{search}%"

        users = db().execute(
            """
            SELECT user_id, name, email, role, created_at, is_active
            FROM users
            WHERE name LIKE ?
               OR email LIKE ?
               OR role LIKE ?
               OR CAST(user_id AS TEXT) LIKE ?
            ORDER BY user_id
            """,
            (pattern, pattern, pattern, pattern)
        ).fetchall()
    else:
        users = db().execute(
            """
            SELECT user_id, name, email, role, created_at, is_active
            FROM users
            ORDER BY user_id
            """
        ).fetchall()

    total_accounts = db().execute(
        "SELECT COUNT(*) AS n FROM users"
    ).fetchone()["n"]

    return render_template(
        "users.html",
        users=users,
        search=search,
        total_accounts=total_accounts
    )


# ============================================================
# ADMIN: UPDATE USER DETAILS AND ROLE
# ============================================================

@app.route("/users/<int:user_id>/update", methods=["POST"])
@login_required
@roles_required("admin")
def update_user(user_id):
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "").strip().lower()

    allowed_roles = {"admin", "staff", "user"}

    user = db().execute(
        """
        SELECT user_id, role, is_active
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("users_page"))

    if not name or not email or role not in allowed_roles:
        flash("Enter a name, a valid email, and a valid role.", "error")
        return redirect(url_for("users_page"))

    if user_id == session.get("user_id") and role != "admin":
        flash("You cannot change your own administrator role.", "error")
        return redirect(url_for("users_page"))

    # Prevent demoting the last active administrator.
    if user["role"] == "admin" and user["is_active"] and role != "admin":
        active_admins = db().execute(
            """
            SELECT COUNT(*) AS n FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()["n"]

        if active_admins <= 1:
            flash(
                "The last active administrator cannot be demoted.",
                "error"
            )
            return redirect(url_for("users_page"))

    try:
        db().execute(
            """
            UPDATE users
            SET name = ?, email = ?, role = ?
            WHERE user_id = ?
            """,
            (name, email, role, user_id)
        )

        db().commit()

        if user_id == session.get("user_id"):
            session["name"] = name
            session["email"] = email
            session["role"] = role

        flash("Account details updated successfully.", "success")

    except sqlite3.IntegrityError:
        db().rollback()
        flash("That email address is already in use.", "error")

    return redirect(url_for("users_page"))


# ============================================================
# ADMIN: ACTIVATE / DEACTIVATE USER
# ============================================================

@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@roles_required("admin")
def toggle_user(user_id):
    user = db().execute(
        """
        SELECT user_id, role, is_active
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("users_page"))

    if user_id == session.get("user_id"):
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("users_page"))

    new_status = 0 if user["is_active"] else 1

    if user["role"] == "admin" and new_status == 0:
        active_admins = db().execute(
            """
            SELECT COUNT(*) AS n FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()["n"]

        if active_admins <= 1:
            flash(
                "The last active administrator cannot be deactivated.",
                "error"
            )
            return redirect(url_for("users_page"))

    db().execute(
        "UPDATE users SET is_active = ? WHERE user_id = ?",
        (new_status, user_id)
    )

    db().commit()

    flash(
        "Account activated successfully."
        if new_status
        else "Account deactivated successfully.",
        "success"
    )

    return redirect(url_for("users_page"))


# ============================================================
# ADMIN: DELETE USER
# ============================================================

@app.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("admin")
def delete_user(user_id):
    user = db().execute(
        """
        SELECT user_id, role, is_active
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if not user:
        flash("Account not found.", "error")
        return redirect(url_for("users_page"))

    if user_id == session.get("user_id"):
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("users_page"))

    if user["role"] == "admin" and user["is_active"]:
        active_admins = db().execute(
            """
            SELECT COUNT(*) AS n FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()["n"]

        if active_admins <= 1:
            flash(
                "The last active administrator cannot be deleted.",
                "error"
            )
            return redirect(url_for("users_page"))

    complaint_count = db().execute(
        "SELECT COUNT(*) AS n FROM complaints WHERE user_id = ?",
        (user_id,)
    ).fetchone()["n"]

    assignment_count = db().execute(
        "SELECT COUNT(*) AS n FROM assignments WHERE staff_id = ?",
        (user_id,)
    ).fetchone()["n"]

    if complaint_count or assignment_count:
        flash(
            "This account has complaint or assignment history and "
            "cannot be deleted. Deactivate it instead to preserve records.",
            "error"
        )
        return redirect(url_for("users_page"))

    try:
        db().execute(
            "DELETE FROM users WHERE user_id = ?",
            (user_id,)
        )

        db().commit()
        flash("Account deleted successfully.", "success")

    except sqlite3.IntegrityError:
        db().rollback()
        flash(
            "This account is linked to existing records. "
            "Deactivate it instead.",
            "error"
        )

    return redirect(url_for("users_page"))


# ============================================================
# ADMIN: CREATE STAFF ACCOUNT
# ============================================================

@app.route("/users/create-staff", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def create_staff():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("create_staff"))

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("create_staff"))

        existing = db().execute(
            "SELECT user_id FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        if existing:
            flash("An account with this email already exists.", "error")
            return redirect(url_for("create_staff"))

        try:
            db().execute(
                """
                INSERT INTO users
                (name, email, password_hash, role, created_at, is_active)
                VALUES (?, ?, ?, 'staff', ?, 1)
                """,
                (
                    name,
                    email,
                    hash_password(password),
                    datetime.now().isoformat(timespec="seconds")
                )
            )

            db().commit()
            flash("Staff account created successfully.", "success")
            return redirect(url_for("users_page"))

        except sqlite3.IntegrityError:
            db().rollback()
            flash("This email is already registered.", "error")
            return redirect(url_for("create_staff"))

    return render_template("staff_create.html")


# ============================================================
# ADMIN: CATEGORY MANAGEMENT
# ============================================================

@app.route("/categories", methods=["GET", "POST"])
@login_required
@roles_required("admin")
def categories_page():
    if request.method == "POST":
        name = request.form.get("category_name", "").strip()
        priority = request.form.get("priority_level", "1")

        try:
            priority = max(1, min(3, int(priority)))

            if name:
                db().execute(
                    """
                    INSERT INTO categories
                    (category_name, priority_level)
                    VALUES (?, ?)
                    """,
                    (name, priority)
                )

                db().commit()
                flash("Category added.", "success")
            else:
                flash("Category name is required.", "error")

        except ValueError:
            flash("Choose a valid category priority.", "error")

        except sqlite3.IntegrityError:
            flash("Category already exists.", "error")

        return redirect(url_for("categories_page"))

    cats = db().execute(
        """
        SELECT cat.*, COUNT(c.complaint_id) complaint_count
        FROM categories cat
        LEFT JOIN complaints c ON c.category_id = cat.category_id
        GROUP BY cat.category_id
        ORDER BY cat.category_name
        """
    ).fetchall()

    return render_template("categories.html", categories=cats)


# ============================================================
# ANALYTICS
# ============================================================

@app.route("/analytics")
@login_required
@roles_required("admin", "staff")

def analytics():
    conn = db()
    role = session.get("role")
    uid = session.get("user_id")

    # Admin sees all complaints; staff sees only their latest assignments.
    if role == "staff":
        scope = """
            c.complaint_id IN (
                SELECT a.complaint_id
                FROM assignments a
                WHERE a.staff_id = ?
                  AND a.assignment_id = (
                      SELECT MAX(a2.assignment_id)
                      FROM assignments a2
                      WHERE a2.complaint_id = a.complaint_id
                  )
            )
        """
        args = (uid,)
        complaint_where = "WHERE " + scope
    else:
        complaint_where = ""
        args = ()

    # Category-wise analytics
    cat = conn.execute(
        f"""
        SELECT cat.category_name AS name,
               COUNT(c.complaint_id) AS value
        FROM categories cat
        LEFT JOIN complaints c
            ON c.category_id = cat.category_id
            {"AND " + scope if role == "staff" else ""}
        GROUP BY cat.category_id
        ORDER BY value DESC
        """,
        args if role == "staff" else ()
    ).fetchall()

    # Status-wise analytics
    status = conn.execute(
        f"""
        SELECT status AS name, COUNT(*) AS value
        FROM complaints c
        {complaint_where}
        GROUP BY status
        """,
        args
    ).fetchall()

    # Monthly complaint totals
    monthly = conn.execute(
        f"""
        SELECT substr(c.created_at, 1, 7) AS month,
               COUNT(*) AS total
        FROM complaints c
        {complaint_where}
        GROUP BY substr(c.created_at, 1, 7)
        ORDER BY month DESC
        LIMIT 6
        """,
        args
    ).fetchall()[::-1]

    # Total complaints in the permitted scope
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM complaints c
        {complaint_where}
        """,
        args
    ).fetchone()["n"]

    # Resolved complaints in the permitted scope
    resolved = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM complaints c
        JOIN resolutions r
            ON r.complaint_id = c.complaint_id
        {complaint_where}
        """,
        args
    ).fetchone()["n"]

    # Average resolution time for the permitted complaints
    avg = conn.execute(
        f"""
        SELECT AVG(
            (julianday(r.resolved_at) -
             julianday(c.created_at)) * 24
        ) AS n
        FROM complaints c
        JOIN resolutions r
            ON r.complaint_id = c.complaint_id
        {complaint_where}
        """,
        args
    ).fetchone()["n"]

    return render_template(
        "analytics.html",
        cat=cat,
        status=status,
        monthly=monthly,
        total=total,
        resolved=resolved,
        avg=round(avg, 1) if avg else 0
    )


# ============================================================
# REFRESH AI PREDICTION
# ============================================================

@app.route("/prediction/<int:complaint_id>", methods=["POST"])
@login_required
def refresh_prediction(complaint_id):
    c = db().execute(
        """
        SELECT c.*, cat.category_name
        FROM complaints c
        JOIN categories cat ON cat.category_id = c.category_id
        WHERE complaint_id = ?
        """,
        (complaint_id,)
    ).fetchone()

    if not c:
        return jsonify({"error": "Complaint not found"}), 404

    if (
        session["role"] == "user"
        and c["user_id"] != session["user_id"]
    ):
        return jsonify({"error": "Forbidden"}), 403

    open_count = db().execute(
        """
        SELECT COUNT(*) n FROM complaints
        WHERE status NOT IN ('Resolved', 'Closed')
        """
    ).fetchone()["n"]

    hours, model = predict_hours(
        c["category_name"],
        c["priority"],
        open_count,
        open_count // 2
    )

    predicted_at = datetime.now().isoformat(timespec="seconds")

    db().execute(
        """
        INSERT INTO predictions
        (complaint_id, predicted_hours, model_name, predicted_at)
        VALUES (?, ?, ?, ?)
        """,
        (complaint_id, hours, model, predicted_at)
    )

    db().commit()

    return jsonify({
        "hours": hours,
        "model": model,
        "predicted_at": datetime.now().strftime(
            "%d %b %Y, %I:%M %p"
        )
    })


# ============================================================
# EXPORT COMPLAINT REPORT
# ============================================================

@app.route("/reports.csv")
@login_required
@roles_required("admin", "staff")
def reports_csv():
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Complaint ID",
        "Reporter",
        "Category",
        "Title",
        "Priority",
        "Status",
        "Created At",
        "Assigned Staff",
        "Predicted Hours",
        "Resolved At"
    ])

    rows = db().execute(
        """
        SELECT
            c.complaint_id,
            u.name,
            cat.category_name,
            c.title,
            c.priority,
            c.status,
            c.created_at,
            (
                SELECT u2.name
                FROM assignments a
                JOIN users u2 ON u2.user_id = a.staff_id
                WHERE a.complaint_id = c.complaint_id
                ORDER BY a.assignment_id DESC
                LIMIT 1
            ),
            (
                SELECT predicted_hours
                FROM predictions p
                WHERE p.complaint_id = c.complaint_id
                ORDER BY p.prediction_id DESC
                LIMIT 1
            ),
            (
                SELECT resolved_at
                FROM resolutions r
                WHERE r.complaint_id = c.complaint_id
            )
        FROM complaints c
        JOIN users u ON u.user_id = c.user_id
        JOIN categories cat ON cat.category_id = c.category_id
        ORDER BY c.created_at DESC
        """
    ).fetchall()

    writer.writerows([tuple(row) for row in rows])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=complaint-report.csv"
        }
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(404)
def not_found(e):
    return render_template(
        "error.html",
        code=404,
        message="We could not find that page."
    ), 404


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
