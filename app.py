from flask import Flask, render_template, request, redirect,url_for, session, jsonify, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from datetime import timedelta
from datetime import datetime
from sqlalchemy import func
import re
import os
import shutil
import hashlib

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

from dotenv import load_dotenv
load_dotenv()

# ----------------------------
# Flask-Babel
# ----------------------------
from flask_babel import Babel, _, get_locale

from models import (
    db, User, Listing, ListingImage, ReviewListing,
    MessageThread, Message,
    Deal, DealDocument, DealAudit,
    DealContract, DealContractSigned
)
from ai_utils import get_embedding, cosine_sim

app = Flask(__name__)

# ----------------------------
# CONFIG
# ----------------------------
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SECRET_KEY"] = "R333!///ok."
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["UPLOAD_FOLDER"] = "static/uploads"

# Babel / i18n
app.config["BABEL_DEFAULT_LOCALE"] = "de"
app.config["BABEL_SUPPORTED_LOCALES"] = ["de", "en"]
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"


def select_locale():
    if "lang" in session:
        return session["lang"]
    best = request.accept_languages.best_match(["de", "en"])
    return best or "de"


babel = Babel(app, locale_selector=select_locale)

db.init_app(app)

with app.app_context():
    db.create_all()


# Make get_locale available in templates (so <html lang="{{ get_locale() }}"> works)
@app.context_processor
def inject_globals():
    return {"get_locale": get_locale}


@app.route("/set-lang/<lang>")
def set_language(lang):
    if lang in ['en', 'de']:
        session["lang"] = lang
    return redirect(request.referrer or "/")


# -----------------------------------------
# Make User and session available in Jinja2
# -----------------------------------------
@app.context_processor
def inject_user():
    current_user = None
    if "user_id" in session:
        current_user = User.query.get(session["user_id"])
    return dict(user=current_user, session=session)


# ----------------------------
# ROUTES
# ----------------------------

@app.route("/")
def index():
    if "user_id" in session:
        return redirect("/index_logged")

    listings = Listing.query.limit(4).all()
    for item in listings:
        image = ListingImage.query.filter_by(listing_id=item.id) \
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc()) \
            .first()
        item.image_filenames = [image.filename] if image else []

    return render_template("index.html", listings=listings)


@app.route("/index_logged")
def index_logged():
    if "user_id" not in session:
        return redirect("/login")

    user = User.query.get(session["user_id"])
    listings = Listing.query.limit(4).all()

    for item in listings:
        image = ListingImage.query.filter_by(listing_id=item.id) \
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc()) \
            .first()
        item.image_filenames = [image.filename] if image else []

    return render_template("index_logged.html", user=user, listings=listings)


# ----------------------------
# AUTH
# ----------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower()
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["role"] = user.role
            return redirect("/dashboard")

        return render_template("login.html", error="Неверный логин или пароль")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email", "").lower()
        password = request.form.get("password")
        role = request.form.get("role")  # tenant / landlord
        terms = request.form.get("terms")  # <-- важно

        if not terms:
            return render_template(
                "register.html",
                error="Вы должны принять пользовательское соглашение"
            )

        if User.query.filter_by(email=email).first():
            return render_template(
                "register.html",
                error="Email уже используется"
            )

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role=role,
            terms_accepted=True,
            terms_accepted_at=datetime.utcnow()
        )

        db.session.add(user)
        db.session.commit()

        session["user_id"] = user.id
        session["role"] = role

        return redirect("/dashboard")

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ----------------------------
# LISTINGS
# ----------------------------

@app.route("/listings")
def listings():
    city = request.args.get("city", "")
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")
    type_ = request.args.get("type", "")

    query = Listing.query

    if city:
        query = query.filter(Listing.city.ilike(f"%{city}%"))
    if min_price:
        query = query.filter(Listing.price >= int(min_price))
    if max_price:
        query = query.filter(Listing.price <= int(max_price))
    if type_:
        query = query.filter(Listing.type == type_)

    results = query.all()

    for item in results:
        image = (
            ListingImage.query
            .filter_by(listing_id=item.id)
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc())
            .first()
        )
        item.image_filenames = [image.filename] if image else []

    return render_template("listings.html", listings=results)


@app.route("/listing/<int:id>")
def listing_detail(id):
    listing = Listing.query.get_or_404(id)

    reviews = ReviewListing.query.filter_by(
        listing_id=id
    ).order_by(ReviewListing.created_at.desc()).all()

    images = ListingImage.query.filter_by(listing_id=id).order_by(ListingImage.sort_order.asc(),
                                                                  ListingImage.id.asc()).all()

    return render_template(
        "listing_detail.html",
        listing=listing,
        images=images,
        reviews=reviews
    )

