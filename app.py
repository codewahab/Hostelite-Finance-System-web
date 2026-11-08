from flask import Flask, render_template, request, redirect, url_for, flash, Response, session, g, has_app_context
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import calendar
import urllib.parse
import re
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from pdf_generator import generate_monthly_report_pdf, generate_split_bill_pdf, generate_expense_pdf
from models import db, User, Allowance, Room, Split, Expense, MoneyOwed


def load_dotenv_file(filepath=None):
    """Load key=value pairs from .env into os.environ if not already present."""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(filepath):
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


# Load local environment configuration if .env exists
load_dotenv_file()

def is_production():
    """Detect if running in production (Render, Vercel, or with FLASK_ENV=production)."""
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("VERCEL")
        or os.environ.get("FLASK_ENV") == "production"
    )

app = Flask(__name__)

# Secret Key Configuration
secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    if is_production():
        raise RuntimeError("CRITICAL: SECRET_KEY environment variable is required in production.")
    secret_key = "hostelite-secure-prod-key-98218941"
app.secret_key = secret_key

# Session Cookie Security Configuration
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if is_production():
    app.config["SESSION_COOKIE_SECURE"] = True

# Reverse Proxy Support for Render / Production Load Balancers
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# -------------------------------------------------------------
# Database Configuration (Supabase PostgreSQL / SQLAlchemy)
# -------------------------------------------------------------
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url[len("postgres://"):]

if database_url and (os.environ.get("STORAGE_BACKEND", "").lower() != "json" or is_production()):
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    engine_opts = {
        "pool_pre_ping": True,
    }
    if ":6543" in database_url:
        engine_opts["connect_args"] = {"options": "-c search_path=public"}
    if is_production():
        from sqlalchemy.pool import NullPool
        engine_opts["poolclass"] = NullPool
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts
    db.init_app(app)

# -------------------------------------------------------------
# Google OAuth 2.0 Configuration (Authlib)
# -------------------------------------------------------------
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile"
    }
)



# -------------------------------------------------------------
# Jinja Custom Filters
# -------------------------------------------------------------
def format_rupees(amount):
    """Format numbers into clean Pakistani Rupees (e.g. Rs. 30,000 or Rs. 250.50)."""
    try:
        val = float(amount)
        sign = "-" if val < 0 else ""
        val = abs(val)
        if val.is_integer():
            return f"{sign}Rs. {int(val):,}"
        else:
            return f"{sign}Rs. {val:,.2f}"
    except (ValueError, TypeError):
        return f"Rs. {amount}"

def urlencode_filter(s):
    """URL encode text for WhatsApp and web sharing links."""
    return urllib.parse.quote_plus(str(s))

app.jinja_env.filters["rupees"] = format_rupees
app.jinja_env.filters["urlencode"] = urlencode_filter


# -------------------------------------------------------------
# Name Normalization Helper
# -------------------------------------------------------------
def normalize_person_name(name, existing_records=None, existing_rooms=None):
    """
    Standardize person names by stripping whitespace and doing case-insensitive matching.
    If the name matches an existing recorded person (e.g. 'Ali'), return that canonical casing.
    Otherwise return clean title-cased name (e.g. 'Saad').
    """
    if not name:
        return ""
    clean = " ".join(str(name).strip().split())
    if not clean:
        return ""

    # Check if this person already exists in money_owed records
    if existing_records:
        for r in existing_records:
            existing = r.get("person", "").strip()
            if existing.lower() == clean.lower():
                return existing

    # Check if exists in rooms
    if existing_rooms:
        for room in existing_rooms:
            for member in room.get("members", []):
                if member.strip().lower() == clean.lower():
                    return member.strip()

    # Default to Title Case
    return clean.title()

USERS_FILE = "users.json"
USER_STORAGE_BASE = "user_data"

def is_db_active():
    """Check if database storage backend is configured and active."""
    if not has_app_context():
        return False
    if app.config.get("TESTING") and not app.config.get("TEST_DATABASE"):
        return False
    if is_production():
        if not app.config.get("SQLALCHEMY_DATABASE_URI"):
            raise RuntimeError(
                "CRITICAL: DATABASE_URL is not configured in production. "
                "Silently falling back to ephemeral local JSON storage is prohibited."
            )
        return True
    backend = os.environ.get("STORAGE_BACKEND", "").strip().lower()
    return bool(app.config.get("SQLALCHEMY_DATABASE_URI")) and backend in ("database", "supabase", "postgres", "postgresql")

def get_active_user_id(user_id=None):
    """Resolve the active user ID from param, request context, session, or test config."""
    if user_id is not None:
        try:
            return int(user_id)
        except (ValueError, TypeError):
            return None
    try:
        if hasattr(g, "user_id") and g.user_id:
            return int(g.user_id)
        if "user_id" in session:
            return int(session.get("user_id"))
    except RuntimeError:
        pass
    if app.config.get("TEST_ACTIVE_USER_ID"):
        return int(app.config.get("TEST_ACTIVE_USER_ID"))
    return None

def parse_date(date_val):
    """Safely parse date from string or date object."""
    if not date_val:
        return None
    if isinstance(date_val, (date, datetime)):
        return date_val if isinstance(date_val, date) else date_val.date()
    try:
        return datetime.strptime(str(date_val).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def load_users():
    """Load all registered users from database or users.json."""
    if is_db_active():
        return [u.to_dict() for u in User.query.all()]
    if is_production():
        return []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_users(users):
    """Save registered users to database and backup to users.json."""
    if is_db_active():
        try:
            existing = {u.id: u for u in User.query.all()}
            for u in users:
                u_id = int(u["id"])
                created_dt = parse_date(u.get("created_at"))
                created_datetime = datetime.combine(created_dt, datetime.min.time()) if created_dt else datetime.now(timezone.utc)
                if u_id in existing:
                    user_obj = existing[u_id]
                    user_obj.username = u.get("username", user_obj.username)
                    user_obj.display_name = u.get("display_name", user_obj.display_name)
                    user_obj.email = u.get("email") or None
                    user_obj.email_verified = bool(u.get("email_verified", False))
                    user_obj.password_hash = u.get("password_hash")
                    user_obj.google_id = u.get("google_id") or None
                    user_obj.auth_provider = u.get("auth_provider", "local")
                else:
                    new_user = User(
                        id=u_id,
                        username=u.get("username"),
                        display_name=u.get("display_name") or u.get("username"),
                        email=u.get("email") or None,
                        email_verified=bool(u.get("email_verified", False)),
                        password_hash=u.get("password_hash"),
                        google_id=u.get("google_id") or None,
                        auth_provider=u.get("auth_provider", "local"),
                        created_at=created_datetime
                    )
                    db.session.add(new_user)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=4)
    except Exception:
        pass

def find_user_by_username(username):
    """Lookup user by case-insensitive username."""
    if not username:
        return None
    clean = username.strip().lower()
    if is_db_active():
        u = User.query.filter(db.func.lower(User.username) == clean).first()
        return u.to_dict() if u else None
    if is_production():
        return None
    users = load_users()
    for u in users:
        if u.get("username", "").strip().lower() == clean:
            return u
    return None

def find_user_by_id(user_id):
    """Lookup user by unique integer or string ID."""
    if not user_id:
        return None
    if is_db_active():
        u = db.session.get(User, int(user_id))
        return u.to_dict() if u else None
    if is_production():
        return None
    users = load_users()
    for u in users:
        if str(u.get("id")) == str(user_id):
            return u
    return None

def find_user_by_google_id(google_id):
    """Lookup user by stable Google subject identifier (sub)."""
    if not google_id:
        return None
    gid = str(google_id).strip()
    if is_db_active():
        u = User.query.filter_by(google_id=gid).first()
        return u.to_dict() if u else None
    if is_production():
        return None
    users = load_users()
    for u in users:
        if str(u.get("google_id", "")).strip() == gid:
            return u
    return None

def find_user_by_email(email):
    """Lookup user by email address (case-insensitive)."""
    if not email:
        return None
    clean = email.strip().lower()
    if is_db_active():
        u = User.query.filter(db.func.lower(User.email) == clean).first()
        return u.to_dict() if u else None
    if is_production():
        return None
    users = load_users()
    for u in users:
        if u.get("email", "").strip().lower() == clean:
            return u
    return None

