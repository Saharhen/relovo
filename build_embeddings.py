# build_embeddings.py
from app import app, db
from models import Listing
from ai_utils import get_embedding

with app.app_context():
    listings = Listing.query.all()

    for listing in listings:
        text = f"{listing.title}. {listing.city}. {listing.description or ''}"

        print(f"Embedding listing {listing.id} — {listing.title}")
        embedding = get_embedding(text)

        listing.embedding = embedding

    db.session.commit()

print("Done! All embeddings created.")