@app.route("/listing/<int:id>/update-description", methods=["POST"])
def update_listing_description(id):
    if "user_id" not in session:
        return redirect("/login")

    listing = Listing.query.get_or_404(id)

    # только владелец объявления (landlord) может менять описание
    if listing.user_id != session["user_id"]:
        return "Нет доступа", 403

    new_desc = (request.form.get("description") or "").strip()
    listing.description = new_desc
    db.session.commit()

    return redirect(f"/listing/{id}")

@app.route("/listing/<int:id>/photos")
def listing_photos(id):
    listing = Listing.query.get_or_404(id)
    images = listing.images
    return render_template(
        "listing_photos.html",
        listing=listing,
        images=images
    )

@app.route("/listing/<int:listing_id>/images/reorder", methods=["POST"])
def reorder_listing_images(listing_id):
    if "user_id" not in session:
        return jsonify({"error": "auth"}), 401

    listing = Listing.query.get_or_404(listing_id)

    if listing.user_id != session["user_id"]:
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    ordered_ids = data.get("ordered_ids", [])

    images = ListingImage.query.filter_by(listing_id=listing_id).all()
    images_by_id = {img.id: img for img in images}

    order = 1
    for img_id in ordered_ids:
        if img_id in images_by_id:
            images_by_id[img_id].sort_order = order
            order += 1

    db.session.commit()
    return jsonify({"ok": True})


# ----------------------------
# AI SEARCH
# ----------------------------

@app.route("/ai-search", methods=["POST"])
def ai_search():
    data = request.get_json(silent=True) or {}
    query_text = (data.get("query") or "").strip()

    if not query_text:
        return jsonify([])

    query_emb = get_embedding(query_text)

    listings_ = Listing.query.filter(Listing.embedding.isnot(None)).all()
    if not listings_:
        return jsonify([])

    scored = []
    for item in listings_:
        if not item.embedding:
            continue
        sim = cosine_sim(query_emb, item.embedding)
        scored.append((sim, item))

    if not scored:
        return jsonify([])

    scored.sort(key=lambda x: x[0], reverse=True)
    top_items = [item for sim, item in scored[:10]]

    results = []
    for item in top_items:
        image = (
            ListingImage.query
            .filter_by(listing_id=item.id)
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc())
            .first()
        )
        results.append({
            "id": item.id,
            "title": item.title,
            "city": item.city,
            "price": item.price,
            "image": image.filename if image else None
        })

    return jsonify(results)


# ----------------------------
# CREATE LISTING (Landlord)
# ----------------------------

@app.route("/create-listing", methods=["GET", "POST"])
def create_listing():
    if "user_id" not in session or session.get("role") != "landlord":
        return redirect("/login")

    if request.method == "POST":
        title = request.form.get("title")
        city = request.form.get("city")
        price = request.form.get("price")
        type_ = request.form.get("type")
        desc = request.form.get("description")

        listing = Listing(
            title=title,
            city=city,
            price=int(price),
            type=type_,
            description=desc,
            user_id=session["user_id"]
        )

        db.session.add(listing)
        db.session.commit()

        files = request.files.getlist("images[]")
        current_max = db.session.query(func.max(ListingImage.sort_order)).filter_by(listing_id=listing.id).scalar() or 0

        for f in files:
            if f.filename:
                filename = secure_filename(f.filename)
                path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                f.save(path)

                current_max += 1
                img = ListingImage(
                    filename=filename,
                    listing_id=listing.id,
                    sort_order=current_max
                )
                db.session.add(img)

        db.session.commit()
        return redirect("/my-listings")

    return render_template("create_listing.html")


# ----------------------------
# EDIT LISTING
# ----------------------------

@app.route("/edit-listing/<int:id>", methods=["GET", "POST"])
def edit_listing(id):
    listing = Listing.query.get_or_404(id)

    if "user_id" not in session or listing.user_id != session["user_id"]:
        return redirect("/login")

    if request.method == "POST":
        listing.title = request.form.get("title")
        listing.city = request.form.get("city")
        listing.price = int(request.form.get("price"))
        listing.type = request.form.get("type")
        listing.description = request.form.get("description")

        files = request.files.getlist("images[]")
        current_max = db.session.query(func.max(ListingImage.sort_order)).filter_by(listing_id=listing.id).scalar() or 0

        for f in files:
            if f and f.filename:
                filename = secure_filename(f.filename)
                path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                f.save(path)

                current_max += 1
                img = ListingImage(
                    filename=filename,
                    listing_id=listing.id,
                    sort_order=current_max
                )
                db.session.add(img)

        db.session.commit()
        return redirect("/my-listings")

    images = ListingImage.query.filter_by(listing_id=id).all()
    return render_template("edit_listing.html", listing=listing, images=images)