def init_user_storage(user_id):
    """Ensure user directory and initial database records exist."""
    u_id = get_active_user_id(user_id)
    if is_db_active() and u_id:
        try:
            al = Allowance.query.filter_by(user_id=u_id).first()
            if not al:
                db.session.add(Allowance(user_id=u_id, amount=Decimal("0.0"), received_date=None))
                db.session.commit()
        except Exception:
            db.session.rollback()

    if is_production():
        return

    user_dir = os.path.join(USER_STORAGE_BASE, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    
    files_with_defaults = {
        "expenses.json": [],
        "allowance.json": {"allowance": 0.0, "received_date": ""},
        "money_owed.json": [],
        "splits.json": [],
        "rooms.json": []
    }
    
    users = load_users()
    is_first_user = (len(users) <= 1)
    
    for filename, default_val in files_with_defaults.items():
        user_file = os.path.join(user_dir, filename)
        if not os.path.exists(user_file):
            if is_first_user and os.path.exists(filename):
                try:
                    with open(filename, "r", encoding="utf-8") as src:
                        data = json.load(src)
                    with open(user_file, "w", encoding="utf-8") as dst:
                        json.dump(data, dst, indent=4)
                except Exception:
                    with open(user_file, "w", encoding="utf-8") as dst:
                        json.dump(default_val, dst, indent=4)
            else:
                with open(user_file, "w", encoding="utf-8") as dst:
                    json.dump(default_val, dst, indent=4)

def get_user_storage_path(filename, user_id=None):
    """Get the file path for a storage file, scoped to the current user."""
    if user_id is None:
        try:
            if hasattr(g, "user_id") and g.user_id:
                user_id = g.user_id
            elif "user_id" in session:
                user_id = session.get("user_id")
        except RuntimeError:
            user_id = None

    if user_id is None and app.config.get("TEST_ACTIVE_USER_ID"):
        user_id = app.config.get("TEST_ACTIVE_USER_ID")

    if user_id:
        user_dir = os.path.join(USER_STORAGE_BASE, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, filename)
    return filename

@app.before_request
def load_logged_in_user():
    try:
        g.user_id = session.get("user_id")
        if g.user_id:
            g.user = find_user_by_id(g.user_id)
        else:
            g.user = None
    except Exception:
        g.user_id = None
        g.user = None

def login_required(f):
    """Decorator to ensure route requires authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if app.config.get("TESTING") and app.config.get("TEST_ACTIVE_USER_ID"):
            return f(*args, **kwargs)
        if "user_id" not in session:
            flash("Please sign in to access your finances.", "warning")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


# -------------------------------------------------------------
# Storage Helper Functions (Dual-Backend: Database & JSON File)
# -------------------------------------------------------------
def load_expenses(user_id=None):
    """Load all expenses for user and ensure every expense has a unique id."""
    u_id = get_active_user_id(user_id)
    if is_db_active():
        if not u_id:
            return []
        records = Expense.query.filter_by(user_id=u_id).order_by(Expense.date.desc(), Expense.id.desc()).all()
        return [r.to_dict() for r in records]
    if is_production():
        return []

    filepath = get_user_storage_path("expenses.json", user_id)
    needs_save = False
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            expenses = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        expenses = []

    base_id = int(time.time() * 1000)
    for idx, exp in enumerate(expenses):
        if "id" not in exp:
            exp["id"] = base_id + idx
            needs_save = True

    if needs_save:
        save_expenses(expenses, user_id=user_id)

    return expenses

def save_expenses(expenses, user_id=None):
    """Save the list of expenses back to database and user's expenses.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active() and u_id:
        try:
            existing = {e.id: e for e in Expense.query.filter_by(user_id=u_id).all()}
            incoming_ids = set()
            for item in expenses:
                e_id = int(item.get("id"))
                incoming_ids.add(e_id)
                e_date = parse_date(item.get("date")) or date.today()
                s_id = item.get("split_id")
                if s_id and not db.session.get(Split, int(s_id)):
                    s_id = None
                global_exp = db.session.get(Expense, e_id)
                if global_exp and global_exp.user_id != u_id:
                    raise ValueError(f"Cross-user ID collision: Expense ID {e_id} belongs to user {global_exp.user_id}, cannot assign to user {u_id}")
                if e_id in existing:
                    exp_obj = existing[e_id]
                    exp_obj.name = item.get("name", exp_obj.name)
                    exp_obj.category = item.get("category", exp_obj.category)
                    exp_obj.amount = Decimal(str(item.get("amount", 0.0)))
                    exp_obj.date = e_date
                    exp_obj.note = item.get("note", "") or ""
                    exp_obj.payment_method = item.get("payment_method", "Cash") or "Cash"
                    exp_obj.split_id = int(s_id) if s_id else None
                else:
                    new_exp = Expense(
                        id=e_id,
                        user_id=u_id,
                        name=item.get("name", "Expense"),
                        category=item.get("category", "General"),
                        amount=Decimal(str(item.get("amount", 0.0))),
                        date=e_date,
                        note=item.get("note", "") or "",
                        payment_method=item.get("payment_method", "Cash") or "Cash",
                        split_id=int(s_id) if s_id else None
                    )
                    db.session.add(new_exp)
            for e_id, record in existing.items():
                if e_id not in incoming_ids:
                    db.session.delete(record)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return

    filepath = get_user_storage_path("expenses.json", user_id)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(expenses, file, indent=4)
    except Exception:
        pass

def load_allowance(user_id=None):
    """Load monthly allowance data from database or user's allowance.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active():
        if not u_id:
            return {"amount": 0.0, "allowance": 0.0, "received_date": ""}
        row = Allowance.query.filter_by(user_id=u_id).first()
        if row:
            return row.to_dict()
        return {"amount": 0.0, "allowance": 0.0, "received_date": ""}
    if is_production():
        return {"amount": 0.0, "allowance": 0.0, "received_date": ""}

    filepath = get_user_storage_path("allowance.json", user_id)
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            data = json.load(file)
            return {
                "amount": float(data.get("allowance", 0.0)),
                "allowance": float(data.get("allowance", 0.0)),
                "received_date": data.get("received_date", "")
            }
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {"amount": 0.0, "allowance": 0.0, "received_date": ""}

def save_allowance(amount, received_date="", user_id=None):
    """Save allowance and received date to database and user's allowance.json."""
    u_id = get_active_user_id(user_id)
    parsed_d = parse_date(received_date)
    rec_date_str = parsed_d.strftime("%Y-%m-%d") if parsed_d else ""
    if is_db_active() and u_id:
        try:
            row = Allowance.query.filter_by(user_id=u_id).first()
            if not row:
                row = Allowance(user_id=u_id, amount=Decimal(str(amount)), received_date=parsed_d)
                db.session.add(row)
            else:
                row.amount = Decimal(str(amount))
                row.received_date = parsed_d
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return

    filepath = get_user_storage_path("allowance.json", user_id)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump({"allowance": float(amount), "received_date": rec_date_str}, file, indent=4)
    except Exception:
        pass

def load_money_owed(user_id=None):
    """Load money owed records from database or user's money_owed.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active():
        if not u_id:
            return []
        records = MoneyOwed.query.filter_by(user_id=u_id).order_by(MoneyOwed.date.desc(), MoneyOwed.id.desc()).all()
        return [r.to_dict() for r in records]
    if is_production():
        return []

    filepath = get_user_storage_path("money_owed.json", user_id)
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_money_owed(records, user_id=None):
    """Save money owed records to database and user's money_owed.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active() and u_id:
        try:
            existing = {m.id: m for m in MoneyOwed.query.filter_by(user_id=u_id).all()}
            incoming_ids = set()
            for item in records:
                m_id = int(item.get("id"))
                incoming_ids.add(m_id)
                m_date = parse_date(item.get("date")) or date.today()
                s_id = item.get("split_id")
                if s_id and not db.session.get(Split, int(s_id)):
                    s_id = None
                global_debt = db.session.get(MoneyOwed, m_id)
                if global_debt and global_debt.user_id != u_id:
                    raise ValueError(f"Cross-user ID collision: MoneyOwed ID {m_id} belongs to user {global_debt.user_id}, cannot assign to user {u_id}")
                if m_id in existing:
                    debt_obj = existing[m_id]
                    debt_obj.person = item.get("person", debt_obj.person)
                    debt_obj.type = item.get("type", debt_obj.type)
                    debt_obj.amount = Decimal(str(item.get("amount", 0.0)))
                    debt_obj.date = m_date
                    debt_obj.reason = item.get("reason", "") or ""
                    debt_obj.note = item.get("note", "") or ""
                    debt_obj.status = item.get("status", "Pending") or "Pending"
                    debt_obj.split_id = int(s_id) if s_id else None
                else:
                    new_debt = MoneyOwed(
                        id=m_id,
                        user_id=u_id,
                        person=item.get("person", "Friend"),
                        type=item.get("type", "they_owe_me"),
                        amount=Decimal(str(item.get("amount", 0.0))),
                        date=m_date,
                        reason=item.get("reason", "") or "",
                        note=item.get("note", "") or "",
                        status=item.get("status", "Pending") or "Pending",
                        split_id=int(s_id) if s_id else None
                    )
                    db.session.add(new_debt)
            for m_id, record in existing.items():
                if m_id not in incoming_ids:
                    db.session.delete(record)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return

    filepath = get_user_storage_path("money_owed.json", user_id)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(records, file, indent=4)
    except Exception:
        pass

def load_splits(user_id=None):
    """Load bill splitting history from database or user's splits.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active():
        if not u_id:
            return []
        try:
            records = Split.query.filter_by(user_id=u_id).order_by(Split.date.desc(), Split.id.desc()).all()
            return [s.to_dict() for s in records]
        except Exception:
            pass
    if is_production():
        return []

    filepath = get_user_storage_path("splits.json", user_id)
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_splits(records, user_id=None):
    """Save bill splitting history to database and user's splits.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active() and u_id:
        try:
            existing = {s.id: s for s in Split.query.filter_by(user_id=u_id).all()}
            incoming_ids = set()
            for item in records:
                s_id = int(item.get("id"))
                incoming_ids.add(s_id)
                s_date = parse_date(item.get("date")) or date.today()
                global_split = db.session.get(Split, s_id)
                if global_split and global_split.user_id != u_id:
                    raise ValueError(f"Cross-user ID collision: Split ID {s_id} belongs to user {global_split.user_id}, cannot assign to user {u_id}")
                if s_id in existing:
                    split_obj = existing[s_id]
                    split_obj.title = item.get("title", split_obj.title)
                    split_obj.total_amount = Decimal(str(item.get("total_amount", 0.0)))
                    split_obj.payer = item.get("payer", split_obj.payer)
                    split_obj.date = s_date
                    split_obj.split_mode = item.get("split_mode", "room")
                    split_obj.split_type = item.get("split_type", "equal")
                    split_obj.participants = item.get("participants", [])
                    split_obj.breakdown = item.get("breakdown", [])
                    split_obj.who_owes_whom = item.get("who_owes_whom", [])
                else:
                    new_split = Split(
                        id=s_id,
                        user_id=u_id,
                        title=item.get("title", "Split Bill"),
                        total_amount=Decimal(str(item.get("total_amount", 0.0))),
                        payer=item.get("payer", "You"),
                        date=s_date,
                        split_mode=item.get("split_mode", "room"),
                        split_type=item.get("split_type", "equal"),
                        participants=item.get("participants", []),
                        breakdown=item.get("breakdown", []),
                        who_owes_whom=item.get("who_owes_whom", [])
                    )
                    db.session.add(new_split)
            for s_id, record in existing.items():
                if s_id not in incoming_ids:
                    db.session.delete(record)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return

    filepath = get_user_storage_path("splits.json", user_id)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(records, file, indent=4)
    except Exception:
        pass

def load_rooms(user_id=None):
    """Load rooms and roommate rosters from database or user's rooms.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active():
        if not u_id:
            return []
        try:
            records = Room.query.filter_by(user_id=u_id).all()
            return [r.to_dict() for r in records]
        except Exception:
            pass
    if is_production():
        return []

    filepath = get_user_storage_path("rooms.json", user_id)
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_rooms(rooms, user_id=None):
    """Save rooms roster to database and user's rooms.json."""
    u_id = get_active_user_id(user_id)
    if is_db_active() and u_id:
        try:
            existing = {r.id: r for r in Room.query.filter_by(user_id=u_id).all()}
            incoming_ids = set()
            for item in rooms:
                r_id = int(item.get("id"))
                incoming_ids.add(r_id)
                global_room = db.session.get(Room, r_id)
                if global_room and global_room.user_id != u_id:
                    raise ValueError(f"Cross-user ID collision: Room ID {r_id} belongs to user {global_room.user_id}, cannot assign to user {u_id}")
                if r_id in existing:
                    room_obj = existing[r_id]
                    room_obj.name = item.get("name", room_obj.name)
                    room_obj.members = item.get("members", [])
                else:
                    new_room = Room(
                        id=r_id,
                        user_id=u_id,
                        name=item.get("name", "Room"),
                        members=item.get("members", [])
                    )
                    db.session.add(new_room)
            for r_id, record in existing.items():
                if r_id not in incoming_ids:
                    db.session.delete(record)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
    if is_production():
        return

    filepath = get_user_storage_path("rooms.json", user_id)
    try:
        with open(filepath, "w", encoding="utf-8") as file:
            json.dump(rooms, file, indent=4)
    except Exception:
        pass


# -------------------------------------------------------------
# Budget & Cycle Calculation Helpers
# -------------------------------------------------------------
def get_cycle_info(received_date_str):
    """
    Calculate the 1-month budget cycle based on the allowance received date.
    Does NOT assume cycle begins on the 1st of the month.
    """
    today = date.today()
    if not received_date_str:
        # Default fallback: calendar month
        _, total_days = calendar.monthrange(today.year, today.month)
        cycle_start = date(today.year, today.month, 1)
        cycle_end = date(today.year, today.month, total_days)
        days_rem = max(1, total_days - today.day + 1)
        return cycle_start, cycle_end, days_rem

    try:
        rec_date = datetime.strptime(received_date_str, "%Y-%m-%d").date()
    except ValueError:
        return get_cycle_info(None)

    # 1 month cycle from received_date
    y = rec_date.year
    m = rec_date.month
    d = rec_date.day

    next_m = m + 1 if m < 12 else 1
    next_y = y if m < 12 else y + 1
    max_d_in_next_m = calendar.monthrange(next_y, next_m)[1]
    next_cycle_start = date(next_y, next_m, min(d, max_d_in_next_m))
    cycle_end = next_cycle_start - timedelta(days=1)

    diff = (cycle_end - today).days + 1
    days_rem = max(1, diff) if diff > 0 else 1
    return rec_date, cycle_end, days_rem


# -------------------------------------------------------------
# 0. AUTHENTICATION & PROFILE ROUTES (REGISTER, LOGIN, LOGOUT)
# -------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip() or username
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        # Validation
        if not username or len(username) < 3:
            flash("Username must be at least 3 characters long.", "error")
            return render_template("register.html", username=username, display_name=display_name)

        if not re.match(r"^[a-zA-Z0-9_.-]+$", username):
            flash("Username can only contain letters, numbers, dots, and underscores.", "error")
            return render_template("register.html", username=username, display_name=display_name)

        if not password or len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return render_template("register.html", username=username, display_name=display_name)

        if password != confirm_password:
            flash("Passwords do not match. Please re-enter.", "error")
            return render_template("register.html", username=username, display_name=display_name)

        if find_user_by_username(username):
            flash(f"Username '{username}' is already taken. Please pick another.", "error")
            return render_template("register.html", username=username, display_name=display_name)

        # Create user
        users = load_users()
        user_id = int(time.time() * 1000)
        new_user = {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "password_hash": generate_password_hash(password),
            "created_at": date.today().isoformat()
        }
        users.append(new_user)
        save_users(users)

        # Initialize private data folder
        init_user_storage(user_id)

        # Automatically log in the user
        session["user_id"] = user_id
        session["username"] = username
        session["display_name"] = display_name

        flash(f"Welcome, {display_name}! Your account has been created successfully.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))

    next_url = request.args.get("next")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please enter both username and password.", "error")
            return render_template("login.html", username=username, next=next_url)

        user = find_user_by_username(username)
        if not user or not check_password_hash(user.get("password_hash", ""), password):
            flash("Invalid username or password. Please try again.", "error")
            return render_template("login.html", username=username, next=next_url)

        # Login successful
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["display_name"] = user.get("display_name", user["username"])

        # Ensure user folder exists
        init_user_storage(user["id"])

        flash(f"Welcome back, {session['display_name']}!", "success")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("home"))

    return render_template("login.html", next=next_url)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("login"))


