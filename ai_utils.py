# ai_utils.py
import os
import numpy as np
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL = "text-embedding-3-large"  # можно и text-embedding-3-small

def get_embedding(text: str) -> list[float]:
    """
    Возвращает эмбеддинг текста как список float.
    """
    text = text.replace("\n", " ")
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=text
    )
    return resp.data[0].embedding

def cosine_sim(a: list[float], b: list[float]) -> float:
    """
    Косинусное сходство двух векторов.
    """
    a = np.array(a)
    b = np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