@app.route("/delete-image/<int:id>")
def delete_image(id):
    img = ListingImage.query.get_or_404(id)
    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], img.filename))
    except FileNotFoundError:
        pass
    listing_id = img.listing_id
    db.session.delete(img)
    db.session.commit()
    return redirect(f"/edit-listing/{listing_id}")


# ----------------------------
# MY LISTINGS
# ----------------------------

@app.route("/my-listings")
def my_listings():
    if "user_id" not in session:
        return redirect("/login")

    listings_ = Listing.query.filter_by(user_id=session["user_id"]).all()

    for item in listings_:
        first = ListingImage.query \
            .filter_by(listing_id=item.id) \
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc()) \
            .first()

        item.image_filenames = [first.filename] if first else []

    return render_template("my_listings.html", listings=listings_)


# ----------------------------
# DASHBOARD
# ----------------------------

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    user = User.query.get(session["user_id"])

    if user.role == "admin":
        return render_template("dashboard_admin.html", user=user)
    if user.role == "landlord":
        return render_template("dashboard_landlord.html", user=user)
    else:
        return render_template("dashboard_tenant.html", user=user)


# ----------------------------
# REVIEWS
# ----------------------------

@app.route("/listing/<int:listing_id>/review", methods=["POST"])
def add_review(listing_id):
    if "user_id" not in session:
        return redirect("/login")

    rating = int(request.form.get("rating", 0))
    text = request.form.get("text", "").strip()

    if rating < 1 or rating > 5:
        return "Оценка должна быть от 1 до 5", 400

    existing = ReviewListing.query.filter_by(
        listing_id=listing_id,
        user_id=session["user_id"]
    ).first()

    if existing:
        return "Вы уже оставили отзыв для этого объявления", 400

    new_review = ReviewListing(
        listing_id=listing_id,
        user_id=session["user_id"],
        rating=rating,
        text=text
    )

    db.session.add(new_review)
    db.session.commit()

    return redirect(f"/listing/{listing_id}")


@app.route("/review/<int:review_id>/edit", methods=["POST"])
def edit_review(review_id):
    if "user_id" not in session:
        return redirect("/login")

    review = ReviewListing.query.get_or_404(review_id)

    if review.user_id != session["user_id"]:
        return "Нет доступа", 403

    review.rating = int(request.form.get("rating", review.rating))
    review.text = request.form.get("text", review.text)

    db.session.commit()
    return redirect(f"/listing/{review.listing_id}")


@app.route("/review/<int:review_id>/delete", methods=["POST"])
def delete_review(review_id):
    if "user_id" not in session:
        return redirect("/login")

    review = ReviewListing.query.get_or_404(review_id)

    if review.user_id != session["user_id"]:
        return "Нет доступа", 403

    listing_id = review.listing_id
    db.session.delete(review)
    db.session.commit()

    return redirect(f"/listing/{listing_id}")


@app.route("/listing/<int:listing_id>/reviews")
def listing_reviews(listing_id):
    listing = Listing.query.get_or_404(listing_id)

    return jsonify([
        {
            "id": r.id,
            "rating": r.rating,
            "text": r.text,
            "author": r.user.email,
            "created_at": r.created_at.strftime("%d.%m.%Y")
        }
        for r in listing.reviews
    ])


@app.route("/profile/my_reviews")
def my_reviews():
    if "user_id" not in session:
        return redirect("/login")

    reviews = ReviewListing.query.filter_by(user_id=session["user_id"]).all()

    return jsonify([
        {
            "id": r.id,
            "listing_id": r.listing_id,
            "listing_title": r.listing.title,
            "rating": r.rating,
            "text": r.text,
            "created_at": r.created_at.strftime("%d.%m.%Y")
        }
        for r in reviews
    ])


# ----------------------------
# PROFILE SETTINGS
# ----------------------------

@app.route("/profile/settings", methods=["GET", "POST"])
def profile_settings():
    if "user_id" not in session:
        return redirect("/login")

    user = User.query.get(session["user_id"])

    if request.method == "POST":
        user.name = request.form.get("name", user.name)

        new_email = request.form.get("email")
        if new_email and new_email != user.email:
            user.email = new_email

        theme = request.form.get("theme")
        if theme in ["light", "dark"]:
            user.theme = theme

        new_password = request.form.get("password")
        if new_password:
            # (оставил как у тебя было; лучше захешировать, но не трогаю логику без запроса)
            user.password = new_password

        db.session.commit()
        return redirect("/profile/settings")

    return render_template("profile_settings.html", user=user)