# -------------------------------------------------------------
# Google OAuth 2.0 Routes
# -------------------------------------------------------------
def get_oauth_redirect_uri():
    """
    Generate external redirect URI for Google OAuth callback.
    Safeguards HTTPS for reverse proxies like Render / PythonAnywhere.
    Allows optional GOOGLE_REDIRECT_URI override from environment.
    """
    custom_uri = os.environ.get("GOOGLE_REDIRECT_URI")
    if custom_uri and custom_uri.strip():
        return custom_uri.strip()

    redirect_uri = url_for("google_callback", _external=True)
    proto = request.headers.get("X-Forwarded-Proto")
    if (proto == "https" or is_production()) and redirect_uri.startswith("http://"):
        redirect_uri = "https://" + redirect_uri[len("http://"):]
    return redirect_uri


@app.route("/login/google")
def login_google():
    """Initiate Google OAuth 2.0 sign-in flow."""
    if "user_id" in session:
        return redirect(url_for("home"))

    if "GOOGLE_CLIENT_ID" not in os.environ or "GOOGLE_CLIENT_SECRET" not in os.environ:
        load_dotenv_file()

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        flash("Google Sign-In is not configured yet. Please configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your environment or .env file.", "warning")
        return redirect(url_for("login"))

    oauth.google.client_id = client_id
    oauth.google.client_secret = client_secret

    redirect_uri = get_oauth_redirect_uri()
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    """Handle callback from Google OAuth 2.0."""
    if "user_id" in session:
        return redirect(url_for("home"))

    # 1. Handle user cancellation or errors returned in query params
    error = request.args.get("error")
    if error:
        if error == "access_denied":
            flash("Google sign-in was cancelled.", "info")
        else:
            flash(f"Google sign-in was not completed: {error}", "error")
        return redirect(url_for("login"))

    if "GOOGLE_CLIENT_ID" not in os.environ or "GOOGLE_CLIENT_SECRET" not in os.environ:
        load_dotenv_file()

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        flash("Google Sign-In is not configured yet. Please configure GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.", "warning")
        return redirect(url_for("login"))

    oauth.google.client_id = client_id
    oauth.google.client_secret = client_secret

    # 2. Token exchange
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Google authentication could not be completed. Please try again.", "error")
        return redirect(url_for("login"))

    if not token:
        flash("Authentication failed. No token received from Google.", "error")
        return redirect(url_for("login"))

    # 3. Retrieve verified userinfo (from ID token or userinfo endpoint)
    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            resp = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo")
            userinfo = resp.json()
        except Exception:
            userinfo = None

    if not userinfo or not userinfo.get("sub"):
        flash("Failed to retrieve verified account details from Google.", "error")
        return redirect(url_for("login"))

    google_sub = str(userinfo.get("sub")).strip()
    email = str(userinfo.get("email", "")).strip().lower()
    name = str(userinfo.get("name", "")).strip() or str(userinfo.get("given_name", "")).strip()
    email_verified = userinfo.get("email_verified", False)

    users = load_users()

    # 4. Check if an account already has this stable Google sub ID
    user = find_user_by_google_id(google_sub)
    if user:
        # Existing user: login directly, strictly preserve user files
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["display_name"] = user.get("display_name", user["username"])
        init_user_storage(user["id"])
        flash(f"Welcome back, {session['display_name']}!", "success")
        return redirect(url_for("home"))

    # 5. Collision check: email exists on an account without this google_id
    # Requirement 7: Do not automatically merge accounts solely because their email addresses match.
    if email:
        existing_by_email = find_user_by_email(email)
        if existing_by_email:
            flash(
                f"An account with email '{email}' already exists. "
                "For your security, automatic merging is disabled. "
                "Please sign in with your username and password.",
                "warning"
            )
            return redirect(url_for("login"))

    # 6. New user registration: generate unique, clean username
    base_candidate = ""
    if email:
        base_candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", email.split("@")[0])
    if not base_candidate and name:
        base_candidate = re.sub(r"[^a-zA-Z0-9_.-]", "", name.lower().replace(" ", "_"))
    if not base_candidate or len(base_candidate) < 3:
        base_candidate = f"hostelite_{int(time.time()) % 100000}"

    candidate_username = base_candidate
    counter = 1
    while find_user_by_username(candidate_username):
        candidate_username = f"{base_candidate}_{counter}"
        counter += 1

    display_name = name if name else candidate_username
    new_user_id = int(time.time() * 1000)
    new_user = {
        "id": new_user_id,
        "username": candidate_username,
        "display_name": display_name,
        "email": email,
        "email_verified": bool(email_verified),
        "google_id": google_sub,
        "auth_provider": "google",
        "created_at": date.today().isoformat()
    }

    users.append(new_user)
    save_users(users)

    # Initialize isolated private user storage folder
    init_user_storage(new_user_id)

    # Establish session
    session["user_id"] = new_user_id
    session["username"] = candidate_username
    session["display_name"] = display_name

    flash(f"Welcome to Hostelite Finance, {display_name}!", "success")
    return redirect(url_for("home"))



