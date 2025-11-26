# app.py
import os
import random
from typing import List, Dict, Optional

from flask import (
    Flask, render_template, request, redirect, url_for, flash
)
from sqlalchemy import (
    create_engine, Column, String, Integer, JSON
)
from sqlalchemy.orm import declarative_base, sessionmaker

# --- Configuration Flask ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# --- Configuration DB ---
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Use SQLite for local development
    DATABASE_URL = "sqlite:///./secret_santa.db"
    print("DEBUG: Using SQLite for local development")
else:
    print(f"DEBUG: Using production database: {DATABASE_URL[:50]}...")

# Convert psycopg2 URL to psycopg URL if needed (only for PostgreSQL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# SQLAlchemy setup
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# --- Modèles ---
class Config(Base):
    """
    Table clé-valeur pour stocker des blobs JSON (names, compat).
    key: ex. 'names', 'compat'
    value: JSON
    """
    __tablename__ = "config"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(JSON, nullable=True)

class Participant(Base):
    """
    Table participants : nom (PK), mot de passe, receiver
    """
    __tablename__ = "participants"
    name = Column(String(200), primary_key=True)
    password = Column(String(200), nullable=False)
    receiver = Column(String(200), nullable=False)

# --- Fonction d'initialisation des tables ---
_db_initialized = False

def ensure_db_init():
    """S'assure que la base de données est initialisée"""
    global _db_initialized
    if not _db_initialized:
        try:
            Base.metadata.create_all(engine)
            _db_initialized = True
            print("DEBUG: Database initialized successfully")
        except Exception as e:
            print(f"DEBUG: Database initialization failed: {e}")
            raise

def init_db():
    """Initialise les tables de la base de données"""
    Base.metadata.create_all(engine)

# -------------------------
# Utilitaires DB (petits CRUD)
# -------------------------
def db_get_config(key: str):
    ensure_db_init()
    with SessionLocal() as db:
        row = db.query(Config).filter_by(key=key).one_or_none()
        return row.value if row is not None else None

def db_set_config(key: str, value):
    ensure_db_init()
    try:
        with SessionLocal() as db:
            row = db.query(Config).filter_by(key=key).one_or_none()
            if row is None:
                row = Config(key=key, value=value)
                db.add(row)
            else:
                row.value = value
            db.commit()
            print(f"DEBUG: Successfully saved config {key} = {value}")
    except Exception as e:
        print(f"DEBUG: Failed to save config {key}: {e}")
        raise

def db_clear_participants():
    ensure_db_init()
    with SessionLocal() as db:
        db.query(Participant).delete()
        db.commit()

def db_get_all_participants() -> Dict[str, Dict]:
    ensure_db_init()
    with SessionLocal() as db:
        rows = db.query(Participant).all()
        return {r.name: {"password": r.password, "target": r.receiver} for r in rows}

def db_save_participants(participants: Dict[str, Dict[str, str]]):
    """
    participants : {name: {"password": pw, "target": target}}
    On écrase la table participants.
    """
    ensure_db_init()
    with SessionLocal() as db:
        db.query(Participant).delete()
        for name, info in participants.items():
            p = Participant(name=name, password=info["password"], receiver=info["target"])
            db.add(p)
        db.commit()

# -------------------------
# Mot de passe thématique Noël
# -------------------------
def gen_password_christmas(existing_set: Optional[set] = None) -> str:
    adjectifs = [
        "joyeux", "blanc", "rouge", "vert", "dore", "argente", "brillant",
        "festif", "magique", "hivernal", "sucre", "gourmand", "glace",
        "etincelant", "lumineux",
        "merveilleux", "etonnant", "petillant", "enchante", "radieux",
         "epique", "fantastique"
    ]

    noms = [
    "sapin", "renne", "lutin", "traineau", "bonnet", "cadeau",
    "houx", "flocon", "pain_depice",
    "ours", "elfe", "jouet", "carillon",
    "bonhomme",
    "ruban", "pere_noel", "rudolf"
    ]

    if existing_set is None:
        existing_set = set()

    # essaie d'éviter collisions en ajoutant un petit suffixe numérique si nécessaire
    for _ in range(100):
        candidate = f"{random.choice(adjectifs)}_{random.choice(noms)}"
        if candidate not in existing_set:
            return candidate
    # fallback : ajoute un numéro aléatoire
    return f"{random.choice(adjectifs)}_{random.choice(noms)}_{random.randint(10,99)}"

# -------------------------
# Algorithme d'affectation (essai par permutations)
# -------------------------
def find_assignment(names: List[str], compat_matrix: List[List[int]], max_tries: int = 100000) -> Optional[Dict[str,str]]:
    n = len(names)
    if n == 0:
        return {}

    idx = list(range(n))
    for _ in range(max_tries):
        perm = idx[:]
        random.shuffle(perm)
        ok = True
        for i, j in enumerate(perm):
            # compat_matrix must be indexed same order as names
            if compat_matrix[i][j] != 1:
                ok = False
                break
        if ok:
            return {names[i]: names[perm[i]] for i in range(n)}
    return None

