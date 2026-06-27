import urllib.request
import json
import math
import re
from datetime import datetime, date
import streamlit as st

# ================= CONFIG =================
MAX_SELECTIONS = 3
MIN_SCORE = 78
MIN_CONF  = 72
MIN_VALUE = 1
TIMEOUT   = 12
BASE_URL  = "https://online.turfinfo.api.pmu.fr/rest/client/1/programme"

# ================= API ====================
def pmu_get(path):
    url = BASE_URL + path
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

def today():
    return datetime.now().strftime("%d%m%Y")

# =============== OUTILS ===================
def clamp(v, a, b):
    return max(a, min(v, b))

def dict_id(v):
    """Retourne v['id'] si v est un dict, sinon None — évite AttributeError."""
    return v.get("id") if isinstance(v, dict) else None

def safe_float(v):
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except:
        return None

def safe_int(v, default=0):
    try:
        return int(v)
    except:
        return default

def detect_discipline(course):
    txt = str(course.get("discipline", "")).upper()
    if "ATTEL" in txt or "MONT" in txt or "TROT" in txt:
        return "TROT"
    if "HAIE" in txt or "STEEPLE" in txt or "CROSS" in txt:
        return "OBSTACLE"
    return "PLAT"

# =============== MUSIQUE ==================
def parse_musique(m):
    if not m:
        return []
    m = re.sub(r"\s", "", str(m))
    vals = []
    i = 0
    while i < len(m):
        c = m[i]
        if c.isdigit():
            num = int(c)
            if i + 1 < len(m) and m[i+1].isdigit() and c != "0":
                num = int(c + m[i+1])
                i += 1
            vals.append(("pos", num))
        elif c.lower() == "d":
            vals.append(("disq", None))
        elif c.lower() == "a":
            vals.append(("abs", None))
        i += 1
    return vals[:10]

# ============= CRITÈRES DE BASE ===========

def score_forme(music):
    """Pondération exponentielle des positions récentes."""
    score = 0
    for i, (typ, pos) in enumerate(music):
        if typ != "pos":
            continue
        w = math.exp(-0.35 * i)
        if pos == 1:       score += 12 * w
        elif pos <= 3:     score += 8 * w
        elif pos <= 5:     score += 4 * w
        elif pos <= 8:     score += 1.5 * w
    return clamp(score, 0, 20)

def score_regularite(music):
    """Faible écart-type = cheval régulier."""
    vals = [p for t, p in music if t == "pos"]
    if len(vals) < 4:
        return 0
    moy = sum(vals) / len(vals)
    sigma = math.sqrt(sum((x - moy)**2 for x in vals) / len(vals))
    return clamp(8 - sigma, 0, 8)

def score_progression(music):
    """Bonus si les positions s'améliorent sur les 5 dernières courses."""
    vals = [p for t, p in music if t == "pos"][:5]
    if len(vals) < 3:
        return 0
    n = len(vals)
    x_moy = (n - 1) / 2
    y_moy = sum(vals) / n
    num = sum((i - x_moy) * (vals[i] - y_moy) for i in range(n))
    den = sum((i - x_moy)**2 for i in range(n))
    if den == 0:
        return 0
    pente = num / den
    if pente < -0.5:   return 5
    elif pente < 0:    return 2
    elif pente > 0.5:  return -2
    return 0

def score_ratio_place(cheval):
    """% de courses dans le top 3.
    nombrePlaces inclut déjà les victoires côté API PMU — pas besoin d'ajouter vict.
    """
    total  = safe_int(cheval.get("nombreCourses", 0))
    places = safe_int(cheval.get("nombrePlaces", 0)) or safe_int(cheval.get("nombrePlace", 0))
    if total < 3:
        return 0
    ratio = places / total
    return clamp(ratio * 8, 0, 8)

def score_marche(ch):
    cote = (ch.get("dernierRapportDirect") or {}).get("rapport")
    if not cote:
        cote = ch.get("coteInitiale")
    cote = safe_float(cote)
    if not cote:
        return 0, None
    prob = clamp(1 / cote, 0, 0.95)
    return prob * 12, cote

# ============= NOUVEAUX CRITÈRES ==========