@app.route("/backup-data")
@login_required
def backup_data():
    """Download user's complete data bundle as a JSON file."""
    user_id = session.get("user_id")
    bundle = {
        "app": "Hostelite Finance",
        "exported_at": date.today().isoformat(),
        "username": session.get("username"),
        "display_name": session.get("display_name"),
        "allowance": load_allowance(user_id=user_id),
        "expenses": load_expenses(user_id=user_id),
        "money_owed": load_money_owed(user_id=user_id),
        "splits": load_splits(user_id=user_id),
        "rooms": load_rooms(user_id=user_id)
    }
    json_bytes = json.dumps(bundle, indent=4).encode("utf-8")
    return Response(
        json_bytes,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=hostelite_backup_{session.get('username')}_{date.today().isoformat()}.json"}
    )


# -------------------------------------------------------------
# 1. DASHBOARD & QUICK ADD ROUTES
# -------------------------------------------------------------
@app.route("/")
@login_required
def home():
    expenses = load_expenses()
    allowance_info = load_allowance()
    allowance = allowance_info["amount"]
    received_date = allowance_info["received_date"]

    cycle_start, cycle_end, days_remaining = get_cycle_info(received_date)

    # Calculate expenses within the current allowance cycle
    if received_date:
        cycle_expenses = [e for e in expenses if e.get("date", "") >= received_date]
    else:
        cycle_expenses = expenses

    total_expense = sum(float(e.get("amount", 0)) for e in cycle_expenses)
    all_time_total = sum(float(e.get("amount", 0)) for e in expenses)

    # Budget calculations (Allowance stays the fixed baseline)
    remaining = allowance - total_expense
    percentage_spent = round((total_expense / allowance) * 100, 1) if allowance > 0 else 0.0

    if allowance > 0 and remaining > 0:
        daily_limit = round(remaining / days_remaining, 2)
    else:
        daily_limit = 0.0

    is_over_budget = (allowance > 0 and remaining < 0)

    # Money Owed overview
    money_owed_records = load_money_owed()
    others_owe_you = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "they_owe_me" and r.get("status") == "Pending")
    you_owe_others = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "i_owe_them" and r.get("status") == "Pending")
    pending_records_count = sum(1 for r in money_owed_records if r.get("status") == "Pending")

    # Available Cash calculation (Cash in hand):
    # Allowance Remaining + Cash borrowed from friends - Cash lent to friends
    if allowance > 0:
        available_cash = remaining + you_owe_others - others_owe_you
    else:
        available_cash = you_owe_others - others_owe_you

    # ---------------------------------------------------------
    # Daily Safe Spending Limit Warning System (Thresholds: Rs. 300, Rs. 200)
    # ---------------------------------------------------------
    contextual_warnings = []
    daily_limit_status = "normal"

    if allowance <= 0:
        daily_limit_status = "unconfigured"
    else:
        if daily_limit >= 300.0:
            daily_limit_status = "normal"
            # Rs. 300 or more: Normal behavior, no warning
        elif 200.0 <= daily_limit < 300.0:
            daily_limit_status = "low"
            contextual_warnings.append({
                "type": "warning",
                "title": "Low daily limit",
                "message": f"You have only {format_rupees(daily_limit)} available per day."
            })
        else: # daily_limit < 200.0 (including 0.0 or over budget)
            daily_limit_status = "critical"
            if is_over_budget:
                warning_msg = f"Very low daily limit — review your remaining budget. You have exceeded your allowance by {format_rupees(abs(remaining))}."
            else:
                warning_msg = f"Very low daily limit — review your remaining budget. Only {format_rupees(daily_limit)}/day available."

            contextual_warnings.append({
                "type": "danger",
                "title": "Very low daily limit",
                "message": warning_msg
            })

            # If critical balance AND friends owe money, show separate actionable reminder
            if others_owe_you > 0:
                debtors_map = {}
                for r in money_owed_records:
                    if r.get("type") == "they_owe_me" and r.get("status") == "Pending":
                        p = r.get("person", "").strip().title()
                        if p:
                            debtors_map[p] = debtors_map.get(p, 0.0) + float(r.get("amount", 0))

                top_debtors = sorted([{"name": k, "amount": v} for k, v in debtors_map.items()], key=lambda x: x["amount"], reverse=True)
                debtors_summary = ", ".join([f"{d['name']} ({format_rupees(d['amount'])})" for d in top_debtors[:3]])
                debtor_count = len(debtors_map)

                contextual_warnings.append({
                    "type": "info",
                    "title": "Outstanding Money to Collect",
                    "message": f"You have {format_rupees(others_owe_you)} outstanding from {debtor_count} {'person' if debtor_count == 1 else 'people'} ({debtors_summary}). Consider following up on these payments.",
                    "action_url": "/money-owed",
                    "action_text": "View Money Owed"
                })

    recent_cats = []
    for e in reversed(expenses):
        c = e.get("category", "").strip().capitalize()
        if c and c not in recent_cats:
            recent_cats.append(c)
    defaults = ["Mess/Food", "Chai/Snacks", "Transport", "Groceries", "Laundry"]
    quick_categories = (recent_cats + [d for d in defaults if d not in recent_cats])[:5]
    last_payment_method = expenses[-1].get("payment_method", "Cash") if expenses else "Cash"

    # Recent expenses (last 5, newest first)
    recent_expenses = list(reversed(expenses))[:5]

    # Pending debts snapshot for dashboard (newest first, up to 4)
    pending_debts = [r for r in reversed(money_owed_records) if r.get("status") == "Pending"][:4]

    return render_template(
        "index.html",
        expenses=expenses,
        recent_expenses=recent_expenses,
        pending_debts=pending_debts,
        total_expense=total_expense,
        all_time_total=all_time_total,
        allowance=allowance,
        received_date=received_date,
        cycle_start=cycle_start.strftime("%b %d, %Y"),
        cycle_end=cycle_end.strftime("%b %d, %Y"),
        remaining=remaining,
        available_cash=available_cash,
        percentage_spent=percentage_spent,
        days_remaining=days_remaining,
        daily_limit=daily_limit,
        daily_limit_status=daily_limit_status,
        is_over_budget=is_over_budget,
        contextual_warnings=contextual_warnings,
        others_owe_you=others_owe_you,
        you_owe_others=you_owe_others,
        pending_records_count=pending_records_count,
        quick_categories=quick_categories,
        last_payment_method=last_payment_method,
        today=date.today().isoformat(),
        active_page="dashboard"
    )