# -------------------------
# Routes Admin / Participant (tous en français)
# -------------------------
@app.route("/")
def home():
    return redirect(url_for("participant_login"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == "super_santa_2025":
            return redirect(url_for("admin_start"))
        else:
            flash("Mot de passe incorrect.", "danger")
    
    return render_template("admin_login.html")

@app.route("/admin/start", methods=["GET", "POST"])
def admin_start():
    if request.method == "POST":
        raw = request.form.get("names", "")
        lines = [l.strip() for l in raw.replace(",", "\n").splitlines() if l.strip()]
        names = []
        seen = set()
        for l in lines:
            if l not in seen:
                names.append(l)
                seen.add(l)
        if len(names) < 2:
            flash("Il faut au moins 2 participants.", "danger")
            return render_template("admin_start.html", names_raw=raw)
        # sauvegarde noms et réinitialise compat & participants
        print(f"DEBUG: Saving names: {names}")  # Debug line
        db_set_config("names", names)
        print("DEBUG: Names saved successfully")  # Debug line
        # reset de compat et participants
        db_set_config("compat", [])
        db_clear_participants()
        flash("Liste enregistrée. Définissez la matrice de compatibilité.", "success")
        return redirect(url_for("admin_matrix"))

    # GET
    names = db_get_config("names") or []
    names_raw = "\n".join(names)
    return render_template("admin_start.html", names_raw=names_raw)

@app.route("/admin/matrix", methods=["GET", "POST"])
def admin_matrix():
    names = db_get_config("names") or []
    print(f"DEBUG: Retrieved names: {names}")  # Debug line
    if not names:
        print("DEBUG: No names found, redirecting to admin_start")  # Debug line
        return redirect(url_for("admin_start"))
    n = len(names)

    if request.method == "POST":
        compat = [[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                key = f"c_{i}_{j}"
                compat[i][j] = 1 if request.form.get(key) == "on" else 0

        # validation basique : chaque donneur doit pouvoir offrir à au moins une personne
        for i in range(n):
            if sum(compat[i]) == 0:
                flash(f"Le participant {names[i]} ne peut offrir à personne (ligne vide).", "danger")
                return render_template("admin_matrix.html", names=names, compat=compat)

        db_set_config("compat", compat)
        flash("Matrice enregistrée.", "success")
        return redirect(url_for("admin_generate"))

    compat = db_get_config("compat")
    if not compat:
        compat = [[1]*n for _ in range(n)]
        for i in range(n):
            compat[i][i] = 0

    return render_template("admin_matrix.html", names=names, compat=compat)

@app.route("/admin/generate")
def admin_generate():
    names = db_get_config("names") or []
    compat = db_get_config("compat") or []
    if not names or not compat:
        flash("Données manquantes. Recommencez.", "danger")
        return redirect(url_for("admin_start"))

    assignment = find_assignment(names, compat, max_tries=100000)
    if assignment is None:
        flash("Impossible de trouver une affectation respectant la matrice. Modifiez la matrice et réessayez.", "danger")
        return redirect(url_for("admin_matrix"))

    # génération des mots de passe festifs, sans collision
    existing = set()
    participants = {}
    for name in names:
        pw = gen_password_christmas(existing)
        existing.add(pw)
        participants[name] = {"target": assignment[name], "password": pw}

    # sauvegarde en base (écrase la table participants)
    db_save_participants(participants)
    flash("Affectations générées et sauvegardées.", "success")
    # afficher résultats (récupère depuis la DB pour garantir cohérence)
    saved = db_get_all_participants()
    return render_template("admin_results.html", participants=saved)

# Participant
@app.route("/participant", methods=["GET", "POST"])
def participant_login():
    participants = db_get_all_participants()
    names = list(db_get_config("names") or [])
    if not participants:
        flash("Aucune affectation n'est disponible. Contactez l'administrateur.", "warning")
        return render_template("participant_login.html", names=[])

    if request.method == "POST":
        name = request.form.get("name")
        pwd = request.form.get("password", "")
        if name not in participants:
            flash("Nom invalide.", "danger")
            return render_template("participant_login.html", names=names)

        real = participants[name]["password"]
        if pwd.strip() == real:
            password = participants[name]["password"]
            return render_template("participant_result.html", name=name, password=password)
        else:
            flash("Mot de passe incorrect.", "danger")
            return render_template("participant_login.html", names=names)

    return render_template("participant_login.html", names=names)

# Export JSON (optionnel) - retourne un dump des participants (pratique pour sauvegarde)
@app.route("/admin/export")
def admin_export():
    participants = db_get_all_participants()
    # on renvoie un JSON string manuel pour l'export (Flask Response)
    import json
    content = json.dumps(participants, ensure_ascii=False, indent=2)
    return app.response_class(
        content,
        mimetype='application/json',
        headers={"Content-Disposition": "attachment;filename=secret_santa_postgres.json"}
    )

# Lancer en local (debug)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