def score_fraicheur(cheval):
    """Bonus/malus selon le nombre de jours depuis la dernière course."""
    s = 0
    derniere = cheval.get("dateDerniereCourse") or cheval.get("derniereCourseDateFr")
    if not derniere:
        return 0
    try:
        if isinstance(derniere, (int, float)):
            d = datetime.fromtimestamp(derniere / 1000).date()
        else:
            raw = str(derniere)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    d = datetime.strptime(raw[:10], fmt).date()
                    break
                except ValueError:
                    continue
            else:
                return 0
        jours = (date.today() - d).days
        if jours < 0:
            return 0
        if 14 <= jours <= 35:   s += 4
        elif 7 <= jours < 14:   s += 2
        elif 35 < jours <= 60:  s += 1
        elif jours > 90:        s -= 3
        elif jours > 60:        s -= 1
    except:
        pass
    return round(s, 1)

def score_allocation(course):
    """Bonus selon le niveau (allocation) de la course."""
    alloc = safe_float(
        course.get("allocation") or course.get("montantPrix") or course.get("totalOffert")
    )
    if not alloc:
        return 0
    if alloc >= 100000:   return -2
    elif alloc >= 50000:  return 1
    elif alloc >= 20000:  return 3
    elif alloc >= 10000:  return 2
    else:                 return 1

def score_nb_partants(course):
    """Malus si beaucoup de partants (aléatoire augmente)."""
    nb = safe_int(course.get("nombreDeclaresPartants", 0))
    if nb == 0:
        return 0
    if nb <= 6:    return 3
    elif nb <= 9:  return 1
    elif nb <= 12: return 0
    elif nb <= 15: return -1
    else:          return -3

def score_recul_trot(cheval):
    """Malus selon les mètres de recul en trot."""
    recul = safe_float(cheval.get("handicapDistance") or cheval.get("distanceHandicap") or 0)
    if not recul or recul <= 0:
        return 0
    if recul <= 25:    return -1
    elif recul <= 50:  return -2
    elif recul <= 75:  return -4
    else:              return -6

def score_gains_annee(cheval):
    """Gains de l'année en cours — plus représentatif que la carrière."""
    gains = safe_float(cheval.get("gainsAnneeEnCours") or cheval.get("gainsCourseAnneeCourante") or 0)
    if not gains:
        return 0
    return clamp(gains / 30000 * 5, 0, 5)

def score_sexe(cheval):
    """Légère préférence pour les hongres (plus réguliers)."""
    sexe = str(cheval.get("sexe") or cheval.get("indicateurSexe") or "").upper()
    if "HONG" in sexe or sexe == "H":
        return 2
    return 0

def score_entraineur(cheval, tous_partants):
    """Taux de victoires de l'entraîneur parmi les partants de la réunion."""
    s = 0
    ent_id = dict_id(cheval.get("entraineur"))
    if not ent_id or not tous_partants:
        return 0
    victoires = 0
    sorties   = 0
    for p in tous_partants:
        if dict_id(p.get("entraineur")) == ent_id:
            sorties += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                victoires += 1
    if sorties >= 3:
        taux = victoires / sorties
        s += clamp(taux * 8, 0, 5)
    return round(s, 1)

def score_forme_ecurie(cheval, tous_partants):
    """Bonus si l'écurie/propriétaire a gagné récemment dans la réunion."""
    prop_id = dict_id(cheval.get("proprietaire"))
    if not prop_id or not tous_partants:
        return 0
    for p in tous_partants:
        if dict_id(p.get("proprietaire")) == prop_id:
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                return 3
    return 0

def score_deferre(cheval):
    deferre = str(cheval.get("deferre") or cheval.get("incident") or "").upper()
    if "DEFER" in deferre or deferre in ["D", "DA", "DP", "DAP"]:
        return 4
    return 0

def score_jockey(cheval, tous_partants):
    jockey_id = (
        dict_id(cheval.get("jockey"))
        or dict_id(cheval.get("driver"))
    )
    if not jockey_id or not tous_partants:
        return 0
    victoires = 0
    montes    = 0
    for p in tous_partants:
        j = (
            dict_id(p.get("jockey"))
            or dict_id(p.get("driver"))
        )
        if j == jockey_id:
            montes += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                victoires += 1
    if montes >= 3:
        return round(clamp((victoires / montes) * 10, 0, 6), 1)
    return 0

def score_poids(cheval, disc):
    poids = safe_float(cheval.get("handicapPoids") or cheval.get("poidsConditionMonte"))
    if not poids:
        return 0
    ref = {"PLAT": 57.0, "OBSTACLE": 65.0, "TROT": 0}
    base = ref.get(disc, 0)
    if base == 0:
        return 0
    ecart = poids - base
    if ecart > 4:     return -5
    elif ecart > 2:   return -3
    elif ecart > 0:   return -1
    elif ecart < -2:  return 2
    return 0

