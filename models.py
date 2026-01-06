from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# ----------------------------
# USERS
# ----------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    # tenant / landlord / admin
    role = db.Column(db.String(20), nullable=False, default="tenant")

    theme = db.Column(db.String(10), default="light")
    name = db.Column(db.String(120))

    terms_accepted = db.Column(db.Boolean, default=False, nullable=False)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)

# ----------------------------
# LISTINGS
# ----------------------------
class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    title = db.Column(db.String(150), nullable=False)
    city = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Embedding for AI search
    embedding = db.Column(db.PickleType)

    user = db.relationship("User", backref="listings")
    images = db.relationship(
        "ListingImage",
        backref="listing",
        cascade="all, delete-orphan",
        order_by="ListingImage.sort_order"
    )
    reviews = db.relationship("ReviewListing", backref="listing", cascade="all, delete-orphan")

    @property
    def avg_rating(self):
        """Средний рейтинг объявления."""
        if not self.reviews:
            return None
        return sum(r.rating for r in self.reviews) / len(self.reviews)


class ListingImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)

    sort_order = db.Column(db.Integer, nullable=False, default=0)

# ----------------------------
# REVIEWS
# ----------------------------
class ReviewListing(db.Model):
    __tablename__ = "review_listing"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="listing_reviews")


# ----------------------------
# CHAT
# ----------------------------
class MessageThread(db.Model):
    __tablename__ = "message_thread"

    id = db.Column(db.Integer, primary_key=True)

    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    landlord_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)

    listing = db.relationship("Listing")
    landlord = db.relationship("User", foreign_keys=[landlord_id])
    tenant = db.relationship("User", foreign_keys=[tenant_id])


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("message_thread.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship("User")


# ----------------------------
# DEALS (Relok flow)
# ----------------------------
class Deal(db.Model):
    """Сделка между арендатором и владельцем по конкретному объявлению.

    Важно: у Relok это НЕ мгновенная бронь как Booking/Airbnb.
    Это управляемая админом сделка: резерв -> документы -> подпись -> оплата.
    """
    __tablename__ = "deal"

    id = db.Column(db.Integer, primary_key=True)

    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    landlord_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # created_by is tenant (reserve) usually
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # lifecycle status (string keeps it flexible)
    status = db.Column(db.String(40), nullable=False, default="reserved")

    # rental period (selected by tenant, confirmed by admin)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    dates_confirmed = db.Column(db.Boolean, default=False)

    # optional admin assigned to deal
    admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # free-form notes
    tenant_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    listing = db.relationship("Listing")
    tenant = db.relationship("User", foreign_keys=[tenant_id])
    landlord = db.relationship("User", foreign_keys=[landlord_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    admin = db.relationship("User", foreign_keys=[admin_id])

    documents = db.relationship("DealDocument", backref="deal", cascade="all, delete-orphan")
    audit = db.relationship("DealAudit", backref="deal", cascade="all, delete-orphan")

    def touch(self):
        self.updated_at = datetime.utcnow()


class DealDocument(db.Model):
    __tablename__ = "deal_document"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False)

    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # tenant / landlord
    party = db.Column(db.String(20), nullable=False)

    # e.g. passport, visa, income_proof, ownership_proof, extra
    doc_type = db.Column(db.String(40), nullable=False)

    filename = db.Column(db.String(255), nullable=False)

    # pending / approved / rejected
    status = db.Column(db.String(20), nullable=False, default="pending")

    # admin comment on reject / request
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    uploader = db.relationship("User", foreign_keys=[uploader_id])
    reviewed_by_admin = db.relationship("User", foreign_keys=[reviewed_by_admin_id])


class DealAudit(db.Model):
    __tablename__ = "deal_audit"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False)

    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(60), nullable=False)  # status_change / doc_upload / doc_review / cancel / etc.
    meta = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    actor = db.relationship("User", foreign_keys=[actor_id])

# ----------------------------
# CONTRACTS (Deal PDF + manual signature)
# ----------------------------
class DealContract(db.Model):
    __tablename__ = "deal_contract"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False, unique=True)

    # Generated PDF (unsigned)
    unsigned_filename = db.Column(db.String(255), nullable=False)
    unsigned_sha256 = db.Column(db.String(64), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    deal = db.relationship("Deal", backref=db.backref("contract", uselist=False))
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    signed_files = db.relationship(
        "DealContractSigned",
        backref="contract",
        cascade="all, delete-orphan"
    )


class DealContractSigned(db.Model):
    __tablename__ = "deal_contract_signed"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("deal_contract.id"), nullable=False)

    # tenant / landlord
    party = db.Column(db.String(20), nullable=False)

    filename = db.Column(db.String(255), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)

    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    uploader = db.relationship("User", foreign_keys=[uploader_id])

    __table_args__ = (
        db.UniqueConstraint("contract_id", "party", name="uq_contract_party"),
    )