@app.route("/quick-add-expense", methods=["POST"])
@login_required
def quick_add_expense():
    """Rapid expense entry directly from Dashboard."""
    amount_raw = request.form.get("amount", "").strip()
    category = request.form.get("category", "").strip() or "General"
    name = request.form.get("name", "").strip() or category
    payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"
    date_val = request.form.get("date", "").strip() or date.today().isoformat()
    note = request.form.get("note", "").strip()

    try:
        amount = round(float(amount_raw), 2)
        if amount <= 0:
            raise ValueError
    except ValueError:
        flash("Please enter a valid positive expense amount.", "error")
        return redirect(url_for("home"))

    expenses = load_expenses()
    new_expense = {
        "id": int(time.time() * 1000),
        "name": name,
        "category": category,
        "amount": amount,
        "date": date_val,
        "note": note,
        "payment_method": payment_method
    }
    expenses.append(new_expense)
    save_expenses(expenses)
    flash(f"Expense '{name}' ({format_rupees(amount)}) recorded successfully.", "success")
    return redirect(url_for("home"))

@app.route("/expenses")
@login_required
def list_expenses():
    expenses = load_expenses()

    q = request.args.get("q", "").strip().lower()
    category_filter = request.args.get("category", "").strip().lower()
    payment_filter = request.args.get("payment_method", "").strip()
    date_filter = request.args.get("date", "").strip()

    filtered = []
    for exp in reversed(expenses):
        match = True

        if q:
            name_match = q in exp.get("name", "").lower()
            cat_match = q in exp.get("category", "").lower()
            note_match = q in exp.get("note", "").lower()
            if not (name_match or cat_match or note_match):
                match = False

        if match and category_filter:
            if category_filter not in exp.get("category", "").lower():
                match = False

        if match and payment_filter and payment_filter != "All":
            if exp.get("payment_method", "Cash") != payment_filter:
                match = False

        if match and date_filter:
            if exp.get("date", "") != date_filter:
                match = False

        if match:
            filtered.append(exp)

    filtered_total = sum(float(exp.get("amount", 0)) for exp in filtered)

    return render_template(
        "expenses.html",
        expenses=filtered,
        total_count=len(expenses),
        filtered_count=len(filtered),
        filtered_total=filtered_total,
        q=request.args.get("q", ""),
        category_filter=request.args.get("category", ""),
        payment_filter=payment_filter,
        date_filter=date_filter,
        active_page="expenses"
    )