# ----------------------------
# CHAT
# ----------------------------

@app.route("/chat/start/<int:listing_id>")
def start_chat(listing_id):
    if "user_id" not in session:
        return redirect("/login")

    listing = Listing.query.get_or_404(listing_id)

    tenant_id = session["user_id"]
    landlord_id = listing.user_id

    if tenant_id == landlord_id:
        return "Нельзя писать самому себе", 400

    thread = MessageThread.query.filter_by(
        listing_id=listing_id,
        tenant_id=tenant_id,
        landlord_id=landlord_id
    ).first()

    if not thread:
        thread = MessageThread(
            listing_id=listing_id,
            tenant_id=tenant_id,
            landlord_id=landlord_id
        )
        db.session.add(thread)
        db.session.commit()

    return redirect(f"/chat/{thread.id}")


@app.route("/chat/<int:thread_id>")
def chat(thread_id):
    if "user_id" not in session:
        return redirect("/login")

    thread = MessageThread.query.get_or_404(thread_id)

    if session["user_id"] not in [thread.tenant_id, thread.landlord_id]:
        return "Нет доступа", 403

    messages = Message.query.filter_by(
        thread_id=thread.id
    ).order_by(Message.created_at.asc()).all()

    return render_template("chat.html", thread=thread, messages=messages)


@app.route("/chats")
def chats():
    if "user_id" not in session:
        return redirect("/login")

    uid = session["user_id"]

    threads = MessageThread.query.filter(
        (MessageThread.landlord_id == uid) |
        (MessageThread.tenant_id == uid)
    ).order_by(MessageThread.last_activity.desc()).all()

    return render_template("chats.html", threads=threads)


@app.route("/chat/<int:thread_id>/send", methods=["POST"])
def send_message(thread_id):
    if "user_id" not in session:
        return jsonify({"error": "auth"}), 401

    text = request.json.get("text", "").strip()
    if not text:
        return jsonify({"error": "empty"}), 400

    msg = Message(
        thread_id=thread_id,
        sender_id=session["user_id"],
        body=text
    )

    db.session.add(msg)

    thread = MessageThread.query.get(thread_id)
    thread.last_activity = datetime.utcnow()

    db.session.commit()

    return jsonify({
        "sender_id": msg.sender_id,
        "text": msg.body,
        "time": msg.created_at.strftime("%H:%M")
    })


# ----------------------------
# DEALS / RESERVATION (Relok flow)
# ----------------------------

DEAL_STATUSES = [
    ("reserved", "deal_reserved"),
    ("docs_pending", "deal_docs_pending"),
    ("docs_verified", "deal_docs_verified"),
    ("ready_to_sign", "deal_ready_to_sign"),
    ("ready_to_pay", "deal_ready_to_pay"),
    ("paid", "deal_paid"),
    ("completed", "deal_completed"),
    ("canceled", "deal_canceled"),
]

TENANT_DOC_TYPES = [
    ("passport", "doc_passport"),
    ("visa_or_residence", "doc_visa"),
    ("income_proof", "doc_income"),
    ("extra", "doc_extra"),
]

LANDLORD_DOC_TYPES = [
    ("ownership_proof", "Документ собственности"),
    ("landlord_id", "ID владельца"),
    ("extra", "Дополнительный документ"),
]


def require_login():
    return "user_id" in session


def is_admin():
    return session.get("role") == "admin"


def audit(deal_id: int, action: str, meta: str = ""):
    try:
        entry = DealAudit(
            deal_id=deal_id,
            actor_id=session.get("user_id"),
            action=action,
            meta=meta or ""
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_contract_pdf(path: str, deal: "Deal") -> None:
    """
    Variant A (MVP): generate an UNSIGNED contract PDF from Jinja2 template and
    let users sign manually and upload back.
    """
    try:
        from weasyprint import HTML

        html = render_template("contract_template.html", deal=deal)
        base_url = request.url_root
        HTML(string=html, base_url=base_url).write_pdf(path)
        return

    except Exception as e:
        # Fallback: minimal PDF so flow never breaks
        c = canvas.Canvas(path, pagesize=A4)
        w, h = A4
        y = h - 60
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, y, f"Rental Agreement / Vertrag (fallback) — Deal #{deal.id}")
        y -= 30

        c.setFont("Helvetica", 11)
        lines = [
            f"Listing: {deal.listing.title if deal.listing else deal.listing_id}",
            f"City: {deal.listing.city if deal.listing else ''}",
            f"Tenant: {deal.tenant.name or ''} ({deal.tenant.email})",
            f"Landlord: {deal.landlord.name or ''} ({deal.landlord.email})",
            f"Period: {deal.start_date} → {deal.end_date}",
            f"Monthly rent: {deal.listing.price if deal.listing else ''} EUR",
            "",
            f"PDF generation fallback reason: {type(e).__name__}",
        ]
        for line in lines:
            c.drawString(50, y, line)
            y -= 16
            if y < 80:
                c.showPage()
                y = h - 60
                c.setFont("Helvetica", 11)

        c.showPage()
        c.save()