def score_oeilleres(cheval):
    oeilleres = str(cheval.get("oeilleres") or cheval.get("equipement") or "").upper()
    if not oeilleres:
        return 0
    if "PREMIER" in oeilleres or "1ER" in oeilleres:
        return 5
    elif "OEIL" in oeilleres or "OEI" in oeilleres:
        return 2
    return 0

# ============= SCORE BASE =================
def score_base(cheval, course):
    music = parse_musique(cheval.get("musique"))
    forme = score_forme(music)
    reg   = score_regularite(music)
    prog  = score_progression(music)
    place = score_ratio_place(cheval)
    total = safe_int(cheval.get("nombreCourses", 0))
    vict  = safe_int(cheval.get("nombreVictoires", 0))
    tx_vict = clamp((vict / total) * 12, 0, 10) if total > 0 else 0
    mkt, cote = score_marche(cheval)
    score = forme + reg + prog + place + tx_vict + mkt
    return {
        "score": round(score, 1),
        "cote":  cote,
        "forme": round(forme, 1),
        "reg":   round(reg, 1),
        "prog":  round(prog, 1),
        "place": round(place, 1),
    }

# ============= DISCIPLINES ================
def score_trot(cheval, course):
    s = 0
    age = safe_float(cheval.get("age"))
    if age:
        if 5 <= age <= 8: s += 6
        elif age in [4, 9]: s += 3
    gains = safe_float(cheval.get("gainsCarriere", 0))
    if gains:
        s += clamp(gains / 200000 * 8, 0, 8)
    num = safe_int(cheval.get("numPmu", 0))
    if num:
        if num <= 3: s += 5
        elif num <= 6: s += 3
    s += score_recul_trot(cheval)
    return round(s, 1)

def score_plat(cheval, course):
    s = 0
    age = safe_float(cheval.get("age"))
    if age:
        if 3 <= age <= 5: s += 6
        elif age <= 7: s += 3
    num = safe_int(cheval.get("numPmu", 0))
    nb  = safe_int(course.get("nombreDeclaresPartants", 12)) or 12
    if nb > 0:
        s += clamp((nb - num) / 2, 0, 6)
    return round(s, 1)

def score_obstacle(cheval, course):
    s = 0
    total = safe_int(cheval.get("nombreCourses", 0))
    if total:
        s += clamp(math.log(total + 1) * 3, 0, 8)
    age = safe_float(cheval.get("age"))
    if age and 5 <= age <= 8:
        s += 5
    return round(s, 1)

# ============= VALUE & CONFIANCE ==========
def calc_value(score, cote):
    if not cote:
        return -100
    prob_modele = clamp(score / 100, 0.01, 0.95)
    prob_marche = clamp(1 / cote, 0.01, 0.95)
    return round((prob_modele - prob_marche) * 100, 1)

def calc_confiance(score, value, cote):
    conf = score * 0.9
    if value > 8:   conf += 8
    if value > 15:  conf += 5
    if cote:
        if cote > 20:   conf -= 10
        elif cote > 12: conf -= 5
        elif cote < 3:  conf += 3
    return round(clamp(conf, 0, 99), 1)

# ============= ANALYSE CHEVAL =============
def analyse_cheval(cheval, course, tous_partants):
    base = score_base(cheval, course)
    disc = detect_discipline(course)

    if disc == "TROT":
        b_disc = score_trot(cheval, course)
    elif disc == "PLAT":
        b_disc = score_plat(cheval, course)
    else:
        b_disc = score_obstacle(cheval, course)

    b_fraicheur  = score_fraicheur(cheval)
    b_allocation = score_allocation(course)
    b_partants   = score_nb_partants(course)
    b_gains_an   = score_gains_annee(cheval)
    b_sexe       = score_sexe(cheval)
    b_entraineur = score_entraineur(cheval, tous_partants)
    b_ecurie     = score_forme_ecurie(cheval, tous_partants)
    b_deferre    = score_deferre(cheval)
    b_jockey     = score_jockey(cheval, tous_partants)
    b_poids      = score_poids(cheval, disc)
    b_oeilleres  = score_oeilleres(cheval)

    total_bonus = (
        b_disc + b_fraicheur + b_allocation + b_partants +
        b_gains_an + b_sexe + b_entraineur + b_ecurie +
        b_deferre + b_jockey + b_poids + b_oeilleres
    )

    final = clamp(base["score"] + total_bonus, 0, 99)
    value = calc_value(final, base["cote"])
    conf  = calc_confiance(final, value, base["cote"])

    return {
        "discipline": disc,
        "score":      round(final, 1),
        "value":      value,
        "confiance":  conf,
        "cote":       base["cote"],
        "details": {
            "forme":      base["forme"],
            "reg":        base["reg"],
            "prog":       base["prog"],
            "place":      base["place"],
            "disc":       b_disc,
            "fraicheur":  b_fraicheur,
            "allocation": b_allocation,
            "partants":   b_partants,
            "gains_an":   b_gains_an,
            "sexe":       b_sexe,
            "entraineur": b_entraineur,
            "ecurie":     b_ecurie,
            "deferre":    b_deferre,
            "jockey":     b_jockey,
            "poids":      b_poids,
            "oeilleres":  b_oeilleres,
        }
    }

