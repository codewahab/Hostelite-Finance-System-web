from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timezone

db = SQLAlchemy()

def utcnow():
    return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.BigInteger, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    auth_provider = db.Column(db.String(30), default="local", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    allowance = db.relationship("Allowance", backref="user", uselist=False, cascade="all, delete-orphan")
    expenses = db.relationship("Expense", backref="user", lazy=True, cascade="all, delete-orphan")
    debts = db.relationship("MoneyOwed", backref="user", lazy=True, cascade="all, delete-orphan")
    rooms = db.relationship("Room", backref="user", lazy=True, cascade="all, delete-orphan")
    splits = db.relationship("Split", backref="user", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "email_verified": self.email_verified,
            "google_id": self.google_id,
            "auth_provider": self.auth_provider,
            "password_hash": self.password_hash,
            "created_at": self.created_at.strftime("%Y-%m-%d") if self.created_at else date.today().isoformat()
        }


class Allowance(db.Model):
    __tablename__ = "allowances"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), default=0.0, nullable=False)
    received_date = db.Column(db.Date, nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def to_dict(self):
        amt = float(self.amount) if self.amount is not None else 0.0
        rec = self.received_date.strftime("%Y-%m-%d") if self.received_date else ""
        return {
            "amount": amt,
            "allowance": amt,
            "received_date": rec
        }


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    members = db.Column(db.JSON, default=list, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "members": self.members if isinstance(self.members, list) else []
        }


class Split(db.Model):
    __tablename__ = "splits"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    payer = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, nullable=False)
    split_mode = db.Column(db.String(50), default="room", nullable=False)
    split_type = db.Column(db.String(50), default="equal", nullable=False)
    participants = db.Column(db.JSON, default=list, nullable=False)
    breakdown = db.Column(db.JSON, default=list, nullable=False)
    who_owes_whom = db.Column(db.JSON, default=list, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    expenses = db.relationship("Expense", backref="split", lazy=True, cascade="all, delete-orphan")
    debts = db.relationship("MoneyOwed", backref="split", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "total_amount": float(self.total_amount) if self.total_amount is not None else 0.0,
            "payer": self.payer,
            "date": self.date.strftime("%Y-%m-%d") if self.date else "",
            "split_mode": self.split_mode,
            "split_type": self.split_type,
            "participants": self.participants if isinstance(self.participants, list) else [],
            "breakdown": self.breakdown if isinstance(self.breakdown, list) else [],
            "who_owes_whom": self.who_owes_whom if isinstance(self.who_owes_whom, list) else []
        }


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    note = db.Column(db.Text, default="", nullable=False)
    payment_method = db.Column(db.String(50), default="Cash", nullable=False)
    split_id = db.Column(db.BigInteger, db.ForeignKey("splits.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    def to_dict(self):
        d = {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "amount": float(self.amount) if self.amount is not None else 0.0,
            "date": self.date.strftime("%Y-%m-%d") if self.date else "",
            "note": self.note or "",
            "payment_method": self.payment_method or "Cash"
        }
        if self.split_id is not None:
            d["split_id"] = self.split_id
        return d


class MoneyOwed(db.Model):
    __tablename__ = "money_owed"

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    person = db.Column(db.String(100), nullable=False, index=True)
    type = db.Column(db.String(20), nullable=False)  # 'they_owe_me' or 'i_owe_them'
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(255), default="", nullable=False)
    note = db.Column(db.Text, default="", nullable=False)
    status = db.Column(db.String(20), default="Pending", nullable=False, index=True)  # 'Pending' or 'Settled'
    split_id = db.Column(db.BigInteger, db.ForeignKey("splits.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    def to_dict(self):
        d = {
            "id": self.id,
            "person": self.person,
            "type": self.type,
            "amount": float(self.amount) if self.amount is not None else 0.0,
            "date": self.date.strftime("%Y-%m-%d") if self.date else "",
            "reason": self.reason or "",
            "note": self.note or "",
            "status": self.status or "Pending"
        }
        if self.split_id is not None:
            d["split_id"] = self.split_id
        return d