def ensure_deal_folder(deal_id: int):
    base_dir = os.path.join(app.config["UPLOAD_FOLDER"], "deals", str(deal_id))
    os.makedirs(base_dir, exist_ok=True)
    return base_dir

def attach_contract_from_template(deal: "Deal", actor_id: int) -> DealContract:
    """
    Прикрепляет к сделке UNSIGNED договор из готового PDF-шаблона.
    Если договор уже есть — ничего не делает (можно поменять поведение ниже).
    """
    # 1) Путь к твоему PDF-шаблону
    template_pdf = os.path.join("static", "contracts", "Relovo_Mietvertrag_MVP_DE_EN.pdf")
    if not os.path.exists(template_pdf):
        raise FileNotFoundError("Шаблон договора не найден: static/contracts/Relovo_Mietvertrag_MVP_DE_EN.pdf")

    # 2) Если уже прикреплён — просто вернём существующий
    existing = DealContract.query.filter_by(deal_id=deal.id).first()
    if existing and existing.unsigned_filename and existing.unsigned_sha256:
        return existing

    # 3) Копируем шаблон в папку сделки
    folder = ensure_deal_folder(deal.id)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored = f"contract_unsigned_{ts}.pdf"
    dst_path = os.path.join(folder, stored)
    shutil.copyfile(template_pdf, dst_path)

    unsigned_sha = sha256_file(dst_path)

    # 4) Создаём/обновляем запись в БД
    if existing:
        # на всякий случай: если была “пустая/битая” запись
        existing.unsigned_filename = f"uploads/deals/{deal.id}/{stored}"
        existing.unsigned_sha256 = unsigned_sha
        existing.created_at = datetime.utcnow()
        existing.created_by_id = actor_id

        # Если хочешь при авто-прикреплении сбрасывать подписи — раскомментируй:
        # DealContractSigned.query.filter_by(contract_id=existing.id).delete()

        contract = existing
    else:
        contract = DealContract(
            deal_id=deal.id,
            unsigned_filename=f"uploads/deals/{deal.id}/{stored}",
            unsigned_sha256=unsigned_sha,
            created_by_id=actor_id
        )
        db.session.add(contract)

    return contract



@app.route("/reserve/<int:listing_id>", methods=["POST"])
def reserve_listing(listing_id):
    if not require_login():
        return redirect("/login")

    user = User.query.get(session["user_id"])
    if not user or user.role != "tenant":
        return "Только арендатор может резервировать", 403

    listing = Listing.query.get_or_404(listing_id)

    if listing.user_id == user.id:
        return "Нельзя резервировать своё объявление", 400

    tenant_note = (request.form.get("tenant_note") or "").strip()

    existing = Deal.query.filter_by(
        listing_id=listing_id,
        tenant_id=user.id,
        landlord_id=listing.user_id
    ).filter(Deal.status != "canceled").first()

    if existing:
        return redirect(f"/deal/{existing.id}")

    deal = Deal(
        listing_id=listing.id,
        tenant_id=user.id,
        landlord_id=listing.user_id,
        created_by_id=user.id,
        status="reserved",
        tenant_note=tenant_note
    )
    db.session.add(deal)
    db.session.commit()

    audit(deal.id, "deal_created", f"listing_id={listing.id}")
    return redirect(f"/deal/{deal.id}")


@app.route("/deals")
def deals_list():
    if not require_login():
        return redirect("/login")

    uid = session["user_id"]
    role = session.get("role")

    if role == "admin":
        return redirect("/admin/deals")

    if role == "landlord":
        deals = Deal.query.filter_by(landlord_id=uid).order_by(Deal.updated_at.desc()).all()
    else:
        deals = Deal.query.filter_by(tenant_id=uid).order_by(Deal.updated_at.desc()).all()

    # FIX: тут НЕ надо передавать contract/signed_contracts — они не определены в этом роуте
    return render_template("deals_list.html", deals=deals, role=role)