@app.route("/add-expense", methods=["GET", "POST"])
@login_required
def add_expense():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip()
        note = request.form.get("note", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"

        try:
            amount = round(float(amount_raw), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "add_expense.html",
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="expenses"
            )

        if not name or not category or not date_val:
            return render_template(
                "add_expense.html",
                error="Please fill in all required fields (Name, Category, Amount, Date).",
                today=date.today().isoformat(),
                active_page="expenses"
            )

        expenses = load_expenses()
        new_expense = {
            "id": int(time.time() * 1000),
            "name": name,
            "category": category,
            "amount": amount,
            "date": date_val,
            "note": note,
            "payment_method": payment_method
        }

        expenses.append(new_expense)
        save_expenses(expenses)
        flash(f"Expense '{name}' ({format_rupees(amount)}) added successfully.", "success")
        return redirect(url_for("list_expenses"))

    return render_template(
        "add_expense.html",
        today=date.today().isoformat(),
        active_page="expenses"
    )

@app.route("/edit-expense/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_expense(item_id):
    expenses = load_expenses()
    target_idx = None
    for idx, exp in enumerate(expenses):
        if exp.get("id") == item_id:
            target_idx = idx
            break

    if target_idx is None:
        flash("Expense not found or already deleted.", "error")
        return redirect(url_for("list_expenses"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip()
        note = request.form.get("note", "").strip()
        payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"

        try:
            amount = round(float(amount_raw), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "edit_expense.html",
                expense=expenses[target_idx],
                item_id=item_id,
                error="Please enter a valid positive amount.",
                active_page="expenses"
            )

        if not name or not category or not date_val:
            return render_template(
                "edit_expense.html",
                expense=expenses[target_idx],
                item_id=item_id,
                error="Please fill in all required fields.",
                active_page="expenses"
            )

        # Preserve split_id if linked
        old_split_id = expenses[target_idx].get("split_id")
        updated_expense = {
            "id": item_id,
            "name": name,
            "category": category,
            "amount": amount,
            "date": date_val,
            "note": note,
            "payment_method": payment_method
        }
        if old_split_id:
            updated_expense["split_id"] = old_split_id

        expenses[target_idx] = updated_expense
        save_expenses(expenses)
        flash(f"Expense '{name}' updated successfully.", "success")
        return redirect(url_for("list_expenses"))

    return render_template(
        "edit_expense.html",
        expense=expenses[target_idx],
        item_id=item_id,
        active_page="expenses"
    )

@app.route("/delete-expense/<int:item_id>", methods=["GET", "POST"])
@login_required
def delete_expense(item_id):
    expenses = load_expenses()
    deleted_exp = None
    new_expenses = []

    for exp in expenses:
        if exp.get("id") == item_id:
            deleted_exp = exp
        else:
            new_expenses.append(exp)

    if deleted_exp:
        save_expenses(new_expenses)
        flash(f"Deleted expense '{deleted_exp.get('name')}' ({format_rupees(deleted_exp.get('amount', 0))}).", "success")
    else:
        flash("Could not delete expense: item not found.", "error")

    referrer = request.referrer
    if referrer and "/expenses" in referrer:
        return redirect(url_for("list_expenses"))
    return redirect(url_for("home"))

@app.route("/set-allowance", methods=["GET", "POST"])
@login_required
def set_allowance():
    if request.method == "POST":
        allowance_raw = request.form.get("allowance", "").strip()
        received_date = request.form.get("received_date", "").strip()

        try:
            amount = round(float(allowance_raw), 2)
            if amount < 0:
                raise ValueError
        except ValueError:
            current_info = load_allowance()
            return render_template(
                "set_allowance.html",
                allowance=current_info["amount"],
                received_date=current_info["received_date"],
                error="Please enter a valid allowance amount (0 or greater).",
                active_page="dashboard"
            )

        save_allowance(amount, received_date)
        flash("Monthly allowance updated successfully.", "success")
        return redirect(url_for("home"))

    current_info = load_allowance()
    return render_template(
        "set_allowance.html",
        allowance=current_info["amount"],
        received_date=current_info["received_date"],
        today=date.today().isoformat(),
        active_page="dashboard"
    )

def calculate_money_owed_stats(records):
    """Calculate key summary statistics and per-person balances for Money Owed."""
    others_owe_you = sum(float(r.get("amount", 0)) for r in records if r.get("type") == "they_owe_me" and r.get("status") == "Pending")
    you_owe_others = sum(float(r.get("amount", 0)) for r in records if r.get("type") == "i_owe_them" and r.get("status") == "Pending")

    people_who_owe_me = set(r.get("person", "").strip().lower() for r in records if r.get("type") == "they_owe_me" and r.get("status") == "Pending" and r.get("person", "").strip())
    people_who_owe_me_count = len(people_who_owe_me)

    people_you_owe = set(r.get("person", "").strip().lower() for r in records if r.get("type") == "i_owe_them" and r.get("status") == "Pending" and r.get("person", "").strip())
    people_you_owe_count = len(people_you_owe)

    pending_count = sum(1 for r in records if r.get("status") == "Pending")
    settled_count = sum(1 for r in records if r.get("status") == "Settled")
    settled_total = sum(float(r.get("amount", 0)) for r in records if r.get("status") == "Settled")

    person_groups = {}
    for r in records:
        raw_person = r.get("person", "").strip()
        if not raw_person:
            continue
        key = raw_person.lower()
        if key not in person_groups:
            person_groups[key] = {
                "display_name": raw_person.title(),
                "they_owe": 0.0,
                "you_owe": 0.0,
                "settled_total": 0.0,
                "records": []
            }
        person_groups[key]["records"].append(r)
        amt = float(r.get("amount", 0))
        if r.get("status") == "Pending":
            if r.get("type") == "they_owe_me":
                person_groups[key]["they_owe"] += amt
            else:
                person_groups[key]["you_owe"] += amt
        else:
            person_groups[key]["settled_total"] += amt

    grouped_list = []
    for key, group in person_groups.items():
        net = round(group["they_owe"] - group["you_owe"], 2)
        group["net"] = net
        grouped_list.append(group)

    # Sort grouped list: active debts first (highest owed to user at top)
    grouped_list.sort(key=lambda g: (g["they_owe"] > 0, g["net"], abs(g["net"])), reverse=True)

    return {
        "others_owe_you": others_owe_you,
        "you_owe_others": you_owe_others,
        "people_who_owe_me_count": people_who_owe_me_count,
        "people_you_owe_count": people_you_owe_count,
        "pending_count": pending_count,
        "settled_count": settled_count,
        "settled_total": settled_total,
        "grouped_by_person": grouped_list
    }


@app.route("/money-owed", methods=["GET", "POST"])
@login_required
def money_owed():
    records = load_money_owed()
    rooms = load_rooms()

    if request.method == "POST":
        raw_person = request.form.get("person", "").strip()
        item_type = request.form.get("type", "they_owe_me").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip() or date.today().isoformat()
        reason = request.form.get("reason", "").strip()
        note = request.form.get("note", "").strip()

        try:
            amount = round(float(amount_raw), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            stats = calculate_money_owed_stats(records)
            return render_template(
                "money_owed.html",
                records=list(reversed(records)),
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="money_owed",
                **stats
            )

        if not raw_person:
            stats = calculate_money_owed_stats(records)
            return render_template(
                "money_owed.html",
                records=list(reversed(records)),
                error="Please enter a person name.",
                today=date.today().isoformat(),
                active_page="money_owed",
                **stats
            )

        person = normalize_person_name(raw_person, records, rooms)

        # Check for immediate duplicate submissions
        now_ts = int(time.time() * 1000)
        is_duplicate = False
        for r in records[-5:]:
            if (r.get("person", "").lower() == person.lower() and
                r.get("type") == item_type and
                float(r.get("amount", 0)) == amount and
                r.get("date") == date_val and
                r.get("reason") == (reason or "General")):
                # Check if submitted within last 5 seconds
                if now_ts - r.get("id", 0) < 5000:
                    is_duplicate = True
                    break

        if is_duplicate:
            flash(f"Duplicate entry detected for {person} ({format_rupees(amount)}). Skipped.", "warning")
            return redirect(url_for("money_owed"))

        new_entry = {
            "id": now_ts,
            "person": person,
            "type": item_type if item_type in ["they_owe_me", "i_owe_them"] else "they_owe_me",
            "amount": amount,
            "date": date_val,
            "reason": reason or "General",
            "note": note,
            "status": "Pending"
        }

        records.append(new_entry)
        save_money_owed(records)
        flash(f"Transaction recorded: {person} ({format_rupees(amount)}).", "success")
        return redirect(url_for("money_owed"))

    stats = calculate_money_owed_stats(records)

    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "All").strip()
    person_filter = request.args.get("person", "").strip().lower()

    filtered_records = []
    for r in reversed(records):
        match = True
        if q:
            p_match = q in r.get("person", "").lower()
            r_match = q in r.get("reason", "").lower()
            n_match = q in r.get("note", "").lower()
            if not (p_match or r_match or n_match):
                match = False
        if match and status_filter and status_filter != "All":
            if r.get("status") != status_filter:
                match = False
        if match and person_filter:
            if r.get("person", "").strip().lower() != person_filter:
                match = False
        if match:
            filtered_records.append(r)

    return render_template(
        "money_owed.html",
        records=filtered_records,
        total_records_count=len(records),
        q=request.args.get("q", ""),
        status_filter=status_filter,
        person_filter=request.args.get("person", ""),
        today=date.today().isoformat(),
        active_page="money_owed",
        **stats
    )


@app.route("/money-owed/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_money_owed(item_id):
    records = load_money_owed()
    rooms = load_rooms()
    target_idx = None
    for idx, r in enumerate(records):
        if r.get("id") == item_id:
            target_idx = idx
            break

    if target_idx is None:
        flash("Record not found.", "error")
        return redirect(url_for("money_owed"))

    target_record = records[target_idx]

    if request.method == "POST":
        raw_person = request.form.get("person", "").strip()
        item_type = request.form.get("type", "they_owe_me").strip()
        amount_raw = request.form.get("amount", "").strip()
        date_val = request.form.get("date", "").strip() or date.today().isoformat()
        reason = request.form.get("reason", "").strip()
        note = request.form.get("note", "").strip()
        status = request.form.get("status", "Pending").strip()

        try:
            amount = round(float(amount_raw), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return render_template(
                "edit_money_owed.html",
                record=target_record,
                error="Please enter a valid positive amount.",
                today=date.today().isoformat(),
                active_page="money_owed"
            )

        if not raw_person:
            return render_template(
                "edit_money_owed.html",
                record=target_record,
                error="Person name is required.",
                today=date.today().isoformat(),
                active_page="money_owed"
            )

        person = normalize_person_name(raw_person, records, rooms)

        # Preserve split_id if linked
        old_split_id = target_record.get("split_id")
        updated_entry = {
            "id": item_id,
            "person": person,
            "type": item_type if item_type in ["they_owe_me", "i_owe_them"] else "they_owe_me",
            "amount": amount,
            "date": date_val,
            "reason": reason or "General",
            "note": note,
            "status": status if status in ["Pending", "Settled"] else "Pending"
        }
        if old_split_id:
            updated_entry["split_id"] = old_split_id

        records[target_idx] = updated_entry
        save_money_owed(records)
        flash(f"Updated record for {person}.", "success")
        return redirect(url_for("money_owed"))

    return render_template(
        "edit_money_owed.html",
        record=target_record,
        today=date.today().isoformat(),
        active_page="money_owed"
    )


@app.route("/money-owed/toggle/<int:item_id>")
@login_required
def toggle_money_owed(item_id):
    records = load_money_owed()
    new_status = "Pending"
    found = False
    for r in records:
        if r.get("id") == item_id:
            new_status = "Settled" if r.get("status") == "Pending" else "Pending"
            r["status"] = new_status
            found = True
            break
    if found:
        save_money_owed(records)
        flash(f"Marked record as {new_status}.", "success")
    else:
        flash("Record not found.", "error")
    return redirect(url_for("money_owed"))


@app.route("/money-owed/delete/<int:item_id>")
@login_required
def delete_money_owed(item_id):
    records = load_money_owed()
    new_records = [r for r in records if r.get("id") != item_id]
    if len(new_records) < len(records):
        save_money_owed(new_records)
        flash("Record deleted successfully.", "success")
    else:
        flash("Record not found or already deleted.", "error")
    return redirect(url_for("money_owed"))

@app.route("/rooms/add", methods=["POST"])
@login_required
def add_room():
    rooms = load_rooms()
    name = request.form.get("room_name", "").strip()
    members_raw = request.form.get("members", "").strip()

    if not name:
        flash("Room name is required.", "error")
        return redirect(url_for("split_expense"))

    # Parse and clean member names
    members = []
    for m in members_raw.split(","):
        clean_m = " ".join(m.strip().split()).title()
        if clean_m and clean_m.lower() not in [x.lower() for x in members]:
            members.append(clean_m)

    new_room = {
        "id": int(time.time() * 1000),
        "name": name,
        "members": members
    }
    rooms.append(new_room)
    save_rooms(rooms)
    flash(f"Room '{name}' created with {len(members)} roommate(s).", "success")
    return redirect(url_for("split_expense"))


@app.route("/rooms/edit/<int:room_id>", methods=["POST"])
@login_required
def edit_room(room_id):
    rooms = load_rooms()
    target = next((r for r in rooms if r.get("id") == room_id), None)
    if not target:
        flash("Room not found.", "error")
        return redirect(url_for("split_expense"))

    name = request.form.get("room_name", "").strip()
    members_raw = request.form.get("members", "").strip()

    if not name:
        flash("Room name cannot be empty.", "error")
        return redirect(url_for("split_expense"))

    members = []
    for m in members_raw.split(","):
        clean_m = " ".join(m.strip().split()).title()
        if clean_m and clean_m.lower() not in [x.lower() for x in members]:
            members.append(clean_m)

    target["name"] = name
    target["members"] = members
    save_rooms(rooms)
    flash(f"Room '{name}' updated successfully.", "success")
    return redirect(url_for("split_expense"))


@app.route("/rooms/delete/<int:room_id>", methods=["POST", "GET"])
@login_required
def delete_room(room_id):
    rooms = load_rooms()
    new_rooms = [r for r in rooms if r.get("id") != room_id]
    if len(new_rooms) < len(rooms):
        save_rooms(new_rooms)
        flash("Room deleted successfully.", "success")
    return redirect(url_for("split_expense"))

@app.route("/split-expense", methods=["GET", "POST"])
@login_required
def split_expense():
    splits_history = load_splits()
    rooms = load_rooms()
    owed_records = load_money_owed()

    # Collect known people across rooms and money owed
    known_people_set = set()
    for rm in rooms:
        for m in rm.get("members", []):
            if m.strip():
                known_people_set.add(m.strip())
    for r in owed_records:
        p = r.get("person", "").strip()
        if p and p.lower() not in ["you", "me"]:
            known_people_set.add(p)
    known_people = sorted(list(known_people_set))

    calculation_result = None
    error = None

    if request.method == "POST":
        title = request.form.get("title", "").strip() or "Group Expense"
        total_raw = request.form.get("total_amount", "").strip()
        payer = request.form.get("payer", "You").strip() or "You"
        split_mode = request.form.get("split_mode", "room").strip()  # "room" or "people"
        split_type = request.form.get("split_type", "equal").strip()  # "equal" or "custom"
        date_val = request.form.get("date", "").strip() or date.today().isoformat()
        selected_room_id = request.form.get("room_id", "").strip()

        sync_money_owed = request.form.get("sync_money_owed") == "yes"
        sync_expense = request.form.get("sync_expense") == "yes"
        expense_category = request.form.get("expense_category", "").strip() or "Food / Mess"
        expense_payment_method = request.form.get("payment_method", "Cash").strip() or "Cash"
        save_to_history = request.form.get("save_history") == "yes"

        # Prepare form data snapshot to preserve inputs on any validation failure
        form_data = {
            "title": title,
            "total_amount": total_raw,
            "payer": payer,
            "split_mode": split_mode,
            "split_type": split_type,
            "date": date_val,
            "room_id": selected_room_id,
            "room_participants": request.form.getlist("room_participants"),
            "custom_people": request.form.get("custom_people", "").strip(),
            "shares": {},
            "sync_money_owed": sync_money_owed,
            "sync_expense": sync_expense,
            "expense_category": expense_category,
            "payment_method": expense_payment_method,
            "save_history": save_to_history
        }

        try:
            total_amount = round(float(total_raw), 2)
            if total_amount <= 0:
                raise ValueError
        except ValueError:
            error = "Please enter a valid positive total bill amount."
            return render_template(
                "split_expense.html",
                splits_history=list(reversed(splits_history)),
                rooms=rooms,
                known_people=known_people,
                error=error,
                form_data=form_data,
                today=date.today().isoformat(),
                active_page="split"
            )

        # Collect participants based on mode
        participants = []
        if split_mode == "room":
            selected_participants = request.form.getlist("room_participants")
            for p in selected_participants:
                p_clean = normalize_person_name(p, owed_records, rooms)
                if p_clean and p_clean.lower() not in [x.lower() for x in participants]:
                    participants.append(p_clean)
        else:
            people_input = request.form.get("custom_people", "").strip()
            raw_list = [p.strip() for p in people_input.split(",") if p.strip()]
            for p in raw_list:
                p_clean = "You" if p.lower() in ["you", "me", "myself"] else normalize_person_name(p, owed_records, rooms)
                if p_clean and p_clean.lower() not in [x.lower() for x in participants]:
                    participants.append(p_clean)

        # Record shares in form_data for preservation
        for p in participants:
            form_data["shares"][p] = request.form.get(f"share_{p}", "")

        if not participants:
            error = "Please select or enter at least one participant sharing this bill."
            return render_template(
                "split_expense.html",
                splits_history=list(reversed(splits_history)),
                rooms=rooms,
                known_people=known_people,
                error=error,
                form_data=form_data,
                today=date.today().isoformat(),
                active_page="split"
            )

        payer_clean = "You" if payer.lower() in ["you", "me", "myself"] else normalize_person_name(payer, owed_records, rooms)

        breakdown = []
        who_owes_whom = []

        if split_type == "equal":
            count = len(participants)
            per_person = round(total_amount / count, 2)
            for p_name in participants:
                breakdown.append({
                    "name": p_name,
                    "share": per_person,
                    "is_payer": (p_name.lower() == payer_clean.lower())
                })
                if p_name.lower() != payer_clean.lower():
                    who_owes_whom.append(f"{p_name} owes {payer_clean} {format_rupees(per_person)}")
        else:
            # Custom shares: read form fields share_<person>
            custom_total = 0.0
            for p_name in participants:
                field_key = f"share_{p_name}"
                val_raw = request.form.get(field_key, "0").strip()
                try:
                    val = round(float(val_raw), 2)
                except ValueError:
                    val = 0.0
                custom_total += val
                breakdown.append({
                    "name": p_name,
                    "share": val,
                    "is_payer": (p_name.lower() == payer_clean.lower())
                })
                if p_name.lower() != payer_clean.lower() and val > 0:
                    who_owes_whom.append(f"{p_name} owes {payer_clean} {format_rupees(val)}")

            # Precise difference calculation
            diff = round(total_amount - custom_total, 2)
            if abs(diff) > 0.01:
                if diff > 0:
                    diff_msg = f"Rs. {diff:,.2f} remains unallocated"
                else:
                    diff_msg = f"shares exceed total bill by Rs. {abs(diff):,.2f}"
                error = f"Sum of individual shares ({format_rupees(custom_total)}) does not equal total bill ({format_rupees(total_amount)}) — {diff_msg}."
                return render_template(
                    "split_expense.html",
                    splits_history=list(reversed(splits_history)),
                    rooms=rooms,
                    known_people=known_people,
                    error=error,
                    form_data=form_data,
                    today=date.today().isoformat(),
                    active_page="split"
                )

        split_id = int(time.time() * 1000)
        sync_notices = []
        is_user_payer = (payer_clean.lower() in ["you", "me"])

        # 1. Sync Debts to Money Owed with split_id linking
        if sync_money_owed:
            owed_list = load_money_owed()
            debts_added = 0

            if is_user_payer:
                # "You" paid for everyone. Anyone other than "You" owes "You"
                for item in breakdown:
                    if item["name"].lower() not in ["you", "me"] and item["share"] > 0:
                        owed_list.append({
                            "id": split_id + debts_added + 1,
                            "split_id": split_id,
                            "person": item["name"],
                            "type": "they_owe_me",
                            "amount": item["share"],
                            "date": date_val,
                            "reason": f"Split: {title}",
                            "note": f"Share of {format_rupees(total_amount)} bill paid by You",
                            "status": "Pending"
                        })
                        debts_added += 1
                if debts_added > 0:
                    sync_notices.append(f"Recorded {debts_added} roommate debt(s) in Money Owed")
            else:
                # Someone else paid (e.g. Ali).
                # If "You" was among participants, "You" owes Ali
                user_item = next((item for item in breakdown if item["name"].lower() in ["you", "me"]), None)
                if user_item and user_item["share"] > 0:
                    owed_list.append({
                        "id": split_id + 1,
                        "split_id": split_id,
                        "person": payer_clean,
                        "type": "i_owe_them",
                        "amount": user_item["share"],
                        "date": date_val,
                        "reason": f"Split: {title}",
                        "note": f"Your share of {format_rupees(total_amount)} bill paid by {payer_clean}",
                        "status": "Pending"
                    })
                    sync_notices.append(f"Recorded debt of {format_rupees(user_item['share'])} to {payer_clean} in Money Owed")

            if debts_added > 0 or any("Recorded debt" in n for n in sync_notices):
                save_money_owed(owed_list)

        # 2. Sync Personal Share to Expenses (Crucial: No double-counting!)
        # Only record if "You" was a participant!
        user_in_participants = any(item["name"].lower() in ["you", "me"] for item in breakdown)
        if sync_expense and user_in_participants:
            user_item = next((item for item in breakdown if item["name"].lower() in ["you", "me"]), None)
            user_share = user_item["share"] if user_item else 0.0
            if user_share > 0:
                exp_list = load_expenses()
                exp_list.append({
                    "id": split_id + 99,
                    "split_id": split_id,
                    "name": f"{title} (My share)",
                    "category": expense_category,
                    "amount": user_share,
                    "date": date_val,
                    "note": f"Personal share of {format_rupees(total_amount)} {title} split",
                    "payment_method": expense_payment_method
                })
                save_expenses(exp_list)
                sync_notices.append(f"Recorded personal share of {format_rupees(user_share)} in Expenses")

        # Save to splits history
        if save_to_history:
            new_split = {
                "id": split_id,
                "title": title,
                "total_amount": total_amount,
                "payer": payer_clean,
                "date": date_val,
                "split_mode": split_mode,
                "split_type": split_type,
                "participants": participants,
                "breakdown": breakdown,
                "who_owes_whom": who_owes_whom
            }
            splits_history.append(new_split)
            save_splits(splits_history)

        calculation_result = {
            "id": split_id,
            "title": title,
            "total_amount": total_amount,
            "payer": payer_clean,
            "date": date_val,
            "participants": participants,
            "breakdown": breakdown,
            "who_owes_whom": who_owes_whom
        }

        sync_message = " & ".join(sync_notices) if sync_notices else None
        if sync_message:
            flash(sync_message, "success")

    return render_template(
        "split_expense.html",
        splits_history=list(reversed(splits_history)),
        rooms=rooms,
        known_people=known_people,
        result=calculation_result,
        today=date.today().isoformat(),
        active_page="split"
    )


@app.route("/split-expense/delete/<int:item_id>")
@login_required
def delete_split(item_id):
    """
    Delete a split bill record and cascade removal ONLY to debts/expenses generated by this split.
    Manual debts and manual expenses remain completely untouched!
    """
    splits = load_splits()
    splits = [s for s in splits if s.get("id") != item_id]
    save_splits(splits)

    # Clean only debts linked with this split_id
    money_owed_list = load_money_owed()
    cleaned_owed = [r for r in money_owed_list if r.get("split_id") != item_id]
    if len(cleaned_owed) < len(money_owed_list):
        save_money_owed(cleaned_owed)

    # Clean only expenses linked with this split_id
    expenses_list = load_expenses()
    cleaned_expenses = [e for e in expenses_list if e.get("split_id") != item_id]
    if len(cleaned_expenses) < len(expenses_list):
        save_expenses(cleaned_expenses)

    flash("Split bill and associated debts/expenses removed successfully.", "success")
    return redirect(url_for("split_expense"))


@app.route("/split-expense/pdf/<int:item_id>")
@login_required
def split_expense_pdf(item_id):
    splits = load_splits()
    split_record = next((s for s in splits if s.get("id") == item_id), None)
    if not split_record:
        flash("Split bill record not found.", "error")
        return redirect(url_for("split_expense"))

    pdf_bytes = generate_split_bill_pdf(split_record)
    safe_title = "".join(c for c in split_record.get("title", "split") if c.isalnum() or c in (' ', '_', '-')).strip().replace(" ", "_")
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Hostelite_Split_{safe_title}_{item_id}.pdf"}
    )


@app.route("/split-expense/download-pdf/<int:item_id>")
@login_required
def download_split_expense_pdf(item_id):
    splits = load_splits()
    split_record = next((s for s in splits if s.get("id") == item_id), None)
    if not split_record:
        flash("Split bill record not found.", "error")
        return redirect(url_for("split_expense"))

    pdf_bytes = generate_split_bill_pdf(split_record)
    safe_title = "".join(c for c in split_record.get("title", "split") if c.isalnum() or c in (' ', '_', '-')).strip().replace(" ", "_")
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Hostelite_Split_{safe_title}_{item_id}.pdf"}
    )


@app.route("/expense/pdf/<int:item_id>")
@login_required
def expense_pdf(item_id):
    expenses = load_expenses()
    exp = next((e for e in expenses if e.get("id") == item_id), None)
    if not exp:
        flash("Expense record not found.", "error")
        return redirect(url_for("list_expenses"))

    allowance_info = load_allowance()
    total_expense = sum(float(e.get("amount", 0)) for e in expenses)
    allowance_info["remaining"] = allowance_info["amount"] - total_expense

    pdf_bytes = generate_expense_pdf(exp, allowance_info)
    safe_name = "".join(c for c in exp.get("name", "expense") if c.isalnum() or c in (' ', '_', '-')).strip().replace(" ", "_")
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Hostelite_Expense_{safe_name}_{item_id}.pdf"}
    )


@app.route("/expense/download-pdf/<int:item_id>")
@login_required
def download_expense_pdf(item_id):
    expenses = load_expenses()
    exp = next((e for e in expenses if e.get("id") == item_id), None)
    if not exp:
        flash("Expense record not found.", "error")
        return redirect(url_for("list_expenses"))

    allowance_info = load_allowance()
    total_expense = sum(float(e.get("amount", 0)) for e in expenses)
    allowance_info["remaining"] = allowance_info["amount"] - total_expense

    pdf_bytes = generate_expense_pdf(exp, allowance_info)
    safe_name = "".join(c for c in exp.get("name", "expense") if c.isalnum() or c in (' ', '_', '-')).strip().replace(" ", "_")
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Hostelite_Expense_{safe_name}_{item_id}.pdf"}
    )
def get_monthly_report_data(selected_month, user_id=None):
    """Compile comprehensive report data for a specific YYYY-MM month."""
    expenses = load_expenses(user_id=user_id)
    allowance_info = load_allowance(user_id=user_id)
    allowance = allowance_info["amount"]
    month_expenses = [e for e in expenses if e.get("date", "").startswith(selected_month)]
    total_spent = sum(float(e.get("amount", 0)) for e in month_expenses)
    try:
        yr, mo = map(int, selected_month.split("-"))
        _, days_in_month = calendar.monthrange(yr, mo)
        month_dt = date(yr, mo, 1)
        month_label = month_dt.strftime("%B %Y")
    except (ValueError, TypeError):
        month_label = selected_month
        days_in_month = 30

    daily_average = round(total_spent / days_in_month, 2) if days_in_month > 0 else 0.0
    cat_map = {}
    for e in month_expenses:
        cat = e.get("category", "General").strip().capitalize() or "General"
        cat_map[cat] = cat_map.get(cat, 0.0) + float(e.get("amount", 0))

    category_stats = []
    category_labels = []
    category_data = []
    for cat, amt in sorted(cat_map.items(), key=lambda x: x[1], reverse=True):
        pct = round((amt / total_spent) * 100, 1) if total_spent > 0 else 0.0
        category_stats.append({"category": cat, "amount": amt, "percentage": pct})
        category_labels.append(cat)
        category_data.append(round(amt, 2))

    pm_map = {}
    for e in month_expenses:
        pm = e.get("payment_method", "Cash").strip() or "Cash"
        pm_map[pm] = pm_map.get(pm, 0.0) + float(e.get("amount", 0))

    payment_stats = []
    payment_labels = []
    payment_data = []
    for pm, amt in sorted(pm_map.items(), key=lambda x: x[1], reverse=True):
        pct = round((amt / total_spent) * 100, 1) if total_spent > 0 else 0.0
        payment_stats.append({"method": pm, "amount": amt, "percentage": pct})
        payment_labels.append(pm)
        payment_data.append(round(amt, 2))

    money_owed_records = load_money_owed(user_id=user_id)
    others_owe_you = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "they_owe_me" and r.get("status") == "Pending")
    you_owe_others = sum(float(r.get("amount", 0)) for r in money_owed_records if r.get("type") == "i_owe_them" and r.get("status") == "Pending")

    remaining = round(allowance - total_spent, 2) if allowance > 0 else 0.0

    return {
        "selected_month": selected_month,
        "month_label": month_label,
        "total_spent": total_spent,
        "expense_count": len(month_expenses),
        "daily_average": daily_average,
        "allowance": allowance,
        "remaining": remaining,
        "category_stats": category_stats,
        "category_labels": category_labels,
        "category_data": category_data,
        "payment_stats": payment_stats,
        "payment_labels": payment_labels,
        "payment_data": payment_data,
        "others_owe_you": others_owe_you,
        "you_owe_others": you_owe_others
    }


@app.route("/reports")
@login_required
def reports():
    expenses = load_expenses()
    today = date.today()
    current_month_str = today.strftime("%Y-%m")

    available_months_set = {current_month_str}
    for e in expenses:
        d = e.get("date", "")
        if len(d) >= 7:
            available_months_set.add(d[:7])
    available_months = sorted(list(available_months_set), reverse=True)

    selected_month = request.args.get("month", current_month_str).strip()
    if selected_month not in available_months:
        selected_month = current_month_str

    report_data = get_monthly_report_data(selected_month)

    top_cats_text = ""
    for c in report_data["category_stats"][:3]:
        top_cats_text += f"- {c['category']}: {format_rupees(c['amount'])} ({c['percentage']}%)\n"
    if not top_cats_text:
        top_cats_text = "- None recorded\n"

    whatsapp_summary = (
        f"*Hostelite Monthly Spending Report — {report_data['month_label']}*\n\n"
        f"Total Spent: {format_rupees(report_data['total_spent'])}\n"
        f"Daily Average: {format_rupees(report_data['daily_average'])}/day\n\n"
        f"*Top Spending Categories:*\n"
        f"{top_cats_text}\n"
        f"*Budget Status:*\n"
        f"- Allowance: {format_rupees(report_data['allowance'])}\n"
        f"- Balance: {format_rupees(report_data['remaining'])}\n\n"
        f"*Outstanding Money Owed:*\n"
        f"- Others owe you: {format_rupees(report_data['others_owe_you'])}\n"
        f"- You owe others: {format_rupees(report_data['you_owe_others'])}\n\n"
        f"Generated by Hostelite Finance System"
    )

    all_time_spent = sum(float(e.get("amount", 0)) for e in expenses)
    seven_days_ago = today - timedelta(days=6)
    last_7_days_labels = []
    last_7_days_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        d_str = d.isoformat()
        last_7_days_labels.append(d.strftime("%b %d"))
        day_total = sum(float(e.get("amount", 0)) for e in expenses if e.get("date") == d_str)
        last_7_days_data.append(round(day_total, 2))

    return render_template(
        "reports.html",
        available_months=available_months,
        selected_month=selected_month,
        report_data=report_data,
        whatsapp_summary=whatsapp_summary,
        all_time_spent=all_time_spent,
        last_7_days_labels_json=json.dumps(last_7_days_labels),
        last_7_days_data_json=json.dumps(last_7_days_data),
        category_labels_json=json.dumps(report_data["category_labels"]),
        category_data_json=json.dumps(report_data["category_data"]),
        payment_labels_json=json.dumps(report_data["payment_labels"]),
        payment_data_json=json.dumps(report_data["payment_data"]),
        active_page="reports"
    )


@app.route("/reports/download-pdf")
@login_required
def download_pdf_report():
    month = request.args.get("month", date.today().strftime("%Y-%m")).strip()
    report_data = get_monthly_report_data(month)
    pdf_bytes = generate_monthly_report_pdf(report_data)

    safe_filename = f"Hostelite_Report_{month}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={safe_filename}"}
    )


if __name__ == "__main__":
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    extra = [env_path] if os.path.exists(env_path) else None
    port = int(os.environ.get("PORT", 5000))
    debug_mode = not is_production()
    app.run(host="0.0.0.0", port=port, debug=debug_mode, extra_files=extra if debug_mode else None)