# =============== STREAMLIT UI =============
st.set_page_config(
    page_title="🏇 Benter V2 PMU",
    page_icon="🏇",
    layout="centered"
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .card {
        background: #161b27;
        border: 1px solid #1e2535;
        border-radius: 14px;
        padding: 16px 18px;
        margin-bottom: 14px;
    }
    .horse-name { font-size: 1.25rem; font-weight: 800; color: #ffffff; }
    .sub { color: #6b7280; font-size: 0.82rem; margin-top: 3px; }
    .badge-fort  { background:#3b1a1a; color:#f87171; padding:3px 10px; border-radius:6px; font-size:0.78rem; font-weight:700; display:inline-block; margin-top:6px; }
    .badge-value { background:#2d2a0f; color:#fbbf24; padding:3px 10px; border-radius:6px; font-size:0.78rem; font-weight:700; display:inline-block; margin-top:6px; }
    .detail-grid { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
    .detail-pill { background:#0f1117; border-radius:6px; padding:3px 8px; font-size:0.73rem; color:#9ca3af; }
    .detail-pill.pos { color:#4ade80; }
    .detail-pill.neg { color:#f87171; }
</style>
""", unsafe_allow_html=True)

st.title("🏇 Benter V2 — Sélections PMU")
st.caption(datetime.now().strftime("%A %d %B %Y"))

with st.sidebar:
    st.header("⚙️ Filtres")
    min_score = st.slider("Score minimum",     0, 99, MIN_SCORE)
    min_conf  = st.slider("Confiance minimum", 0, 99, MIN_CONF)
    min_value = st.slider("Value minimum",    -20, 30, MIN_VALUE)
    max_sel   = st.slider("Sélections max",    1, 10, MAX_SELECTIONS)
    st.divider()
    st.caption("**Critères actifs (17)**")
    st.caption("Musique · Régularité · Progression · Ratio placé · Taux victoires · Cote marché")
    st.caption("Fraîcheur · Allocation · Nb partants · Gains année · Sexe")
    st.caption("Entraîneur · Forme écurie · Déferré · Jockey · Poids · Œillères")
    st.caption("+ Recul handicap (trot) · Âge · Corde (plat) · Expérience (obstacle)")

if st.button("🔍 Analyser les courses du jour", use_container_width=True, type="primary"):

    with st.spinner("Chargement du programme PMU…"):
        try:
            data     = pmu_get(f"/{today()}/reunions")
            reunions = (data.get("programme") or {}).get("reunions") or []
        except Exception as e:
            st.error(f"Impossible de charger le programme PMU : {e}")
            st.stop()

    courses = []
    for r in reunions:
        for c in (r.get("courses") or []):
            courses.append((r, c))

    st.info(f"📋 {len(reunions)} réunions · {len(courses)} courses détectées")

    candidats = []
    erreurs_api     = []
    erreurs_analyse = []
    bar = st.progress(0, text="Analyse des partants…")

    for i, (r, c) in enumerate(courses):
        bar.progress((i + 1) / len(courses), text=f"Course {i+1}/{len(courses)}…")
        num_r = r.get("numOfficiel") or r.get("numOrdre") or r.get("numeroReunion") or r.get("num")
        num_c = c.get("numOfficiel") or c.get("numOrdre") or c.get("numCourse") or c.get("num")
        if not num_r or not num_c:
            continue
        try:
            pdata    = pmu_get(f"/{today()}/R{num_r}/C{num_c}/participants")
            partants = pdata.get("participants") or []
        except Exception as e:
            erreurs_api.append(f"R{num_r}C{num_c} — API: {e}")
            continue
        for ch in partants:
            try:
                a = analyse_cheval(ch, c, partants)
                a.update({
                    "nom":    ch.get("nom", "?"),
                    "num":    ch.get("numPmu"),
                    "course": f"R{num_r}C{num_c}",
                    "hippo":  ((r.get("hippodrome") or {}).get("nom") or "?"),
                })
                candidats.append(a)
            except Exception as e:
                erreurs_analyse.append(f"{ch.get('nom','?')} ({f'R{num_r}C{num_c}'}) — {type(e).__name__}: {e}")

    bar.empty()

    if erreurs_api or erreurs_analyse:
        with st.expander(f"⚠️ Diagnostics ({len(erreurs_api)} erreurs API · {len(erreurs_analyse)} erreurs analyse)"):
            if erreurs_api:
                st.caption("**Erreurs API (participants non chargés) :**")
                for e in erreurs_api[:5]:
                    st.caption(f"• {e}")
            if erreurs_analyse:
                st.caption("**Erreurs analyse (chevaux ignorés) :**")
                for e in erreurs_analyse[:5]:
                    st.caption(f"• {e}")

    gardes = [
        c for c in candidats
        if c["score"] >= min_score
        and c["confiance"] >= min_conf
        and c["value"] >= min_value
    ]
    gardes.sort(key=lambda x: (x["confiance"], x["value"]), reverse=True)
    gardes = gardes[:max_sel]

    st.divider()

    if not gardes:
        st.warning("❌ Aucun pari aujourd'hui — aucun cheval ne passe les filtres.")
        top3 = sorted(candidats, key=lambda x: x["score"], reverse=True)[:3]
        if top3:
            st.caption("Top 3 des meilleurs scores aujourd'hui (hors filtres) :")
            for c in top3:
                st.caption(f"• N°{c['num']} {c['nom']} ({c['course']}) — Score {c['score']} · Conf {c['confiance']}% · Value {c['value']}%")
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 10
        st.success(f"🔥 {len(gardes)} sélection(s) du jour")

        def pill(label, val):
            if val is None or val == 0:
                return f'<span class="detail-pill">{label} 0</span>'
            cls = "pos" if val > 0 else "neg"
            sign = "+" if val > 0 else ""
            return f'<span class="detail-pill {cls}">{label} {sign}{val}</span>'

        for i, c in enumerate(gardes):
            is_fort = c["confiance"] >= 90
            badge   = '<span class="badge-fort">🔥 PARI FORT</span>' if is_fort else '<span class="badge-value">⚡ VALUE</span>'
            hippo   = f" · {c['hippo']}" if c.get("hippo") and c["hippo"] != "?" else ""
            d       = c.get("details", {})

            pills = "".join([
                pill("Forme",     d.get("forme")),
                pill("Rég.",      d.get("reg")),
                pill("Prog.",     d.get("prog")),
                pill("Placé",     d.get("place")),
                pill("Disc.",     d.get("disc")),
                pill("Fraîcheur", d.get("fraicheur")),
                pill("Alloc.",    d.get("allocation")),
                pill("Partants",  d.get("partants")),
                pill("Gains/an",  d.get("gains_an")),
                pill("Sexe",      d.get("sexe")),
                pill("Trainer",   d.get("entraineur")),
                pill("Écurie",    d.get("ecurie")),
                pill("Déferré",   d.get("deferre")),
                pill("Jockey",    d.get("jockey")),
                pill("Poids",     d.get("poids")),
                pill("Œill.",     d.get("oeilleres")),
            ])

            st.markdown(f"""
            <div class="card">
                <div class="horse-name">{medals[i]} N°{c['num']} {c['nom']}</div>
                <div class="sub">{c['course']} · {c['discipline']}{hippo}</div>
                {badge}
                <div class="detail-grid">{pills}</div>
            </div>
            """, unsafe_allow_html=True)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Score",     c["score"])
            col2.metric("Confiance", f"{c['confiance']}%")
            col3.metric("Value",     f"+{c['value']}%" if c['value'] >= 0 else f"{c['value']}%")
            col4.metric("Cote",      f"{c['cote']}×" if c['cote'] else "—")
            st.markdown("---")

    st.caption(f"Filtres : score ≥ {min_score} · confiance ≥ {min_conf}% · value ≥ {min_value}%")
    st.caption(f"{len(candidats)} chevaux analysés au total")