@app.route("/deal/<int:deal_id>")
def deal_detail(deal_id):
    if not require_login():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    uid = session["user_id"]
    role = session.get("role")

    if role != "admin" and uid not in [deal.tenant_id, deal.landlord_id]:
        return "Нет доступа", 403

    docs = DealDocument.query.filter_by(deal_id=deal.id).order_by(DealDocument.created_at.desc()).all()
    audit_log = DealAudit.query.filter_by(deal_id=deal.id).order_by(DealAudit.created_at.desc()).limit(50).all()

    if role == "landlord":
        doc_types = LANDLORD_DOC_TYPES
        party = "landlord"
    elif role == "tenant":
        doc_types = TENANT_DOC_TYPES
        party = "tenant"
    else:
        doc_types = []
        party = "admin"

    contract = DealContract.query.filter_by(deal_id=deal.id).first()

    signed_contracts = {}
    if contract:
        for s in DealContractSigned.query.filter_by(contract_id=contract.id).all():
            signed_contracts[s.party] = s

    return render_template(
        "deal_detail.html",
        deal=deal,
        docs=docs,
        audit_log=audit_log,
        statuses=DEAL_STATUSES,
        doc_types=doc_types,
        party=party,
        role=role,
        contract=contract,
        signed_contracts=signed_contracts
    )


@app.route("/deal/<int:deal_id>/contract/generate", methods=["POST"])
def deal_generate_contract(deal_id):
    if not require_login():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    uid = session["user_id"]
    role = session.get("role")

    if role != "admin" and uid not in [deal.tenant_id, deal.landlord_id]:
        return "Нет доступа", 403

    if not (deal.start_date and deal.end_date and deal.dates_confirmed):
        return "Сначала нужно указать и подтвердить даты аренды", 400

    # 1) Положи твой PDF сюда:
    # static/contracts/Relovo_Mietvertrag_MVP_DE_EN.pdf
    template_pdf = os.path.join("static", "contracts", "Relovo_Mietvertrag_MVP_DE_EN.pdf")
    if not os.path.exists(template_pdf):
        return "Шаблон договора не найден: static/contracts/Relovo_Mietvertrag_MVP_DE_EN.pdf", 400

    # 2) Копируем в папку сделки (чтобы у каждой сделки был “свой” файл)
    folder = ensure_deal_folder(deal.id)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored = f"contract_unsigned_{ts}.pdf"
    dst_path = os.path.join(folder, stored)
    shutil.copyfile(template_pdf, dst_path)

    unsigned_sha = sha256_file(dst_path)

    existing = DealContract.query.filter_by(deal_id=deal.id).first()
    if existing:
        DealContractSigned.query.filter_by(contract_id=existing.id).delete()
        existing.unsigned_filename = f"uploads/deals/{deal.id}/{stored}"
        existing.unsigned_sha256 = unsigned_sha
        existing.created_at = datetime.utcnow()
        existing.created_by_id = uid
    else:
        contract = DealContract(
            deal_id=deal.id,
            unsigned_filename=f"uploads/deals/{deal.id}/{stored}",
            unsigned_sha256=unsigned_sha,
            created_by_id=uid
        )
        db.session.add(contract)

    deal.touch()
    db.session.commit()
    audit(deal.id, "contract_attached", f"sha256={unsigned_sha}")

    return redirect(f"/deal/{deal.id}")


@app.route("/deal/<int:deal_id>/contract/upload", methods=["POST"])
def deal_upload_signed_contract(deal_id):
    if not require_login():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    uid = session["user_id"]
    role = session.get("role")

    if role not in ["tenant", "landlord"]:
        return "Нет доступа", 403
    if role == "tenant" and uid != deal.tenant_id:
        return "Нет доступа", 403
    if role == "landlord" and uid != deal.landlord_id:
        return "Нет доступа", 403

    contract = DealContract.query.filter_by(deal_id=deal.id).first()
    if not contract:
        return "Сначала нужно сгенерировать договор", 400

    f = request.files.get("file")
    if not f or not f.filename:
        return "Файл не выбран", 400

    filename = secure_filename(f.filename)
    folder = ensure_deal_folder(deal.id)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored = f"contract_signed_{role}_{ts}_{filename}"
    path = os.path.join(folder, stored)
    f.save(path)

    signed_sha = sha256_file(path)

    existing = DealContractSigned.query.filter_by(contract_id=contract.id, party=role).first()
    if existing:
        existing.filename = f"uploads/deals/{deal.id}/{stored}"
        existing.sha256 = signed_sha
        existing.uploaded_at = datetime.utcnow()
        existing.uploader_id = uid
    else:
        rec = DealContractSigned(
            contract_id=contract.id,
            party=role,
            filename=f"uploads/deals/{deal.id}/{stored}",
            sha256=signed_sha,
            uploader_id=uid
        )
        db.session.add(rec)

    deal.touch()
    db.session.commit()
    audit(deal.id, "contract_signed_upload", f"party={role}; sha256={signed_sha}")

    return redirect(f"/deal/{deal.id}")


