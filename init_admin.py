import os
from app import app, db
from models import User
from werkzeug.security import generate_password_hash

with app.app_context():
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")

    if not email or not password:
        raise Exception("Admin credentials not set")

    admin = User(
        email=email,
        password=generate_password_hash(password),
        role="admin"
    )

    db.session.add(admin)
    db.session.commit()
    print("Admin created")