@app.route("/deal/<int:deal_id>/upload", methods=["POST"])
def deal_upload_document(deal_id):
    if not require_login():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    uid = session["user_id"]
    role = session.get("role")

    if role not in ["tenant", "landlord"]:
        return "Нет доступа", 403
    if role == "tenant" and uid != deal.tenant_id:
        return "Нет доступа", 403
    if role == "landlord" and uid != deal.landlord_id:
        return "Нет доступа", 403

    doc_type = (request.form.get("doc_type") or "").strip()
    if not doc_type:
        return "Не указан тип документа", 400

    f = request.files.get("file")
    if not f or not f.filename:
        return "Файл не выбран", 400

    filename = secure_filename(f.filename)

    folder = ensure_deal_folder(deal.id)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored = f"{role}_{doc_type}_{ts}_{filename}"
    path = os.path.join(folder, stored)
    f.save(path)

    doc = DealDocument(
        deal_id=deal.id,
        uploader_id=uid,
        party=role,
        doc_type=doc_type,
        filename=f"uploads/deals/{deal.id}/{stored}",
        status="pending"
    )
    db.session.add(doc)

    if deal.status == "reserved":
        deal.status = "docs_pending"
    deal.touch()

    db.session.commit()
    audit(deal.id, "doc_upload", f"type={doc_type},file={doc.filename}")

    return redirect(f"/deal/{deal.id}")


# ----------------------------
# ADMIN PANEL
# ----------------------------

def require_admin():
    if not require_login():
        return False
    return is_admin()


@app.route("/admin")
def admin_home():
    if not require_admin():
        return redirect("/login")
    return redirect("/admin/listings")


@app.route("/admin/deals")
def admin_deals():
    if not require_admin():
        return redirect("/login")

    status = request.args.get("status", "").strip()
    q = Deal.query
    if status:
        q = q.filter(Deal.status == status)

    deals = q.order_by(Deal.updated_at.desc()).all()
    return render_template("admin_deals.html", deals=deals, statuses=DEAL_STATUSES, active_status=status)


@app.route("/admin/deal/<int:deal_id>")
def admin_deal_detail(deal_id):
    if not require_admin():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    docs = DealDocument.query.filter_by(deal_id=deal.id).order_by(DealDocument.created_at.desc()).all()
    audit_log = DealAudit.query.filter_by(deal_id=deal.id).order_by(DealAudit.created_at.desc()).limit(200).all()

    # --- CONTRACT ---
    contract = DealContract.query.filter_by(deal_id=deal.id).first()

    signed_contracts = []
    signed_map = {}
    if contract:
        signed_contracts = DealContractSigned.query.filter_by(contract_id=contract.id).all()
        signed_map = {s.party: s for s in signed_contracts}  # {"tenant": obj, "landlord": obj}

    return render_template(
        "admin_deal_detail.html",
        deal=deal,
        docs=docs,
        audit_log=audit_log,
        statuses=DEAL_STATUSES,
        contract=contract,
        signed_contracts=signed_contracts,   # список (если тебе нужно где-то for)
        signed_map=signed_map                # словарь (для .get в шаблоне)
    )



@app.route("/admin/deal/<int:deal_id>/status", methods=["POST"])
def admin_set_deal_status(deal_id):
    if not require_admin():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    new_status = (request.form.get("status") or "").strip()

    allowed = {s for s, _ in DEAL_STATUSES}
    if new_status not in allowed:
        return "Неверный статус", 400

    old = deal.status
    deal.status = new_status
    deal.admin_id = session["user_id"]
    deal.touch()

    # ✅ Автоприкрепление договора при переходе в ready_to_sign
    if new_status == "ready_to_sign":
        # (опционально) можно требовать подтверждённые даты:
        if not deal.dates_confirmed or not deal.start_date or not deal.end_date:
            return "Нельзя перевести в 'ready_to_sign' без подтверждённых дат аренды", 400

        try:
            contract = attach_contract_from_template(deal, actor_id=session["user_id"])
            # здесь sha может быть у уже существующего или нового
            audit(deal.id, "contract_attached_auto", f"sha256={contract.unsigned_sha256}")
        except FileNotFoundError as e:
            return str(e), 400
        except Exception as e:
            # чтобы не “падало” молча
            db.session.rollback()
            return f"Ошибка при прикреплении договора: {type(e).__name__}", 500

    db.session.commit()
    audit(deal.id, "status_change", f"{old} -> {new_status}")

    return redirect(f"/admin/deal/{deal.id}")


@app.route("/admin/document/<int:doc_id>/review", methods=["POST"])
def admin_review_document(doc_id):
    if not require_admin():
        return redirect("/login")

    doc = DealDocument.query.get_or_404(doc_id)
    decision = (request.form.get("decision") or "").strip()
    note = (request.form.get("note") or "").strip()

    if decision not in ["approved", "rejected"]:
        return "Неверное действие", 400

    doc.status = decision
    doc.note = note or None
    doc.reviewed_at = datetime.utcnow()
    doc.reviewed_by_admin_id = session["user_id"]

    deal = Deal.query.get(doc.deal_id)
    if deal:
        deal.admin_id = session["user_id"]
        deal.touch()

    db.session.commit()
    audit(doc.deal_id, "doc_review", f"doc_id={doc.id}, decision={decision}")

    return redirect(f"/admin/deal/{doc.deal_id}")


@app.route("/admin/deal/<int:deal_id>/cancel", methods=["POST"])
def admin_cancel_deal(deal_id):
    if not require_admin():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    reason = (request.form.get("reason") or "").strip()

    old = deal.status
    deal.status = "canceled"
    deal.admin_id = session["user_id"]
    deal.touch()
    db.session.commit()

    audit(deal.id, "deal_canceled", f"{old} -> canceled; reason={reason}")
    return redirect(f"/admin/deal/{deal.id}")


@app.route("/admin/listing/<int:listing_id>/delete", methods=["POST"])
def admin_delete_listing(listing_id):
    if not require_admin():
        abort(403)

    listing = Listing.query.get_or_404(listing_id)

    deals = Deal.query.filter_by(listing_id=listing.id).all()
    for deal in deals:
        deal_folder = os.path.join(app.config["UPLOAD_FOLDER"], "deals", str(deal.id))
        if os.path.exists(deal_folder):
            shutil.rmtree(deal_folder)

        DealDocument.query.filter_by(deal_id=deal.id).delete()
        DealAudit.query.filter_by(deal_id=deal.id).delete()

        db.session.delete(deal)

    images = ListingImage.query.filter_by(listing_id=listing.id).all()
    for img in images:
        try:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], img.filename))
        except FileNotFoundError:
            pass
        db.session.delete(img)

    db.session.delete(listing)
    db.session.commit()

    return redirect("/admin/listings")


@app.route("/admin/listings")
def admin_listings():
    if not require_admin():
        return redirect("/login")

    listings_ = Listing.query.order_by(Listing.created_at.desc()).all()

    for item in listings_:
        image = ListingImage.query.filter_by(listing_id=item.id) \
            .order_by(ListingImage.sort_order.asc(), ListingImage.id.asc()) \
            .first()
        item.image_filenames = [image.filename] if image else []

    return render_template("admin_listings.html", listings=listings_)


@app.route("/deal/<int:deal_id>/dates", methods=["POST"])
def set_deal_dates(deal_id):
    if not require_login():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)

    if session["user_id"] != deal.tenant_id:
        return "Нет доступа", 403

    start = request.form.get("start_date")
    end = request.form.get("end_date")

    if not start or not end:
        return "Даты обязательны", 400

    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()

    if start_date >= end_date:
        return "Неверный период", 400

    deal.start_date = start_date
    deal.end_date = end_date
    deal.dates_confirmed = False
    deal.touch()

    db.session.commit()
    audit(deal.id, "dates_set", f"{start_date} → {end_date}")

    return redirect(f"/deal/{deal.id}")

@app.route("/deal/<int:deal_id>/confirm-dates", methods=["POST"])
def landlord_confirm_dates(deal_id):
    if "user_id" not in session:
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)
    uid = session["user_id"]
    role = session.get("role")

    # Только арендодатель этой сделки
    if role != "landlord" or uid != deal.landlord_id:
        return "Нет доступа", 403

    # Даты должны быть выбраны арендатором
    if not (deal.start_date and deal.end_date):
        return "Сначала арендатор должен выбрать даты", 400

    # Подтверждаем
    deal.dates_confirmed = True
    deal.touch()
    db.session.commit()

    audit(deal.id, "dates_confirmed", "by=landlord")

    return redirect(f"/deal/{deal.id}")


@app.route("/admin/deal/<int:deal_id>/confirm-dates", methods=["POST"])
def admin_confirm_dates(deal_id):
    if not require_admin():
        return redirect("/login")

    deal = Deal.query.get_or_404(deal_id)

    deal.dates_confirmed = True
    deal.touch()
    db.session.commit()

    audit(deal.id, "dates_confirmed")

    return redirect(f"/admin/deal/{deal.id}")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/impressum")
def impressum():
    return render_template("impressum.html")


# ----------------------------
# RUN
# ----------------------------

if __name__ == "__main__":
    app.run(debug=True)
