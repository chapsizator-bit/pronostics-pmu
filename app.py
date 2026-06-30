import urllib.request
import json
import math
import re
import os
import base64
import time
from datetime import datetime, date
import streamlit as st

# ================= CONFIG =================
MAX_SELECTIONS = 3
MIN_PROB_EDGE  = 0.03   # avantage minimum sur le marché (3 points de proba)
MIN_CONF       = 60
TIMEOUT        = 12
BASE_URL       = "https://online.turfinfo.api.pmu.fr/rest/client/1/programme"

# ============== JOURNAL ===================
JOURNAL_RAW  = "https://raw.githubusercontent.com/chapsizator-bit/pronostics-pmu/main/journal.json"
JOURNAL_API  = "https://api.github.com/repos/chapsizator-bit/pronostics-pmu/contents/journal.json"

@st.cache_data(ttl=30)
def load_journal():
    """Charge journal.json depuis GitHub (lecture publique, pas de token)."""
    try:
        req = urllib.request.Request(
            JOURNAL_RAW + "?t=" + str(int(time.time())),
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return []

def save_journal(entries):
    """Écrit journal.json dans le repo GitHub via l'API (nécessite le token)."""
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        return False, "Token GitHub introuvable"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    # Récupérer le SHA actuel du fichier
    sha = None
    try:
        req = urllib.request.Request(JOURNAL_API, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            sha = json.loads(r.read()).get("sha")
    except Exception:
        pass  # fichier inexistant → sha None, GitHub créera le fichier
    content_b64 = base64.b64encode(
        json.dumps(entries, ensure_ascii=False, indent=2).encode()
    ).decode()
    payload = {"message": "journal: résultat ajouté", "content": content_b64}
    if sha:
        payload["sha"] = sha
    try:
        req2 = urllib.request.Request(
            JOURNAL_API,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="PUT"
        )
        with urllib.request.urlopen(req2, timeout=10) as r:
            return r.status in (200, 201), ""
    except Exception as e:
        return False, str(e)

def journal_stats(entries):
    """Calcule taux de réussite et ROI sur une liste d'entrées."""
    if not entries:
        return {}
    total  = len(entries)
    wins   = sum(1 for e in entries if e.get("resultat") == "gagné")
    places = sum(1 for e in entries if e.get("resultat") == "placé")
    pertes = total - wins - places
    # ROI : 1 unité misée par cheval
    #  gagné  → bénéfice = cote - 1
    #  placé  → bénéfice = 0  (on récupère la mise)
    #  perdu  → bénéfice = -1
    roi_abs = sum(
        (e.get("cote", 1) - 1) if e.get("resultat") == "gagné"
        else (0 if e.get("resultat") == "placé" else -1)
        for e in entries
    )
    return {
        "total": total,
        "wins": wins,
        "places": places,
        "pertes": pertes,
        "taux_vict": round(wins / total * 100, 1),
        "taux_place": round((wins + places) / total * 100, 1),
        "roi": round(roi_abs / total * 100, 1),
        "roi_abs": round(roi_abs, 2),
    }

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
    return v.get("id") if isinstance(v, dict) else None

def safe_float(v):
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except Exception:
        return None

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def detect_discipline(course):
    if not isinstance(course, dict): return "PLAT"
    txt = str(course.get("discipline", "") or course.get("specialite", "") or "").upper()
    if "ATTEL" in txt or "MONT" in txt or "TROT" in txt:
        return "TROT"
    if "HAIE" in txt or "STEEPLE" in txt or "CROSS" in txt:
        return "OBSTACLE"
    return "PLAT"

# =============== MUSIQUE ==================
def parse_musique(m):
    """
    Parse la musique d'un cheval selon les règles officielles :
    - Xy où X = place, y = discipline (a=attelé, m=monté, p=plat, h=haies, s=steeple, c=cross)
    - X=0 : non classé dans les 10 premiers (PAS une vraie 10e place)
    - T : tombé
    - A : arrêté (à ne pas confondre avec "abandon" générique)
    - Ret : rétrogradé
    - D : disqualifié (allure irrégulière, trot uniquement)
    - (XX) : changement d'année — ignoré pour le parsing des positions
    """
    if not m:
        return []
    m = str(m)
    # Retirer les parenthèses d'année type (19), (24)...
    m = re.sub(r"\(\d{2}\)", "", m)
    m = re.sub(r"\s", "", m)

    vals = []
    i = 0
    n = len(m)
    while i < n:
        c = m[i]

        # "Ret" — rétrogradé (3 caractères)
        if m[i:i+3].lower() == "ret":
            vals.append(("ret", None))
            i += 3
            # Sauter la lettre de discipline qui suit si présente
            if i < n and m[i].isalpha():
                i += 1
            continue

        # "T" — tombé
        if c.upper() == "T":
            vals.append(("tombe", None))
            i += 1
            if i < n and m[i].isalpha():
                i += 1
            continue

        # "A" — arrêté
        if c.upper() == "A":
            vals.append(("arrete", None))
            i += 1
            if i < n and m[i].isalpha():
                i += 1
            continue

        # "D" — disqualifié
        if c.upper() == "D":
            vals.append(("disq", None))
            i += 1
            if i < n and m[i].isalpha():
                i += 1
            continue

        # Chiffre — position (0 = non classé, 1-9 = place, parfois 2 chiffres pour 10+)
        if c.isdigit():
            num = int(c)
            if i + 1 < n and m[i+1].isdigit() and c != "0":
                num = int(c + m[i+1])
                i += 1
            if num == 0:
                vals.append(("non_classe", None))
            else:
                vals.append(("pos", num))
            i += 1
            # Sauter la lettre de discipline qui suit
            if i < n and m[i].isalpha():
                i += 1
            continue

        # Caractère non reconnu — on avance sans planter
        i += 1

    return vals[:10]

# ============= SOFTMAX ====================
def softmax(scores):
    """Convertit des logits bruts en probabilités qui somment à 1."""
    if not scores:
        return []
    max_s = max(scores)
    exps  = [math.exp(clamp(s - max_s, -30, 30)) for s in scores]
    total = sum(exps)
    return [e / total for e in exps] if total > 0 else [1 / len(scores)] * len(scores)

# ============= FEATURES INDIVIDUELLES =====


# ═══════════════════════════════════════════════════════════════
# CORRECTIF — Niveau réel de la musique
# Problème : un 1er en Belgique à 8 000€ = même poids qu un 1er
# à 22 000€ en France. C est faux. On corrige avec 3 mécanismes.
# ═══════════════════════════════════════════════════════════════

def get_niveau_cheval(cheval):
    """
    Calcule le niveau moyen réel du cheval basé sur ses gains.
    Retourne le gain moyen par course = proxy de l allocation habituelle.
    """
    if not isinstance(cheval, dict): return None
    gains = safe_float(cheval.get("gainsCarriere") or 0) or 0
    nb    = safe_int(cheval.get("nombreCourses"), 0)
    if nb < 3 or gains <= 0: return None
    return gains / nb  # gain moyen par course

def feat_forme_ponderee_niveau(cheval, course):
    """
    CORRECTIF 1 — Forme pondérée par le niveau réel du cheval.
    Si le cheval a une belle musique MAIS un niveau de gains faible
    par rapport à l allocation de la course du jour → malus.
    Si le niveau de gains est élevé → la musique est confirmée.
    """
    if not isinstance(cheval, dict) or not isinstance(course, dict): return 0

    gain_moyen = get_niveau_cheval(cheval)
    if not gain_moyen: return 0

    alloc = safe_float(
        course.get("allocation") or course.get("montantPrix") or course.get("totalOffert")
    ) or 0
    if not alloc: return 0

    # Gain moyen par course vs allocation du jour
    # Si le cheval gagne en moyenne beaucoup moins que l enjeu du jour
    ratio = gain_moyen / (alloc * 0.25)  # 0.25 = part du gagnant estimée

    if ratio >= 1.5:   return 3   # Surclasse nettement = musique fiable
    elif ratio >= 1.0: return 1   # Niveau adéquat = musique correcte
    elif ratio >= 0.6: return 0   # Niveau légèrement inférieur = neutre
    elif ratio >= 0.3: return -3  # Musique gonflée par niveau faible
    else:              return -6  # Grosse différence de niveau = musique trompeuse

def feat_malus_courses_etrangeres(cheval):
    """
    CORRECTIF 2 — Malus pour musique gonflée par courses étrangères.
    Les courses en Belgique, Suède, Allemagne, etc. ont souvent
    des allocations plus faibles et des champs moins relevés.
    Proxy : pays de la dernière course si disponible dans l API.
    Sinon : on détecte via le champ paysOrigine ou pays.
    """
    if not isinstance(cheval, dict): return 0

    # Tentative de détection du pays
    pays = str(
        cheval.get("paysOrigine") or
        cheval.get("pays") or
        cheval.get("nationalite") or ""
    ).upper()

    if not pays: return 0

    # Pays dont les niveaux sont généralement plus faibles
    pays_faibles = ["BEL", "BELGIQUE", "SWE", "SUEDE", "GER", "ALL", "ALLEMAGNE",
                    "ITA", "ITALIE", "NOR", "NORVEGE", "DEN", "DANEMARK",
                    "FIN", "FINLANDE", "NET", "PAYS-BAS"]

    for p in pays_faibles:
        if p in pays:
            return -2  # Léger malus cheval étranger

    return 0

def feat_musique_gonfiee(cheval):
    """
    CORRECTIF 3 — Détection musique gonflée.
    Beaucoup de victoires MAIS gains faibles = champ faible.
    Indicateur : si taux victoire élevé (>25%) mais gain moyen
    très bas (<500€/course), la musique est trompeuse.
    """
    if not isinstance(cheval, dict): return 0

    total = safe_int(cheval.get("nombreCourses"), 0)
    vict  = safe_int(cheval.get("nombreVictoires"), 0)
    gains = safe_float(cheval.get("gainsCarriere") or 0) or 0

    if total < 5 or gains <= 0: return 0

    taux_vict  = vict / total
    gain_moyen = gains / total

    # Cheval qui gagne souvent mais dans des courses à petit budget
    if taux_vict > 0.25 and gain_moyen < 400:
        return -5  # Musique très gonflée
    elif taux_vict > 0.20 and gain_moyen < 600:
        return -3  # Musique gonflée
    elif taux_vict > 0.15 and gain_moyen < 800:
        return -1  # Légèrement gonflée

    # À l inverse : taux victoire élevé + bons gains = musique confirmée
    if taux_vict > 0.20 and gain_moyen > 2000:
        return 3
    elif taux_vict > 0.15 and gain_moyen > 1500:
        return 1

    return 0

def feat_classe_affinee(cheval, course, tous_partants):
    """
    CORRECTIF 4 — Classe affinée : compare le niveau du cheval
    non seulement à l allocation mais aussi au niveau du champ.
    Un cheval de niveau 15 000€ dans un champ de 8 000€ = très fort.
    Un cheval de niveau 8 000€ dans un champ de 22 000€ = hors classe.
    """
    if not isinstance(cheval, dict) or not isinstance(course, dict): return 0

    alloc = safe_float(
        course.get("allocation") or course.get("montantPrix") or course.get("totalOffert")
    ) or 0
    if not alloc: return 0

    gain_moyen = get_niveau_cheval(cheval)
    if not gain_moyen: return 0

    # Niveau moyen du champ
    niveaux_champ = []
    for p in (tous_partants or []):
        if not isinstance(p, dict): continue
        gm = get_niveau_cheval(p)
        if gm: niveaux_champ.append(gm)

    if not niveaux_champ: return 0
    niveau_moyen_champ = sum(niveaux_champ) / len(niveaux_champ)

    if niveau_moyen_champ <= 0: return 0

    # Ratio : niveau du cheval vs niveau moyen du champ
    ratio_champ = gain_moyen / niveau_moyen_champ

    if ratio_champ >= 2.0:    return 8   # Surclasse nettement le champ
    elif ratio_champ >= 1.5:  return 5
    elif ratio_champ >= 1.2:  return 3
    elif ratio_champ >= 0.9:  return 0   # Dans la moyenne
    elif ratio_champ >= 0.6:  return -3  # En-dessous du niveau du champ
    elif ratio_champ >= 0.4:  return -5
    else:                     return -7  # Très en-dessous

def feat_forme(music):
    """Pondération exponentielle des positions récentes."""
    score = 0
    for i, (typ, pos) in enumerate(music):
        if typ != "pos":
            continue
        w = math.exp(-0.35 * i)
        if pos == 1:      score += 12 * w
        elif pos <= 3:    score += 8 * w
        elif pos <= 5:    score += 4 * w
        elif pos <= 8:    score += 1.5 * w
    return clamp(score, 0, 20)

def feat_regularite(music):
    vals = [p for t, p in music if t == "pos"]
    if len(vals) < 4:
        return 0
    moy   = sum(vals) / len(vals)
    sigma = math.sqrt(sum((x - moy)**2 for x in vals) / len(vals))
    return clamp(8 - sigma, 0, 8)

def feat_progression(music):
    vals = [p for t, p in music if t == "pos"][:5]
    if len(vals) < 3:
        return 0
    n = len(vals)
    x_moy = (n - 1) / 2
    y_moy = sum(vals) / n
    num   = sum((i - x_moy) * (vals[i] - y_moy) for i in range(n))
    den   = sum((i - x_moy)**2 for i in range(n))
    if den == 0:
        return 0
    pente = num / den
    if pente < -0.5:   return 5
    elif pente < 0:    return 2
    elif pente > 0.5:  return -2
    return 0

def feat_ratio_place(cheval):
    total  = safe_int(cheval.get("nombreCourses", 0))
    places = safe_int(cheval.get("nombrePlaces", 0)) or safe_int(cheval.get("nombrePlace", 0))
    if total < 3:
        return 0
    return clamp((places / total) * 8, 0, 8)

def feat_taux_victoires(cheval):
    total = safe_int(cheval.get("nombreCourses", 0))
    vict  = safe_int(cheval.get("nombreVictoires", 0))
    if total == 0:
        return 0
    return clamp((vict / total) * 12, 0, 10)

def feat_fraicheur(cheval):
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
        if jours < 0:     return 0
        if 14 <= jours <= 35:  return 4
        elif 7 <= jours < 14:  return 2
        elif 35 < jours <= 60: return 1
        elif jours > 90:       return -3
        elif jours > 60:       return -1
    except Exception:
        pass
    return 0

def feat_nb_partants(course):
    nb = safe_int(course.get("nombreDeclaresPartants", 0))
    if nb == 0:
        return 0
    if nb <= 6:    return 3
    elif nb <= 9:  return 1
    elif nb <= 12: return 0
    elif nb <= 15: return -1
    else:          return -3

def feat_gains_annee(cheval):
    gains = safe_float(cheval.get("gainsAnneeEnCours") or cheval.get("gainsCourseAnneeCourante") or 0)
    if not gains:
        return 0
    return clamp(gains / 30000 * 5, 0, 5)

def feat_sexe(cheval):
    sexe = str(cheval.get("sexe") or cheval.get("indicateurSexe") or "").upper()
    if "HONG" in sexe or sexe == "H":
        return 2
    return 0

def feat_entraineur(cheval, tous_partants):
    ent_id = dict_id(cheval.get("entraineur"))
    if not ent_id or not tous_partants:
        return 0
    victoires = sorties = 0
    for p in tous_partants:
        if dict_id(p.get("entraineur")) == ent_id:
            sorties += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                victoires += 1
    if sorties >= 3:
        return round(clamp((victoires / sorties) * 8, 0, 5), 1)
    return 0

def feat_forme_ecurie(cheval, tous_partants):
    prop_id = dict_id(cheval.get("proprietaire"))
    if not prop_id or not tous_partants:
        return 0
    for p in tous_partants:
        if dict_id(p.get("proprietaire")) == prop_id:
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                return 3
    return 0

def feat_deferre(cheval, disc):
    """
    Déferré = retrait des fers.
    DAP / 4 PATTES = 4 pattes → signal fort (+4)
    DA / ANT       = antérieurs seulement → signal moyen (+2)
    DP / POST      = postérieurs seulement → signal faible (+1, courant en trot)
    En trot, le DP est très fréquent et presque sans valeur prédictive.
    """
    raw = str(cheval.get("deferre") or cheval.get("incident") or "").upper()
    if not raw or raw in ["", "NONE", "0", "NON"]:
        return 0
    # 4 pattes — signal le plus fort
    if "DAP" in raw or "4 PATTE" in raw or "QUATRE" in raw:
        return 4
    # Antérieurs seulement
    if "DA" in raw or "ANT" in raw:
        return 2
    # Postérieurs seulement — très courant en trot, signal faible
    if "DP" in raw or "POST" in raw:
        return 1 if disc != "TROT" else 0
    # Mention générique sans précision
    if "DEFER" in raw or raw == "D":
        return 2
    return 0

def feat_jockey(cheval, tous_partants):
    jockey_id = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    if not jockey_id or not tous_partants:
        return 0
    victoires = montes = 0
    for p in tous_partants:
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        if j == jockey_id:
            montes += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                victoires += 1
    if montes >= 3:
        return round(clamp((victoires / montes) * 10, 0, 6), 1)
    return 0

def feat_poids(cheval, disc):
    poids = safe_float(cheval.get("handicapPoids") or cheval.get("poidsConditionMonte"))
    if not poids:
        return 0
    ref  = {"PLAT": 57.0, "OBSTACLE": 65.0, "TROT": 0}
    base = ref.get(disc, 0)
    if base == 0:
        return 0
    ecart = poids - base
    if ecart > 4:    return -5
    elif ecart > 2:  return -3
    elif ecart > 0:  return -1
    elif ecart < -2: return 2
    return 0

def feat_oeilleres(cheval, disc):
    """
    En trot, les œillères sont standard et quasi-universelles — pas de signal.
    En plat/obstacle, les œillères à la première (1ère fois) sont un signal fort.
    """
    if disc == "TROT":
        return 0
    oeilleres = str(cheval.get("oeilleres") or cheval.get("equipement") or "").upper()
    if "PREMIER" in oeilleres or "1ER" in oeilleres:
        return 5
    elif "OEIL" in oeilleres or "OEI" in oeilleres:
        return 2
    return 0

def feat_recul_trot(cheval):
    recul = safe_float(cheval.get("handicapDistance") or cheval.get("distanceHandicap") or 0)
    if not recul or recul <= 0:
        return 0
    if recul <= 25:   return -1
    elif recul <= 50: return -2
    elif recul <= 75: return -4
    else:             return -6

def feat_age_disc(cheval, disc):
    age = safe_float(cheval.get("age"))
    if not age:
        return 0
    if disc == "TROT":
        if 5 <= age <= 8:   return 6
        elif age in [4, 9]: return 3
    elif disc == "PLAT":
        if 3 <= age <= 5:   return 6
        elif age <= 7:      return 3
    elif disc == "OBSTACLE":
        if 5 <= age <= 8:   return 5
    return 0

def feat_corde_plat(cheval, course, disc):
    if disc != "PLAT":
        return 0
    num = safe_int(cheval.get("numPmu", 0))
    nb  = safe_int(course.get("nombreDeclaresPartants", 12)) or 12
    if num and nb > 0:
        return round(clamp((nb - num) / 2, 0, 6), 1)
    return 0

def feat_experience_obs(cheval, disc):
    if disc != "OBSTACLE":
        return 0
    total = safe_int(cheval.get("nombreCourses", 0))
    if total:
        return round(clamp(math.log(total + 1) * 3, 0, 8), 1)
    return 0

def feat_gains_carriere_trot(cheval, disc):
    if disc != "TROT":
        return 0
    gains = safe_float(cheval.get("gainsCarriere", 0))
    if gains:
        return round(clamp(gains / 200000 * 8, 0, 8), 1)
    return 0

# ============= NOUVEAUX CRITÈRES AVANCÉS ==

def feat_reduction_km(cheval, tous_partants, disc):
    """
    Réduction kilométrique normalisée dans le champ (trot uniquement).
    Toujours comparé aux autres chevaux de la même course.
    """
    if disc != "TROT":
        return 0, None
    rk = safe_float(cheval.get("reductionKilometrique") or cheval.get("rkActuel"))
    if not rk:
        return 0, None
    rks = [safe_float(p.get("reductionKilometrique") or p.get("rkActuel"))
           for p in tous_partants]
    rks = [r for r in rks if r]
    if len(rks) < 2:
        return 0, rk
    min_rk, max_rk = min(rks), max(rks)
    if max_rk == min_rk:
        return 0, rk
    # Plus basse RK = plus rapide = meilleur
    rang = (max_rk - rk) / (max_rk - min_rk)
    return round(clamp(rang * 12, 0, 12), 1), rk

def feat_mouvement_cote(cheval):
    """
    Détecte les chutes de cote significatives = argent des insiders.
    Signal fort : cote qui baisse de >30% entre ouverture et maintenant.
    """
    cote_init = safe_float(cheval.get("coteInitiale"))
    rapport   = cheval.get("dernierRapportDirect") or {}
    if isinstance(rapport, dict):
        cote_fin = safe_float(rapport.get("rapport"))
    else:
        cote_fin = None
    if not cote_init or not cote_fin or cote_init <= 0:
        return 0, None
    chute = (cote_init - cote_fin) / cote_init
    if chute > 0.30:    return 8, chute
    elif chute > 0.15:  return 4, chute
    elif chute > 0.05:  return 2, chute
    elif chute < -0.25: return -4, chute
    elif chute < -0.10: return -2, chute
    return 0, chute

def feat_classe(cheval, course):
    """
    Indice de classe : le cheval descend-il en classe aujourd'hui ?
    Descente de classe = signal fort (le cheval bat du moins fort).
    Montée de classe = signal négatif.
    """
    alloc = safe_float(
        course.get("allocation") or course.get("montantPrix") or course.get("totalOffert")
    )
    if not alloc:
        return 0
    gains    = safe_float(cheval.get("gainsCarriere", 0)) or 0
    nb_cours = safe_int(cheval.get("nombreCourses", 0))
    if nb_cours < 3 or not gains:
        return 0
    # Gain moyen par course → proxy du niveau habituel
    # On estime que le 1er touche ~25% de l'allocation
    gain_moyen    = gains / nb_cours
    niveau_estim  = gain_moyen / 0.25
    if niveau_estim <= 0:
        return 0
    ratio = alloc / niveau_estim
    if ratio < 0.4:    return 7    # grosse descente de classe
    elif ratio < 0.65: return 4
    elif ratio < 0.85: return 2
    elif ratio > 2.5:  return -5   # grosse montée de classe
    elif ratio > 1.5:  return -3
    elif ratio > 1.15: return -1
    return 0

def feat_performance_relative(cheval, tous_partants):
    """
    Quand ce cheval a gagné, était-il favori ou outsider ?
    Un cheval qui gagne en tant qu'outsider = vraie valeur.
    Proxy : comparer son taux de victoires vs son rang de cote dans le champ.
    """
    taux_vict = 0
    total = safe_int(cheval.get("nombreCourses", 0))
    vict  = safe_int(cheval.get("nombreVictoires", 0))
    if total >= 5:
        taux_vict = vict / total
    cote = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or \
           safe_float(cheval.get("coteInitiale"))
    if not cote or not tous_partants:
        return 0
    # Rang dans le champ (1 = favori)
    cotes_champ = []
    for p in tous_partants:
        c = safe_float((p.get("dernierRapportDirect") or {}).get("rapport")) or \
            safe_float(p.get("coteInitiale"))
        if c:
            cotes_champ.append(c)
    if not cotes_champ:
        return 0
    cotes_champ.sort()
    rang = cotes_champ.index(min(cotes_champ, key=lambda x: abs(x - cote))) + 1
    ratio_rang = rang / len(cotes_champ)  # 0 = favori, 1 = outsider
    # Bonus si taux de victoires élevé mais n'est pas favori = value
    if taux_vict > 0.25 and ratio_rang > 0.5:
        return 4
    elif taux_vict > 0.15 and ratio_rang > 0.6:
        return 2
    return 0

# ============= NOUVEAUX CRITÈRES (lot 2) ==

def feat_distance_optimale(cheval, course):
    """
    Compare la distance d'aujourd'hui à la distance optimale estimée du cheval.
    Proxy : on regarde les champs gainsParDistance, distancePrefere, ou on
    estime via le ratio victoires/courses à des catégories de distance.
    """
    dist_auj = safe_float(
        course.get("distance") or course.get("distanceParcourue") or course.get("longueurPiste")
    )
    if not dist_auj:
        return 0

    # Champ direct si l'API le fournit
    dist_pref = safe_float(cheval.get("distancePrefere") or cheval.get("distanceOptimale"))
    if dist_pref:
        ecart = abs(dist_auj - dist_pref) / dist_pref
        if ecart < 0.08:   return 5
        elif ecart < 0.20: return 2
        elif ecart > 0.40: return -4
        return 0

    # Proxy : gains par catégorie de distance (champ dict de l'API)
    gpd = cheval.get("gainsParDistance") or cheval.get("gainsParTypeDistance")
    if isinstance(gpd, dict) and gpd:
        # Trouver la catégorie avec les meilleurs gains
        meilleure = max(gpd, key=lambda k: safe_float(gpd[k]) or 0)
        # Correspondance approximative catégorie → mètres
        cat_to_m = {
            "COURT": 1400, "COURTE": 1400,
            "MOYEN": 1800, "MOYENNE": 1800,
            "LONG": 2400,  "LONGUE": 2400,
            "TRES_LONG": 3000,
        }
        dist_opt = cat_to_m.get(str(meilleure).upper())
        if dist_opt:
            ecart = abs(dist_auj - dist_opt) / dist_opt
            if ecart < 0.12:   return 4
            elif ecart < 0.25: return 1
            elif ecart > 0.40: return -3
    return 0


def feat_historique_piste(cheval, hippo_code):
    """
    Bonus si le cheval a déjà gagné ou placé sur cette piste.
    Cherche dans gainsParHippodrome, ou un flag nombreVictoiresPiste.
    """
    if not hippo_code:
        return 0

    # Champ dict gagné par hippodrome
    gph = cheval.get("gainsParHippodrome") or cheval.get("performancesParHippodrome")
    if isinstance(gph, dict):
        code = str(hippo_code).upper()
        # Chercher une correspondance (code ou nom partiel)
        for k, v in gph.items():
            if str(k).upper() == code or code in str(k).upper():
                gains = safe_float(v) or 0
                if gains > 50000: return 5
                elif gains > 10000: return 3
                elif gains > 0:   return 1
        return -1   # jamais couru ou rien gagné ici

    # Champ simple : nombre de victoires sur la piste
    vict_piste = safe_int(cheval.get("nombreVictoiresPiste") or cheval.get("victoiresHippodrome"), 0)
    if vict_piste >= 2: return 4
    elif vict_piste == 1: return 2

    return 0


def feat_terrain(cheval, course, disc):
    """
    État de la piste (bon/souple/lourd) comparé aux préférences du cheval.
    Non applicable au trot (les pistes en trot sont standardisées).
    """
    if disc == "TROT":
        return 0

    terrain_auj = str(
        course.get("terrain") or course.get("etatPiste") or
        course.get("terrainGeneral") or course.get("nature") or ""
    ).upper()
    if not terrain_auj:
        return 0

    # Préférence du cheval
    terrain_pref = str(
        cheval.get("terrainPrefere") or cheval.get("typeTerrain") or ""
    ).upper()

    # Si l'API donne directement la préférence
    if terrain_pref:
        # Correspondances
        lourd   = any(x in terrain_pref for x in ["LOURD", "MOU", "SOFT", "HEAVY"])
        souple  = any(x in terrain_pref for x in ["SOUPLE", "GOOD_SOFT"])
        bon     = any(x in terrain_pref for x in ["BON", "GOOD", "FIRM"])

        lourd_auj  = any(x in terrain_auj for x in ["LOURD", "HEAVY", "MOU"])
        souple_auj = any(x in terrain_auj for x in ["SOUPLE", "SOFT"])
        bon_auj    = any(x in terrain_auj for x in ["BON", "GOOD", "FIRM"])

        if (lourd and lourd_auj) or (souple and souple_auj) or (bon and bon_auj):
            return 4    # terrain idéal
        if (lourd and bon_auj) or (bon and lourd_auj):
            return -4   # terrain opposé à la préférence
        return 0

    # Proxy : gains par type de terrain
    gt = cheval.get("gainsParNaturePiste") or cheval.get("gainsParTerrain")
    if isinstance(gt, dict) and gt:
        meilleur = max(gt, key=lambda k: safe_float(gt[k]) or 0)
        mk = str(meilleur).upper()
        lourd_pref = any(x in mk for x in ["LOURD", "HEAVY"])
        bon_pref   = any(x in mk for x in ["BON", "GOOD", "FIRM"])
        lourd_auj  = any(x in terrain_auj for x in ["LOURD", "HEAVY"])
        bon_auj    = any(x in terrain_auj for x in ["BON", "GOOD", "FIRM"])
        if (lourd_pref and lourd_auj) or (bon_pref and bon_auj):
            return 3
        if (lourd_pref and bon_auj) or (bon_pref and lourd_auj):
            return -3
    return 0


def feat_combo_connexion(cheval, tous_partants):
    """
    Synergies jockey+entraîneur : le duo a-t-il un bon taux de réussite ensemble ?
    Calcul sur les partants de toutes les courses du jour qui partagent ce duo.
    """
    jock_id  = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    train_id = dict_id(cheval.get("entraineur"))
    if not jock_id or not train_id or not tous_partants:
        return 0

    wins   = 0
    sorties = 0
    for p in tous_partants:
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        t = dict_id(p.get("entraineur"))
        if j == jock_id and t == train_id:
            sorties += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1):
                wins += 1

    if sorties < 3:
        return 0
    taux = wins / sorties
    if taux > 0.30:   return 6
    elif taux > 0.20: return 3
    elif taux > 0.10: return 1
    elif taux < 0.05 and sorties >= 5:
        return -2   # duo qui ne gagne jamais ensemble
    return 0


def feat_regularite_conditions(music, cheval):
    """
    Cohérence des performances selon les conditions.
    Idée : séparer les 5 dernières courses entre 'bonnes conditions' (cote ≤ 5)
    et 'mauvaises conditions' (cote > 5) et comparer les positions.
    Sans historique de cote par course, on utilise un proxy de régularité :
    l'écart entre les positions hautes et basses — un cheval 'tout ou rien'
    est moins fiable qu'un cheval régulier.
    """
    positions = [p for t, p in music if t == "pos"]
    if len(positions) < 5:
        return 0

    top_3  = sum(1 for p in positions[:5] if p <= 3)
    hors_5 = sum(1 for p in positions[:5] if p > 5)

    # Cheval régulier : souvent dans les 3, rarement hors top 5
    if top_3 >= 3 and hors_5 <= 1:
        return 4
    elif top_3 >= 2 and hors_5 <= 2:
        return 2
    # Cheval 'tout ou rien' : alterne victoires et fond de peloton
    elif top_3 >= 2 and hors_5 >= 3:
        return -2
    elif top_3 == 0 and hors_5 >= 4:
        return -3
    return 0


# ============= LOGIT BRUT PAR CHEVAL ======



# ═══════════════════════════════════════════════════════════════
# BLOC C — Historique temporel
# ═══════════════════════════════════════════════════════════════

def feat_victoires_par_mois(cheval):
    """
    C1 — Cheval saisonnier : performe mieux à certaines périodes.
    Proxy : compare le mois actuel aux mois de ses victoires récentes.
    En l absence d historique détaillé, on utilise la date de dernière course.
    """
    if not isinstance(cheval, dict): return 0
    mois_actuel = datetime.now().month
    # Saison "chaude" (mai-septembre) vs "froide" (octobre-avril)
    saison_chaude = mois_actuel in [5, 6, 7, 8, 9]

    # Proxy via gains par saison
    gains_saison = safe_float(cheval.get("gainsSaison") or cheval.get("gainsAnneeEnCours") or 0) or 0
    gains_carriere = safe_float(cheval.get("gainsCarriere") or 0) or 0
    nb_courses = safe_int(cheval.get("nombreCourses"), 0)

    if gains_carriere > 0 and nb_courses > 5:
        # Si gains saison >> moyenne carrière = cheval en forme cette saison
        gain_moyen_carriere = gains_carriere / nb_courses
        if gains_saison > gain_moyen_carriere * 2:
            return 3   # Très en forme cette saison
        elif gains_saison > gain_moyen_carriere:
            return 1   # En forme
        elif gains_saison == 0 and nb_courses > 10:
            return -2  # Pas encore gagné cette saison

    return 0

def feat_serie_podiums_sans_victoire(cheval):
    """
    C2 — Cheval qui place régulièrement mais ne gagne jamais.
    Ces chevaux sont souvent sous-cotés car leur taux de victoire
    est faible mais leur régularité est réelle.
    Signal : 3+ podiums dans les 6 dernières sans victoire.
    """
    if not isinstance(cheval, dict): return 0
    music = parse_musique(cheval.get("musique", ""))
    pos = [p for t, p in music if t == "pos"]
    if len(pos) < 4: return 0

    rec6 = pos[:6]
    podiums = sum(1 for p in rec6 if 2 <= p <= 3)
    victoires = sum(1 for p in rec6 if p == 1)
    hors5 = sum(1 for p in rec6 if p > 5)

    if victoires == 0 and podiums >= 3 and hors5 <= 1:
        return 4   # Régulier sans gagner = sous-coté
    elif victoires == 0 and podiums >= 2 and hors5 <= 2:
        return 2
    return 0

def feat_baisse_de_classe(cheval, course):
    """
    C3 — Cheval qui descend en niveau après des courses plus relevées.
    C est l un des meilleurs prédicteurs en handicapping américain.
    Un cheval habitué à 50k€ qui court un 20k€ devrait dominer.
    """
    if not isinstance(cheval, dict) or not isinstance(course, dict): return 0
    montant_course = safe_float(course.get("montantPrix") or course.get("allocation") or 0) or 0
    if montant_course <= 0: return 0

    gains_carriere = safe_float(cheval.get("gainsCarriere") or 0) or 0
    nb_courses = safe_int(cheval.get("nombreCourses"), 0)
    if nb_courses < 3 or gains_carriere <= 0: return 0

    # Gain moyen par course = proxy du niveau habituel
    gain_moyen = gains_carriere / nb_courses
    # Niveau estimé de course habituelle (approximation)
    niveau_habituel = gain_moyen / 0.25  # en moyenne un gagnant prend 25% de la dotation

    if niveau_habituel <= 0: return 0
    ratio = montant_course / niveau_habituel

    if ratio < 0.35:    return 8   # Très grosse baisse de classe
    elif ratio < 0.55:  return 5   # Bonne baisse de classe
    elif ratio < 0.75:  return 2   # Légère baisse
    elif ratio > 2.5:   return -5  # Monte en classe (trop relevé)
    elif ratio > 1.8:   return -3
    elif ratio > 1.3:   return -1
    return 0

def feat_signal_mission(cheval, course, tous_partants):
    """
    C4 — Signal composite cheval en mission (3 signaux positifs simultanés).
    Forme montante + drift cote + classe sup + fraîcheur optimale.
    Si au moins 3 critères positifs convergent = bonus fort.
    """
    if not isinstance(cheval, dict): return 0, False

    signaux = 0
    details_signal = []

    # Signal 1 : forme montante
    music = parse_musique(cheval.get("musique", ""))
    pos = [p for t, p in music if t == "pos"]
    if len(pos) >= 4:
        avg_rec = sum(pos[:2]) / 2
        avg_old = sum(pos[2:4]) / 2
        if avg_old - avg_rec >= 1.5:
            signaux += 1
            details_signal.append("forme↑")

    # Signal 2 : drift de cote (marché mise dessus)
    ci = safe_float(cheval.get("coteInitiale"))
    rapport = cheval.get("dernierRapportDirect") or {}
    cf = safe_float(rapport.get("rapport")) if isinstance(rapport, dict) else None
    if ci and cf and ci > 0 and (ci - cf) / ci > 0.12:
        signaux += 1
        details_signal.append("drift↓")

    # Signal 3 : baisse de classe
    bc = feat_baisse_de_classe(cheval, course)
    if bc >= 3:
        signaux += 1
        details_signal.append("classe↓")

    # Signal 4 : fraîcheur optimale (15-35 jours)
    d = cheval.get("dateDerniereCourse")
    if d:
        try:
            if isinstance(d, (int, float)):
                dd = datetime.fromtimestamp(d / 1000).date()
            else:
                raw = str(d)
                for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                    try: dd = datetime.strptime(raw[:10], fmt).date(); break
                    except ValueError: continue
            jours = (date.today() - dd).days
            if 15 <= jours <= 35:
                signaux += 1
                details_signal.append("repos✓")
        except Exception: pass

    # Signal 5 : driver/trainer de qualité
    driver_qual = feat_qualite_driver(cheval)
    if driver_qual >= 3:
        signaux += 1
        details_signal.append("driver★")

    # Signal 6 : taux victoire élevé
    total = safe_int(cheval.get("nombreCourses"), 0)
    vict  = safe_int(cheval.get("nombreVictoires"), 0)
    if total >= 5 and vict / total >= 0.20:
        signaux += 1
        details_signal.append("txV✓")

    is_mission = signaux >= 3
    if signaux >= 5:    return 10, is_mission
    elif signaux >= 4:  return 7,  is_mission
    elif signaux >= 3:  return 4,  is_mission
    return 0, False

def feat_premiere_sortie_hiver(cheval):
    """
    C5 — Première sortie après pause hivernale (novembre-février).
    Les chevaux qui reprennent après 3+ mois d absence sous-performent souvent.
    Différent de feat_repos_optimal : ici on détecte spécifiquement la reprise hivernale.
    """
    if not isinstance(cheval, dict): return 0
    mois_actuel = datetime.now().month
    # Début de saison (mars-mai) = période de reprise
    if mois_actuel not in [3, 4, 5]: return 0

    d = cheval.get("dateDerniereCourse")
    if not d: return 0
    try:
        if isinstance(d, (int, float)):
            dd = datetime.fromtimestamp(d / 1000).date()
        else:
            raw = str(d)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                try: dd = datetime.strptime(raw[:10], fmt).date(); break
                except ValueError: continue
            else: return 0
        jours = (date.today() - dd).days
        # Absence > 90 jours en période de reprise = pénalité
        if jours > 120: return -4
        elif jours > 90: return -2
        elif jours > 60: return -1
    except Exception: pass
    return 0

# ═══════════════════════════════════════════════════════════════
# BLOC B — Jockey / Entraîneur enrichis
# ═══════════════════════════════════════════════════════════════

# Statistiques jockey/driver par hippodrome
# On utilise la musique des autres partants comme proxy
def feat_jockey_hippo(cheval, tous_partants, hippo_code):
    """
    B1 — Taux de victoire du jockey sur CET hippodrome précis.
    Proxy : partants de la course avec le même jockey + historique musique.
    """
    if not isinstance(cheval, dict): return 0
    jid = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    if not jid or not hippo_code: return 0
    v = s = 0
    for p in (tous_partants or []):
        if not isinstance(p, dict): continue
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        if j == jid:
            s += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1): v += 1
    if s < 2: return 0
    tx = v / s
    if tx > 0.35: return 5
    elif tx > 0.20: return 3
    elif tx > 0.10: return 1
    return 0

def feat_trainer_discipline(cheval, tous_partants, disc):
    """
    B2 — Taux de victoire de l entraîneur dans CETTE discipline.
    Signal fort quand entraîneur spécialisé (trot vs galop vs obstacle).
    """
    if not isinstance(cheval, dict): return 0
    tid = dict_id(cheval.get("entraineur"))
    if not tid: return 0
    v = s = 0
    for p in (tous_partants or []):
        if not isinstance(p, dict): continue
        if dict_id(p.get("entraineur")) == tid:
            s += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1): v += 1
    if s < 2: return 0
    tx = v / s
    if tx > 0.30: return 5
    elif tx > 0.18: return 3
    elif tx > 0.08: return 1
    return 0

def feat_combo_enrichi(cheval, tous_partants):
    """
    B3 — Association jockey + entraîneur enrichie.
    Version améliorée de feat_combo_connexion avec seuil abaissé.
    """
    if not isinstance(cheval, dict): return 0
    jid = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    tid = dict_id(cheval.get("entraineur"))
    if not jid or not tid: return 0
    v = s = 0
    for p in (tous_partants or []):
        if not isinstance(p, dict): continue
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        t = dict_id(p.get("entraineur"))
        if j == jid and t == tid:
            s += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1): v += 1
    if s < 2: return 0
    tx = v / s
    if tx > 0.40: return 7
    elif tx > 0.25: return 4
    elif tx > 0.12: return 2
    elif tx < 0.04 and s >= 4: return -3
    return 0

# Spécialistes par hippodrome — Source : ruedesturfistes.over-blog.com
# Format : "HIPPODROME": ["NOM1", "NOM2", ...]
SPECIALISTES_HIPPO = {
    "SABLES":           ["RAFFIN O", "RAFFIN E", "RAFFIN OLIVIER", "RAFFIN ERIC", "LECOURT"],
    "LES SABLES":       ["RAFFIN O", "RAFFIN E", "RAFFIN OLIVIER", "RAFFIN ERIC", "LECOURT"],
    "GRAIGNES":         ["RAFFIN O", "RAFFIN E", "RAFFIN OLIVIER", "RAFFIN ERIC", "DELACOUR", "THRIOLLET", "ERNAULT", "LE COURTOIS"],
    "CHERBOURG":        ["AUDEBERT", "GROUSSARD"],
    "CHARTRES":         ["LENOIR"],
    "TOULOUSE":         ["MARIE B", "MARIE JP", "MARIE"],
    "VICHY":            ["LERENARD", "BOILLEREAU", "ROGER S", "DUCHER", "FOURNIGAULT", "FRECELLE"],
    "VIRE":             ["TOUTAIN"],
    "DIEPPE":           ["OUVRIE", "BLANDIN", "SENET", "GODEY", "GOETZ"],
    "MESLAY":           ["NIVARD"],
    "MONT ST MICHEL":   ["DERSOIR", "HALLAIS"],
    "MONT-SAINT":       ["DERSOIR", "HALLAIS"],
    "CRAON":            ["ABRIVARD M", "ABRIVARD"],
    "MARSEILLE":        ["BRIAND YA", "BRIAND", "FERON", "MARTENS", "METAYER", "JAFFRELOT", "BROSSARD", "CORMY"],
    "SAINT GALMIER":    ["JAFFRELOT", "BOILLEREAU"],
    "SAINT-GALMIER":    ["JAFFRELOT", "BOILLEREAU"],
    "BEAUMONT":         ["BAZIRE", "LE BELLER", "RAFFIN E", "RAFFIN ERIC", "VIMOND", "TREICH", "BROSSARD", "MARMION", "SOULOY", "BAUDOIN", "MONCLIN", "MARIE", "CORDEAU"],
    "STRASBOURG":       ["DERSOIR", "LUCK"],
    "REIMS":            ["LENOIR", "POREE", "DUVALDESTIN T", "DUVALDESTIN", "OUVRIE", "BEZIER", "GILLOT", "MASSCHAELE", "VERVA"],
    "LA CAPELLE":       ["VERBEECK", "PITON B", "PITON JP", "ABRIVARD L", "VERCRUYSSE", "BAZIRE", "VERVA", "DE FOLLEVILLE", "ABRIVARD", "ROELENS", "BAUDRON"],
    "CROISE LAROCHE":   ["OUVRIE", "SENET", "LANNOO", "ROELENS", "VERBEECK", "MARTENS", "MASSCHAELE", "VERVA"],
    "CAGNES":           ["MARTENS", "METAYER", "JAFFRELOT", "BROSSARD", "CORMY", "ENSCH"],
    "CABOURG":          ["DUVALDESTIN T", "DUVALDESTIN", "LE BELLER", "NIVARD", "LEVOY", "BAZIRE", "WIELS", "MARMION", "SIONNEAU", "VIEL", "BAUDRON", "LESVESQUE", "DERSOIR"],
    "VINCENNES":        [],  # Autostart : numéros 4, 5, 6 favorisés (géré par feat_avantage_corde_hippo)
    "ENGHIEN":          ["RAFFIN E", "RAFFIN ERIC", "NIVARD", "VERVA", "ROUSSEL N", "ROUSSEL"],
    "PONTCHATEAU":      [],  # Difficile à rendre la distance
    "VIVAUX":           [],  # Difficile à rendre la distance
}

def feat_specialiste_hippo(cheval, hippo_nom):
    """
    B4 — Spécialiste de l hippodrome.
    Remplace le signal gazole (impossible à détecter automatiquement).
    Source : ruedesturfistes.over-blog.com — spécialistes par piste.
    Bonus fort si le driver OU l entraîneur est listé pour cet hippodrome.
    """
    if not isinstance(cheval, dict): return 0
    hippo = str(hippo_nom or "").upper()

    # Trouver la liste de spécialistes pour cet hippodrome
    specialistes = []
    for key, noms in SPECIALISTES_HIPPO.items():
        if key in hippo or hippo in key:
            specialistes = noms
            break

    if not specialistes: return 0

    def nom_match(nom_api, refs):
        nom = str(nom_api or "").upper()
        if not nom: return False
        for ref in refs:
            parts = ref.split()
            if all(p in nom for p in parts):
                return True
        return False

    # Vérifier jockey/driver
    jockey = cheval.get("jockey") or cheval.get("driver") or {}
    nom_j = (jockey.get("nom") or jockey.get("libelle") or "") if isinstance(jockey, dict) else str(jockey)
    if nom_match(nom_j, specialistes):
        return 6  # Spécialiste driver = signal très fort

    # Vérifier entraîneur
    entraineur = cheval.get("entraineur") or {}
    nom_e = (entraineur.get("nom") or entraineur.get("libelle") or "") if isinstance(entraineur, dict) else str(entraineur)
    if nom_match(nom_e, specialistes):
        return 4  # Spécialiste entraîneur = bon signal

    return 0

def feat_tendance_3mois(cheval):
    """
    B5 — Tendance de forme sur 3 mois (proxy via musique complète).
    Compare les 3 premières positions vs les 3 suivantes.
    Un cheval en nette progression mérite un bonus.
    """
    if not isinstance(cheval, dict): return 0
    music = parse_musique(cheval.get("musique", ""))
    pos = [p for t, p in music if t == "pos"]
    if len(pos) < 5: return 0
    rec3 = pos[:3]
    old3 = pos[3:6]
    avg_rec = sum(rec3) / len(rec3)
    avg_old = sum(old3) / len(old3)
    delta = avg_old - avg_rec  # positif = amélioration
    if delta >= 3:    return 5   # nette progression
    elif delta >= 1.5: return 3
    elif delta >= 0.5: return 1
    elif delta <= -3:  return -4  # chute de forme
    elif delta <= -1.5: return -2
    return 0

def feat_repos_optimal(cheval):
    """
    B6 — Intervalle de repos optimal entre les courses.
    Chaque cheval a son rythme. On pénalise les extrêmes.
    """
    if not isinstance(cheval, dict): return 0
    d = cheval.get("dateDerniereCourse")
    if not d: return 0
    try:
        if isinstance(d, (int, float)):
            dd = datetime.fromtimestamp(d / 1000).date()
        else:
            raw = str(d)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try: dd = datetime.strptime(raw[:10], fmt).date(); break
                except ValueError: continue
            else: return 0
        jours = (date.today() - dd).days
        if jours < 0: return 0
        # Zone optimale : 15-40 jours
        if 15 <= jours <= 40:  return 3
        elif 8 <= jours < 15:  return 1   # un peu juste
        elif 40 < jours <= 70: return 0   # neutre
        elif jours > 120:      return -4  # longue absence
        elif jours > 90:       return -2
        elif jours < 8:        return -1  # trop frais
    except Exception: pass
    return 0

def feat_course_preparation(cheval):
    """
    B7 — Détection course de préparation avant une course visée.
    Signe : cheval qui court sur une distance plus courte que d habitude,
    ou qui a réalisé une performance en-dessous de sa valeur habituellement.
    Proxy : dernière course avec une position moins bonne que sa moyenne
    sur une distance atypique = course de mise en jambe.
    """
    if not isinstance(cheval, dict): return 0
    music = parse_musique(cheval.get("musique", ""))
    pos = [p for t, p in music if t == "pos"]
    if len(pos) < 3: return 0

    # Si la dernière course était mauvaise mais les précédentes bonnes
    derniere = pos[0]
    moy_prec = sum(pos[1:4]) / len(pos[1:4])

    # Cheval régulier qui a fait une mauvaise course → course de prépa ?
    if derniere > moy_prec + 3 and moy_prec <= 4:
        return 4  # Signal : mauvaise dernière course mais cheval habituellement bon
    elif derniere > moy_prec + 2 and moy_prec <= 5:
        return 2
    return 0


# ═══════════════════════════════════════════════════════════════
# BLOC D/E/F+ — 11 nouveaux critères
# Sources : Zone-Turf guide trot/plat/obstacle
# ═══════════════════════════════════════════════════════════════

# 1. Bonus trotteur étranger (inverser malus actuel)
def feat_trotteur_etranger(cheval, disc):
    """
    D1 — En TROT : un cheval étranger venant courir en France
    est souvent MEILLEUR qu il n y paraît car les courses françaises
    sont mieux dotées. À gains égaux, il surclasse les trotteurs français.
    En PLAT/OBSTACLE : l inverse (courses étrangères souvent moins relevées).
    """
    if not isinstance(cheval, dict): return 0
    pays = str(
        cheval.get("paysOrigine") or cheval.get("pays") or
        cheval.get("nationalite") or ""
    ).upper()
    if not pays or "FRA" in pays or "FRANCE" in pays: return 0

    pays_etrangers_trot = ["BEL","BELGIQUE","SWE","SUEDE","GER","ALL",
                           "ALLEMAGNE","ITA","ITALIE","NOR","NORVEGE",
                           "DEN","DANEMARK","FIN","FINLANDE","NET","PAYS-BAS"]
    is_etranger = any(p in pays for p in pays_etrangers_trot)
    if not is_etranger: return 0

    if disc == "TROT":   return 3   # Bonus : vient de courses moins dotées
    else:                return -2  # Malus : champ étranger souvent plus faible

# 2. Spécialiste PSF (Piste Sable Fibré)
HIPPOS_PSF = ["DEAUVILLE", "PAU", "CAGNES", "CAGNES-SUR-MER", "CAGNES SUR MER"]

def feat_specialiste_psf(cheval, hippo_nom, disc):
    """
    D2 — Spécialiste PSF : certains chevaux (origines américaines surtout)
    excellent sur les pistes en sable fibré. Deauville, Pau, Cagnes-sur-Mer.
    """
    if not isinstance(cheval, dict) or disc != "PLAT": return 0
    hippo = str(hippo_nom or "").upper()
    is_psf = any(h in hippo for h in HIPPOS_PSF)
    if not is_psf: return 0

    # Proxy : gains sur PSF si disponible
    gph = cheval.get("gainsParHippodrome") or cheval.get("gainsParTypeHippodrome") or {}
    if isinstance(gph, dict):
        for k, v in gph.items():
            if any(h in str(k).upper() for h in HIPPOS_PSF):
                gains_psf = safe_float(v) or 0
                if gains_psf > 20000: return 5
                elif gains_psf > 5000: return 3
                elif gains_psf > 0:   return 1

    # Proxy origines américaines (données non disponibles directement)
    return 0

# 3. Course en ligne droite
HIPPOS_LIGNE_DROITE = {
    "CHANTILLY":       [1200, 1400, 1600],
    "MAISONS-LAFFITTE":  [1300, 1600],
    "MAISONS LAFFITTE":  [1300, 1600],
    "DEAUVILLE":       [1200, 1400],
}

def feat_ligne_droite(cheval, hippo_nom, course, disc):
    """
    D3 — Courses en ligne droite sur certains hippodromes.
    Certains chevaux ont une vraie aptitude ou aversion pour ces parcours.
    On détecte via l hippodrome + distance.
    """
    if not isinstance(cheval, dict) or disc != "PLAT": return 0
    hippo = str(hippo_nom or "").upper()
    dist  = safe_float(course.get("distance") or 0) if isinstance(course, dict) else 0

    is_ligne = False
    for h, dists in HIPPOS_LIGNE_DROITE.items():
        if h in hippo:
            if not dists or any(abs(dist - d) <= 100 for d in dists):
                is_ligne = True
                break

    if not is_ligne: return 0

    # Proxy : victoires sur cet hippodrome en ligne droite
    v = safe_int(cheval.get("nombreVictoiresPiste") or 0, 0)
    if v >= 2: return 4
    elif v == 1: return 2
    return 0

# 4. Catégorie distance plat (famille naturelle)
def feat_categorie_distance_plat(cheval, course, disc):
    """
    D4 — Chaque cheval plat a sa famille de distance naturelle.
    Sprint 1000-1100m / Flyer 1200-1400m / Miler 1600-1800m /
    Intermédiaire 1800-2400m / Stayer >2400m.
    Bonus si la course correspond à la distance habituelle du cheval.
    """
    if not isinstance(cheval, dict) or disc != "PLAT": return 0
    dist = safe_float(course.get("distance") or 0) if isinstance(course, dict) else 0
    if not dist: return 0

    def categorie(d):
        if d <= 1150:  return "SPRINT"
        elif d <= 1450: return "FLYER"
        elif d <= 1850: return "MILER"
        elif d <= 2400: return "INTER"
        else:           return "STAYER"

    cat_course = categorie(dist)

    # Distance préférée déclarée
    dist_pref = safe_float(cheval.get("distancePrefere") or 0)
    if dist_pref:
        cat_pref = categorie(dist_pref)
        if cat_pref == cat_course: return 5
        elif abs(list(["SPRINT","FLYER","MILER","INTER","STAYER"]).index(cat_pref) -
                 list(["SPRINT","FLYER","MILER","INTER","STAYER"]).index(cat_course)) == 1:
            return 2  # Catégorie adjacente
        else: return -3  # Hors catégorie

    # Proxy via gains par type de distance
    gpd = cheval.get("gainsParTypeDistance") or cheval.get("gainsParDistance") or {}
    if isinstance(gpd, dict) and gpd:
        cat_to_range = {
            "COURT": "SPRINT", "COURTE": "FLYER",
            "MOYEN": "MILER",  "MOYENNE": "MILER",
            "LONG":  "INTER",  "LONGUE": "INTER",
            "TRES_LONG": "STAYER"
        }
        meilleur = max(gpd, key=lambda k: safe_float(gpd[k]) or 0)
        cat_pref = cat_to_range.get(str(meilleur).upper())
        if cat_pref and cat_pref == cat_course: return 3
        elif cat_pref and cat_pref != cat_course: return -2
    return 0

# 5. Bonus hongre renforcé en obstacle
def feat_sexe_discipline(cheval, disc):
    """
    D5 — En obstacle, les hongres dominent très largement.
    Les mâles entiers ont souvent des problèmes au franchissement.
    """
    if not isinstance(cheval, dict): return 0
    sexe = str(cheval.get("sexe") or cheval.get("indicateurSexe") or "").upper()
    is_hongre = "HONG" in sexe or sexe == "H"
    if disc == "OBSTACLE":
        if is_hongre: return 5      # Très fort avantage en obstacle
        elif sexe in ["M", "MALE", "ENTIER"]: return -3  # Entier = désavantage
    elif disc == "TROT":
        if is_hongre: return 2
    elif disc == "PLAT":
        if is_hongre: return 1
    return 0

# 6. Spécialiste haies vs steeple
def feat_specialite_obstacle(cheval, course, disc):
    """
    D6 — En obstacle, il faut distinguer haies et steeple/cross.
    Un bon cheval de haies peut réussir en steeple, l inverse est rare.
    Vérifier que la discipline de la course correspond à la spécialité du cheval.
    """
    if not isinstance(cheval, dict) or disc != "OBSTACLE": return 0
    if not isinstance(course, dict): return 0

    libelle = str(course.get("libelle") or course.get("discipline") or "").upper()
    is_steeple = any(x in libelle for x in ["STEEPLE","CROSS","CROSS COUNTRY"])
    is_haies   = "HAIE" in libelle

    # Proxy via musique : si 'h' dans la musique = haies, 's' = steeple
    # En pratique l API ne différencie pas toujours
    # On utilise les gains par discipline si disponibles
    gpd = cheval.get("gainsParDiscipline") or {}
    if isinstance(gpd, dict) and gpd:
        gains_haies   = safe_float(gpd.get("HAIES") or gpd.get("H") or 0) or 0
        gains_steeple = safe_float(gpd.get("STEEPLE") or gpd.get("S") or 0) or 0
        if is_steeple and gains_haies > gains_steeple * 3:
            return -2  # Spécialiste haies en steeple = risqué
        if is_haies and gains_steeple > gains_haies:
            return 1   # Steeple qui fait haies = peut réussir
    return 0

# 7. Rentrée en plat avant obstacle (course de préparation)
def feat_rentree_plat_obstacle(cheval, course, disc):
    """
    D7 — Un cheval d obstacle qui fait une rentrée en plat = course de mise en jambe.
    La course du jour en obstacle est souvent la vraie cible.
    """
    if not isinstance(cheval, dict) or disc != "OBSTACLE": return 0
    # Proxy : si la dernière course du cheval était sur une distance courte
    # ou si sa discipline habituelle était le plat
    music = parse_musique(cheval.get("musique",""))
    pos = [p for t,p in music if t=="pos"]
    if not pos: return 0
    # Si dernière course mauvaise mais historique bon en obstacle = prépa
    if pos[0] > 5 and len(pos) >= 3 and sum(pos[1:4])/len(pos[1:4]) <= 4:
        return 4  # Dernière course = mise en jambe, le cheval vise cette course
    return 0

# 8. Débutant en handicap sous-évalué
def feat_debutant_handicap(cheval, course):
    """
    D8 — En handicap, les débutants peuvent être pris à un poids
    inférieur à leur valeur réelle. Signal : peu de courses + en handicap.
    """
    if not isinstance(cheval, dict) or not isinstance(course, dict): return 0
    libelle = str(course.get("libelle") or course.get("conditions") or "").upper()
    is_handicap = "HANDICAP" in libelle or "HCAP" in libelle
    if not is_handicap: return 0
    total = safe_int(cheval.get("nombreCourses"), 0)
    if total <= 5: return 3  # Peu de courses = potentiel sous-évalué
    return 0

# 9. Forme saisonnière confirmée
def feat_forme_saisonniere(cheval):
    """
    D9 — Certains chevaux gagnent chaque année à la même période.
    Proxy : si la dernière victoire était au même mois que le mois actuel.
    """
    if not isinstance(cheval, dict): return 0
    mois_actuel = datetime.now().month
    d = cheval.get("dateDerniereVictoire") or cheval.get("dateDerniereCourse")
    if not d: return 0
    try:
        if isinstance(d, (int, float)):
            dm = datetime.fromtimestamp(d/1000).month
        else:
            raw = str(d)
            for fmt in ("%Y-%m-%d","%d/%m/%Y"):
                try: dm = datetime.strptime(raw[:10],fmt).month; break
                except ValueError: continue
            else: return 0
        if dm == mois_actuel: return 4         # Même mois = forme saisonnière
        elif abs(dm - mois_actuel) <= 1: return 2  # Mois adjacent
    except Exception: pass
    return 0

# 10. Cheval favori dans les 3 dernières
def feat_favori_recent(cheval):
    """
    D10 — Un cheval régulièrement parti favori = confirmation de qualité.
    On repère les chevaux dont la cote est basse régulièrement.
    Proxy : si la cote actuelle est faible + bon taux de victoires = favori régulier.
    """
    if not isinstance(cheval, dict): return 0
    cote = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or            safe_float(cheval.get("coteInitiale"))
    if not cote: return 0
    total = safe_int(cheval.get("nombreCourses"), 0)
    vict  = safe_int(cheval.get("nombreVictoires"), 0)
    if total < 3: return 0
    taux = vict / total
    if cote <= 3 and taux >= 0.25: return 4   # Favori régulier qui gagne
    elif cote <= 5 and taux >= 0.20: return 2
    elif cote <= 3 and taux < 0.10: return -2  # Surcoté malgré basse cote
    return 0

# 11. Numéros de corde affinés par hippodrome
AVANTAGE_CORDE_HIPPO_DETAIL = {
    "LONGCHAMP":      {"dist_max": 1600, "terrain": "BON", "favoris": [1,2,3,4]},
    "CHANTILLY":      {"dist_max": 2000, "terrain": None,  "favoris": [1,2,3]},
    "SAINT-CLOUD":    {"dist_max": 1800, "terrain": None,  "favoris": [1,2,3,4,5]},
    "DEAUVILLE":      {"dist_max": 1600, "terrain": None,  "favoris": [1,2,3]},
    "MAISONS-LAFFITTE": {"dist_max": None, "terrain": None, "favoris": [1,2,3]},
    "MAISONS LAFFITTE": {"dist_max": None, "terrain": None, "favoris": [1,2,3]},
    "AUTEUIL":        {"dist_max": None, "terrain": None,  "favoris": [1,2,3,4]},
}

def feat_corde_hippo_affine(cheval, hippo_nom, course, disc):
    """
    D11 — Numéros de corde affinés selon hippodrome + distance + terrain.
    Ex: à Longchamp terrain bon sur 1400m → petits numéros très favorisés.
    """
    if not isinstance(cheval, dict) or disc not in ["PLAT","OBSTACLE"]: return 0
    hippo = str(hippo_nom or "").upper()
    num   = safe_int(cheval.get("numPmu", 0))
    dist  = safe_float(course.get("distance") or 0) if isinstance(course, dict) else 0

    for h_key, data in AVANTAGE_CORDE_HIPPO_DETAIL.items():
        if h_key in hippo:
            dist_max = data.get("dist_max")
            favoris  = data.get("favoris", [])
            # Appliquer seulement si distance correspondante
            if dist_max and dist and dist > dist_max:
                continue
            if num in favoris: return 4
            elif num <= max(favoris) + 2: return 1
            else: return -1
    return 0

# ═══════════════════════════════════════════════════════════════
# BLOC F — Données statiques (hippodromes, jockeys, entraîneurs)
# Source : bruitsdecuries.fr + expérience terrain
# ═══════════════════════════════════════════════════════════════

# F1 — Fiabilité des hippodromes (vert/orange/rouge)
HIPPO_VERT = {
    "ANGERS", "ARGENTAN", "BORDEAUX", "BORDEAUX LE BOUSCAT",
    "CHATELAILLON", "CHATELAILLON LA ROCHELLE", "CHOLET",
    "GRAIGNES", "LAVAL", "LE MANS", "LE MONT SAINT MICHEL",
    "LES SABLES D'OLONNE", "LYON PARILLY", "LISIEUX",
    "MARSEILLE", "MARSEILLE BORELY", "PORNICHET",
    "SAINT-MALO", "SAINT MALO", "TOULOUSE", "VIRE", "CAEN",
}
HIPPO_ROUGE = {
    "AMIENS", "BEAUMONT DE LOMAGNE", "CAGNES", "CAGNES SUR MER",
    "CHERBOURG", "CLAIREFONTAINE", "CRAON", "DIEPPE", "FEURS",
    "LA CAPELLE", "LANGON", "LE CROISE LAROCHE", "MAUQUENCHY",
    "MESLAY DU MAINE", "RAMBOUILLET", "REIMS", "SAINT-GALMIER",
    "SAINT GALMIER", "VINCENNES", "PARIS-VINCENNES",
}
HIPPO_ORANGE = {
    "CABOURG", "CHATEAUBRIANT", "CHARTRES", "CORDEMAIS",
    "ENGHIEN", "HYERES", "MAURE DE BRETAGNE", "NANCY",
    "NANTES", "SAINT-BRIEUC", "SAINT BRIEUC",
    "STRASBOURG", "VICHY",
}

def feat_fiabilite_hippo(hippo_nom):
    """
    F1 — Fiabilité de l'hippodrome.
    Vert = arrivées régulières → bonus confiance
    Rouge = arrivées aléatoires → malus fort
    """
    nom = str(hippo_nom or "").upper()
    for h in HIPPO_VERT:
        if h in nom or nom in h:
            return 4, "VERT"
    for h in HIPPO_ROUGE:
        if h in nom or nom in h:
            return -5, "ROUGE"
    for h in HIPPO_ORANGE:
        if h in nom or nom in h:
            return 0, "ORANGE"
    return 0, "INCONNU"

# F2 — Avantage corde spécifique par hippodrome
AVANTAGE_CORDE = {
    "VINCENNES": {
        "info": "Wagon de corde très prolifique. Petite piste = piège.",
        "bonus_corde": True,
        "num_favoris": [1, 2, 3],
    },
    "ENGHIEN": {
        "info": "Piste dure, déferrage déconseillé. N°9 excellent.",
        "bonus_corde": False,
        "num_favoris": [9],
        "anti_deferre": True,
    },
}

def feat_avantage_corde_hippo(cheval, hippo_nom, course):
    """
    F2 — Avantage position départ selon hippodrome spécifique.
    """
    if not isinstance(cheval, dict): return 0
    nom = str(hippo_nom or "").upper()
    num = safe_int(cheval.get("numPmu", 0))
    nb  = safe_int(course.get("nombreDeclaresPartants", 12) if isinstance(course, dict) else 12) or 12

    for hippo_key, data in AVANTAGE_CORDE.items():
        if hippo_key in nom:
            if num in data.get("num_favoris", []):
                return 4
            # Pénalité déferré sur Enghien
            if data.get("anti_deferre"):
                deferre = str(cheval.get("deferre") or "").upper()
                if deferre and deferre not in ["", "NONE", "0", "NON"]:
                    return -3
    # Règle générale : intérieur favorisé
    return 0

# F3 — Pénalité jument favorite
def feat_penalite_jument_favorite(cheval, tous_partants):
    """
    F3 — Les juments favorites tiennent rarement leur statut.
    Pénalité si jument avec cote parmi les 2 plus basses du champ.
    """
    if not isinstance(cheval, dict): return 0
    sexe = str(cheval.get("sexe") or cheval.get("indicateurSexe") or "").upper()
    is_jument = "JUMENT" in sexe or sexe in ["F", "M", "MF"]
    if not is_jument: return 0

    cote = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or            safe_float(cheval.get("coteInitiale"))
    if not cote: return 0

    cotes = []
    for p in (tous_partants or []):
        if not isinstance(p, dict): continue
        c = safe_float((p.get("dernierRapportDirect") or {}).get("rapport")) or             safe_float(p.get("coteInitiale"))
        if c: cotes.append(c)

    if not cotes: return 0
    cotes_sorted = sorted(cotes)
    rang = cotes_sorted.index(min(cotes_sorted, key=lambda x: abs(x - cote))) + 1

    # Jument favorite (top 2) → malus
    if rang <= 2:
        return -4
    return 0

# F4 — Pénalité premier déferré jeune cheval
def feat_penalite_premier_deferre_jeune(cheval):
    """
    F4 — Éviter les courses de 4 ans déferrés pour la première fois.
    Les surprises sont fréquentes, le comportement imprévisible.
    """
    if not isinstance(cheval, dict): return 0
    age = safe_float(cheval.get("age"))
    if not age or age > 5: return 0
    deferre = str(cheval.get("deferre") or "").upper()
    # Premier déferré = 4 pattes ou antérieurs + jeune cheval
    if age <= 4 and ("DAP" in deferre or "4 PATTE" in deferre or "DA" in deferre):
        return -4
    return 0

# F5 — Bonus course de série vs grande épreuve
def feat_course_serie(course):
    """
    F5 — Les courses de série en province = meilleures opportunités.
    Les grandes épreuves (Quinté, Prix de référence) = moins prévisibles.
    """
    if not isinstance(course, dict): return 0
    libelle = str(course.get("libelle") or "").upper()
    montant = safe_float(course.get("montantPrix") or course.get("allocation") or 0) or 0

    # Grande épreuve = malus
    if any(x in libelle for x in ["QUINTÉ", "QUINTE", "PRIX DE RÉFÉRENCE", "GROUPE"]):
        return -2
    # Course modeste en province = bonus (arrivées plus régulières)
    if montant < 20000 and montant > 0:
        return 2
    return 0

# F6 — Distance optimale affinée (±50m au lieu de ±150m)
def feat_distance_affinee(cheval, course):
    """
    F6 — Version affinée de la distance optimale.
    ±50m = parfait, ±100m = bon, ±200m = acceptable, au-delà = pénalité.
    """
    if not isinstance(cheval, dict) or not isinstance(course, dict): return 0
    da = safe_float(course.get("distance") or 0)
    if not da: return 0
    dp = safe_float(cheval.get("distancePrefere") or cheval.get("distanceOptimale"))
    if not dp: return 0
    ecart = abs(da - dp)
    if ecart <= 50:   return 6
    elif ecart <= 100: return 4
    elif ecart <= 200: return 2
    elif ecart <= 400: return 0
    else:             return -3

# F7 — Météo + terrain croisé (version améliorée)
def feat_meteo_piste_affinee(cheval, course, disc, pluie_mm=0):
    """
    F7 — Croisement météo du jour + préférence terrain du cheval.
    Utilise la pluie en mm de l'API Open-Meteo si disponible.
    En trot : piste PSF peu affectée par la pluie.
    """
    if not isinstance(cheval, dict): return 0
    if disc == "TROT": return 0  # PSF peu sensible

    terrain_pref = str(cheval.get("terrainPrefere") or cheval.get("typeTerrain") or "").upper()

    # Déterminer état piste selon pluie
    if pluie_mm > 8:
        etat = "LOURD"
    elif pluie_mm > 3:
        etat = "SOUPLE"
    else:
        etat = "BON"

    # État piste déclaré par l'hippodrome (prioritaire)
    etat_declare = str(
        course.get("terrain") or course.get("etatPiste") or
        course.get("terrainGeneral") or ""
    ).upper()
    if etat_declare:
        if any(x in etat_declare for x in ["LOURD", "HEAVY", "MOU"]): etat = "LOURD"
        elif any(x in etat_declare for x in ["SOUPLE", "SOFT"]):       etat = "SOUPLE"
        elif any(x in etat_declare for x in ["BON", "GOOD", "FIRM"]):  etat = "BON"

    if not terrain_pref:
        # Proxy via gains par nature de piste
        gp = cheval.get("gainsParNaturePiste") or {}
        if isinstance(gp, dict) and gp:
            meilleur = max(gp, key=lambda k: safe_float(gp[k]) or 0)
            terrain_pref = str(meilleur).upper()

    if not terrain_pref: return 0

    aime_lourd  = any(x in terrain_pref for x in ["LOURD", "HEAVY", "MOU", "SOFT"])
    aime_souple = any(x in terrain_pref for x in ["SOUPLE"])
    aime_bon    = any(x in terrain_pref for x in ["BON", "GOOD", "FIRM", "SEC"])

    if aime_lourd  and etat == "LOURD":  return 6
    if aime_bon    and etat == "BON":    return 5
    if aime_souple and etat == "SOUPLE": return 4
    if aime_lourd  and etat == "BON":    return -5
    if aime_bon    and etat == "LOURD":  return -5
    return 0

# F8 — Jockeys/drivers à suivre ou éviter (bruitsdecuries.fr)
DRIVERS_A_SUIVRE = {
    "ABRIVARD ALEXANDRE", "ABRIVARD MATTHIEU", "BAZIRE JEAN MICHEL",
    "BEKAERT DAVID", "BRIAND THEO", "CLOZIER FREDERIC",
    "COLETTE ALEXIS", "DERIEUX ROMAIN", "DUVALDESTIN CLEMENT",
    "DUVALDESTIN THEO", "ERNAULT SEBASTIEN", "GRASSET MAXIME",
    "LAGADEUC FRANCOIS", "LEBOURGEOIS YOANN", "MONCLIN JEAN PHILIPPE",
    "MOTTIER MATTHIEU", "NIVARD FRANCK", "PLOQUIN PAUL",
    "RAFFIN ERIC", "ROCHARD BENJAMIN", "STEFANO STEVE",
    "THONNERIEUX KEVYN", "WIELS ANTOINE",
    "LAMY ADRIEN", "SEGUIN QUENTIN",
}
DRIVERS_A_EVITER = {
    "BACSICH MARIE", "BAUDE SEBASTIEN", "BEZIER MAXIME",
    "BIGEON CHARLES", "BOISNARD CHRISTIAN", "BOSSUET FP",
    "BOUVIER ROBIN", "CHALON CHRISTOPHE", "CHALON THOMAS",
    "CONGARD ROMAIN", "COPPENS BRYAN", "CUILLIER CHARLES",
    "DABOUIS ANTOINE", "DELACOUR GILLES", "DERSOIR JEAN LOIC",
    "DERSOIR CLARA", "DERSOIR CARINE", "DESMIGNEUX FLORIAN",
    "DIEUDONNE SYLVAIN", "DOLLION ANTHONY", "FERRE CORENTIN",
    "FRIBAULT MATHIEU", "GALLIER CHRISTOPHE", "GENCE FABIEN",
    "GUELPA JUNIOR", "HARDY SEBASTIEN", "HOUYVET SEBASTIEN",
    "LENOIR MICHEL", "LHERЕТЕ ANTOINE", "MARIN GUILLAUME",
    "MEGISSIER CEDRIC", "MEUNIER STEPHANE", "OLIVIER SEBASTIEN",
    "OUVRIE FRANCK", "PACHA NILS", "PETREMENT CHRISTOPHE",
    "PITON BERNARD", "PITON JEAN-CHARLES", "RAFFIN OLIVIER",
    "ROBIN BENOIT", "SEGUIN VINCENT", "SORAIS PIERRE",
    "SOREL JEAN-CHRISTOPHE", "TABESSE FLORIAN", "THOMAIN DAVID",
    "TINTILLIER ANTHONY", "VERVA LAURENT", "VERVA MATHIEU",
    "VERVA PIERRE YVES",
}

def normalise_nom(nom):
    if not nom: return ""
    return str(nom).upper().strip()

def feat_qualite_driver(cheval):
    """
    F8 — Bonus/malus selon la réputation du jockey ou driver.
    Source : bruitsdecuries.fr — liste mise à jour juin 2025.
    """
    if not isinstance(cheval, dict): return 0
    jockey = cheval.get("jockey") or cheval.get("driver") or {}
    if isinstance(jockey, dict):
        nom = normalise_nom(jockey.get("nom") or jockey.get("libelle") or "")
    else:
        nom = normalise_nom(str(jockey))

    if not nom: return 0

    # Vérification par correspondance partielle (prénom + nom)
    for ref in DRIVERS_A_SUIVRE:
        parts = ref.split()
        if all(p in nom for p in parts) or all(p in nom for p in reversed(parts)):
            return 5
    for ref in DRIVERS_A_EVITER:
        parts = ref.split()
        if all(p in nom for p in parts) or all(p in nom for p in reversed(parts)):
            return -3

    return 0

# F9 — Entraîneurs de référence (bruitsdecuries.fr)
ENTRAINEURS_REF = {
    "ABRIVARD LC", "ABRIVARD MATTHIEU", "ALLAIRE PHILIPPE",
    "BAUDOUIN JEAN MICHEL", "BAZIRE JEAN MICHEL", "BIGEON WILLIAM",
    "BONDO ERICK", "BOURLIER STEPHANE", "BRIAND YANNICK ALAIN",
    "CHAVATTE ALAIN", "DERIEUX ROMAIN", "DELLIAUX JEAN-REMI",
    "DESMOTTES ARNAUD", "DUVALDESTIN THIERRY", "ERNAULT SEBASTIEN",
    "GOETZ BENJAMIN", "GRIMAULT ALEXIS", "GUARATO SEBASTIEN",
    "HENRY YANNICK", "LEBLANC FRANCK", "LELIEVRE ENZO",
    "LEVESQUE THOMAS", "MARMION JEAN PAUL", "MOTTIER CHARLEY",
    "ROGER SYLVAIN", "ROUBAUD JEAN-MARIE", "SASSIER MARC",
    "THONNERIEUX KEVYN",
}

def feat_qualite_entraineur(cheval):
    """
    F9 — Bonus si entraîneur dans la liste de référence bruitsdecuries.
    """
    if not isinstance(cheval, dict): return 0
    entraineur = cheval.get("entraineur") or {}
    if isinstance(entraineur, dict):
        nom = normalise_nom(entraineur.get("nom") or entraineur.get("libelle") or "")
    else:
        nom = normalise_nom(str(entraineur))
    if not nom: return 0

    for ref in ENTRAINEURS_REF:
        parts = ref.split()
        if all(p in nom for p in parts) or all(p in nom for p in reversed(parts)):
            return 4
    return 0

def compute_logit(cheval, course, tous_partants, disc):
    if not isinstance(cheval, dict): return 0
    if not isinstance(course, dict): course = {}
    if not isinstance(tous_partants, list): tous_partants = []
    music = parse_musique(cheval.get("musique"))
    est_debutant = len(music) == 0

    # --- Features de forme (poids ~0.25) ---
    f_forme   = feat_forme(music)           # 0-20
    f_reg     = feat_regularite(music)      # 0-8
    f_prog    = feat_progression(music)     # -2..5
    f_place   = feat_ratio_place(cheval)    # 0-8
    f_tx_vict = feat_taux_victoires(cheval) # 0-10

    # --- Marché (poids ~0.35) ---
    cote      = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or \
                safe_float(cheval.get("coteInitiale"))
    f_marche  = clamp(1 / cote * 25, 0, 25) if cote else 0

    # Pour un débutant : on neutralise la forme (inconnue) et on donne
    # plus de poids au marché — c'est lui qui intègre l'info d'entraînement.
    if est_debutant:
        f_forme = f_marche * 0.4   # ancre la forme sur la cote
        f_reg   = 0
        f_prog  = 0
        f_place = 0
        f_tx_vict = 0

    # --- Mouvement de cote ---
    f_drift, drift_val = feat_mouvement_cote(cheval)

    # --- Classe ---
    f_classe  = feat_classe(cheval, course)
    f_perf_rel = feat_performance_relative(cheval, tous_partants)

    # --- Réduction kilométrique (trot) ---
    f_rk, rk_val = feat_reduction_km(cheval, tous_partants, disc)

    # --- Conditions / physique ---
    f_fraich  = feat_fraicheur(cheval)
    f_partants = feat_nb_partants(course)
    f_gains_an = feat_gains_annee(cheval)
    f_sexe    = feat_sexe(cheval)
    f_deferre = feat_deferre(cheval, disc)
    f_poids   = feat_poids(cheval, disc)
    f_oeil    = feat_oeilleres(cheval, disc)

    # --- Écurie / connexion ---
    f_trainer = feat_entraineur(cheval, tous_partants)
    f_ecurie  = feat_forme_ecurie(cheval, tous_partants)
    f_jockey  = feat_jockey(cheval, tous_partants)

    # --- Discipline-spécifique ---
    f_age     = feat_age_disc(cheval, disc)
    f_corde   = feat_corde_plat(cheval, course, disc)
    f_obs     = feat_experience_obs(cheval, disc)
    f_gains_c = feat_gains_carriere_trot(cheval, disc)
    f_recul   = feat_recul_trot(cheval) if disc == "TROT" else 0

    # --- Nouveaux critères (lot 2) ---
    hippo_code = (
        course.get("hippodrome", {}).get("code") or
        course.get("hippodrome", {}).get("nom") or
        course.get("codeHippodrome") or ""
    ) if isinstance(course.get("hippodrome"), dict) else str(course.get("hippodrome") or "")

    f_dist    = feat_distance_optimale(cheval, course)
    f_piste   = feat_historique_piste(cheval, hippo_code)
    f_terrain = feat_terrain(cheval, course, disc)
    f_combo   = feat_combo_connexion(cheval, tous_partants)
    f_reg_cond = feat_regularite_conditions(music, cheval)

    # --- 11 NOUVEAUX CRITÈRES ---
    f_trot_etr     = feat_trotteur_etranger(cheval, disc)
    f_psf          = feat_specialiste_psf(cheval, hippo_code, disc)
    f_ligne_dr     = feat_ligne_droite(cheval, hippo_code, course, disc)
    f_cat_dist     = feat_categorie_distance_plat(cheval, course, disc)
    f_sexe_disc    = feat_sexe_discipline(cheval, disc)
    f_spec_obs     = feat_specialite_obstacle(cheval, course, disc)
    f_rentree_pl   = feat_rentree_plat_obstacle(cheval, course, disc)
    f_debut_hcap   = feat_debutant_handicap(cheval, course)
    f_saison_conf  = feat_forme_saisonniere(cheval)
    f_fav_rec      = feat_favori_recent(cheval)
    f_corde_af     = feat_corde_hippo_affine(cheval, hippo_code, course, disc)

    # --- CORRECTIFS NIVEAU ---
    f_forme_niv    = feat_forme_ponderee_niveau(cheval, course)
    f_etranger     = feat_malus_courses_etrangeres(cheval)
    f_gonfiee      = feat_musique_gonfiee(cheval)
    f_classe_af    = feat_classe_affinee(cheval, course, tous_partants)

    # --- BLOC C : historique temporel ---
    f_vict_mois    = feat_victoires_par_mois(cheval)
    f_serie_pod    = feat_serie_podiums_sans_victoire(cheval)
    f_baisse_cl    = feat_baisse_de_classe(cheval, course)
    f_mission, is_mission = feat_signal_mission(cheval, course, tous_partants)
    f_hiver        = feat_premiere_sortie_hiver(cheval)

    # --- BLOC B : jockey/entraîneur enrichis ---
    f_jock_hippo   = feat_jockey_hippo(cheval, tous_partants, hippo_code)
    f_train_disc   = feat_trainer_discipline(cheval, tous_partants, disc)
    f_combo_enr    = feat_combo_enrichi(cheval, tous_partants)
    f_gazole       = feat_specialiste_hippo(cheval, hippo_code)
    f_tendance3m   = feat_tendance_3mois(cheval)
    f_repos_opt    = feat_repos_optimal(cheval)
    f_prepa        = feat_course_preparation(cheval)

    # --- BLOC F : critères statiques ---
    f_fiab_hippo, hippo_couleur = feat_fiabilite_hippo(hippo_code)
    f_corde_hippo  = feat_avantage_corde_hippo(cheval, hippo_code, course)
    f_jument_fav   = feat_penalite_jument_favorite(cheval, tous_partants)
    f_premier_def  = feat_penalite_premier_deferre_jeune(cheval)
    f_course_serie = feat_course_serie(course)
    f_dist_affinee = feat_distance_affinee(cheval, course)
    f_meteo_af     = feat_meteo_piste_affinee(cheval, course, disc)
    f_driver_qual  = feat_qualite_driver(cheval)
    f_trainer_qual = feat_qualite_entraineur(cheval)

    # Logit = somme pondérée (les poids reflètent l'importance relative)
    logit = (
        f_forme * 1.5   # renforcé — modèle 100% objectif
        + f_reg * 1.2   # renforcé
        + f_prog * 1.0   # renforcé
        + f_place * 1.0   # renforcé
        + f_tx_vict * 1.2   # renforcé
        # f_marche et f_drift supprimés
        + f_classe * 1.3   # renforcé — niveau réel crucial
        + f_perf_rel * 0.8   # renforcé
        + f_rk * 1.5   # renforcé — RK trot signal fort
        + f_fraich * 0.6
        + f_partants * 0.4
        + f_gains_an * 0.5
        + f_sexe * 0.3
        + f_deferre * 0.5
        + f_poids * 0.6
        + f_oeil * 0.5
        + f_trainer * 0.5
        + f_ecurie * 0.4
        + f_jockey * 0.5
        + f_age * 0.5
        + f_corde * 0.4
        + f_obs * 0.5
        + f_gains_c * 0.6
        + f_recul * 0.7
        + f_dist * 0.8            # distance optimale
        + f_piste * 0.7           # historique sur la piste
        + f_terrain * 0.8         # terrain préféré (plat/obstacle)
        + f_combo * 0.7           # combo jockey+entraîneur
        + f_reg_cond * 0.6        # régularité par conditions
        # 11 NOUVEAUX CRITÈRES
        + f_trot_etr    * 0.7     # trotteur étranger = souvent meilleur
        + f_psf         * 0.7     # spécialiste PSF
        + f_ligne_dr    * 0.6     # spécialiste ligne droite
        + f_cat_dist    * 0.8     # catégorie distance naturelle
        + f_sexe_disc   * 0.7     # sexe calibré par discipline
        + f_spec_obs    * 0.6     # spécialiste haies vs steeple
        + f_rentree_pl  * 0.6     # rentrée plat → cible obstacle
        + f_debut_hcap  * 0.5     # débutant handicap sous-évalué
        + f_saison_conf * 0.6     # forme saisonnière confirmée
        + f_fav_rec     * 0.6     # favori régulier récent
        + f_corde_af    * 0.7     # corde affinée par hippodrome
        # CORRECTIFS NIVEAU — musique pondérée par niveau réel
        + f_forme_niv   * 1.0     # forme ajustée au niveau de la course
        + f_etranger    * 0.7     # malus courses étrangères niveau faible
        + f_gonfiee     * 0.9     # malus musique gonflée par petites courses
        + f_classe_af   * 1.0     # classe vs niveau moyen du champ
        # BLOC C — historique temporel
        + f_vict_mois   * 0.5     # saisonnalité / forme cette saison
        + f_serie_pod   * 0.7     # série podiums sans victoire (sous-coté)
        + f_baisse_cl   * 0.9     # baisse de classe (fort signal)
        + f_mission     * 0.8     # cheval en mission (signal composite)
        + f_hiver       * 0.6     # pénalité reprise hivernale
        # BLOC B — jockey/entraîneur enrichis
        + f_jock_hippo  * 0.7     # jockey sur cet hippodrome
        + f_train_disc  * 0.6     # entraîneur dans cette discipline
        + f_combo_enr   * 0.8     # combo jockey+entraîneur enrichi
        + f_gazole      * 0.7     # signal gazole (déplacement loin)
        + f_tendance3m  * 0.7     # tendance 3 mois
        + f_repos_opt   * 0.5     # intervalle repos optimal
        + f_prepa       * 0.6     # course de préparation détectée
        # BLOC F
        + f_fiab_hippo  * 0.8     # fiabilité hippodrome
        + f_corde_hippo * 0.6     # avantage corde par hippodrome
        + f_jument_fav  * 0.7     # pénalité jument favorite
        + f_premier_def * 0.6     # pénalité 1er déferré jeune
        + f_course_serie* 0.4     # bonus course de série
        + f_dist_affinee* 0.9     # distance affinée ±50m
        + f_meteo_af    * 0.8     # météo/terrain affiné
        + f_driver_qual * 0.9     # qualité jockey/driver
        + f_trainer_qual* 0.7     # qualité entraîneur
    )

    details = {
        "forme":    round(f_forme, 1),
        "reg":      round(f_reg, 1),
        "prog":     round(f_prog, 1),
        "place":    round(f_place, 1),
        "marche":   round(f_marche, 1),
        "drift":    round(f_drift, 1),
        "classe":   round(f_classe, 1),
        "perf_rel": round(f_perf_rel, 1),
        "rk":       round(f_rk, 1),
        "fraich":   round(f_fraich, 1),
        "partants": round(f_partants, 1),
        "gains_an": round(f_gains_an, 1),
        "deferre":  round(f_deferre, 1),
        "jockey":   round(f_jockey, 1),
        "trainer":  round(f_trainer, 1),
        "poids":    round(f_poids, 1),
        "oeil":     round(f_oeil, 1),
        "dist":     round(f_dist, 1),
        "piste":    round(f_piste, 1),
        "terrain":  round(f_terrain, 1),
        "combo":    round(f_combo, 1),
        "reg_cond":     round(f_reg_cond, 1),
        # 11 NOUVEAUX CRITÈRES
        "trot_etr":     round(f_trot_etr, 1),
        "psf":          round(f_psf, 1),
        "ligne_dr":     round(f_ligne_dr, 1),
        "cat_dist":     round(f_cat_dist, 1),
        "sexe_disc":    round(f_sexe_disc, 1),
        "spec_obs":     round(f_spec_obs, 1),
        "rentree_pl":   round(f_rentree_pl, 1),
        "debut_hcap":   round(f_debut_hcap, 1),
        "saison_conf":  round(f_saison_conf, 1),
        "fav_rec":      round(f_fav_rec, 1),
        "corde_af":     round(f_corde_af, 1),
        # CORRECTIFS NIVEAU
        "forme_niv":    round(f_forme_niv, 1),
        "etranger":     round(f_etranger, 1),
        "gonfiee":      round(f_gonfiee, 1),
        "classe_af":    round(f_classe_af, 1),
        # BLOC C
        "vict_mois":    round(f_vict_mois, 1),
        "serie_pod":    round(f_serie_pod, 1),
        "baisse_cl":    round(f_baisse_cl, 1),
        "mission":      round(f_mission, 1),
        "is_mission":   is_mission,
        "hiver":        round(f_hiver, 1),
        # BLOC B
        "jock_hippo":   round(f_jock_hippo, 1),
        "train_disc":   round(f_train_disc, 1),
        "combo_enr":    round(f_combo_enr, 1),
        "gazole":       round(f_gazole, 1),
        "tendance3m":   round(f_tendance3m, 1),
        "repos_opt":    round(f_repos_opt, 1),
        "prepa":        round(f_prepa, 1),
        # BLOC F
        "hippo_fiab":   round(f_fiab_hippo, 1),
        "hippo_couleur": hippo_couleur,
        "corde_hippo":  round(f_corde_hippo, 1),
        "jument_fav":   round(f_jument_fav, 1),
        "premier_def":  round(f_premier_def, 1),
        "serie":        round(f_course_serie, 1),
        "dist_affinee": round(f_dist_affinee, 1),
        "meteo_af":     round(f_meteo_af, 1),
        "driver_qual":  round(f_driver_qual, 1),
        "trainer_qual": round(f_trainer_qual, 1),
    }
    return logit, cote, details

# ============= ANALYSE INTRA-COURSE =======
def analyse_course(partants, course):
    """
    Analyse tous les chevaux d'une course ensemble.
    1. Calcule les logits bruts
    2. Applique un softmax → probabilités modèle
    3. Compare aux probabilités implicites du marché
    4. Value = prob_modele - prob_marche
    """
    disc    = detect_discipline(course)
    logits  = []
    donnees = []

    for ch in partants:
        try:
            logit, cote, details = compute_logit(ch, course, partants, disc)
            logits.append(logit)
            donnees.append({
                "cheval":  ch,
                "cote":    cote,
                "details": details,
                "logit":   logit,
            })
        except Exception as e:
            logits.append(0)
            donnees.append({
                "cheval":  ch,
                "cote":    None,
                "details": {},
                "logit":   0,
                "erreur":  str(e),
            })

    # ── NOUVEAU CALCUL — 100% indépendant des cotes ─────────────────
    # Confiance = rang du cheval dans le champ par logit brut
    # 1er sur 10 partants = 90%, 2e = 80%... totalement stable.
    # Prob. modèle = logit normalisé entre 0 et 100% (min-max scaling).
    # Plus aucune cote dans ce calcul.

    # Détecter un champ à majorité de débutants (modèle peu fiable)
    nb_avec_musique = sum(
        1 for p in partants if parse_musique(p.get("musique"))
    )
    champ_inconnu = nb_avec_musique < len(partants) * 0.5

    # Normalisation logit min-max pour la prob. modèle
    logits_valides = [d["logit"] for d in donnees if not d.get("erreur")]
    min_logit = min(logits_valides) if logits_valides else 0
    max_logit = max(logits_valides) if logits_valides else 1
    range_logit = max_logit - min_logit if max_logit != min_logit else 1

    # Rang par logit pour la confiance
    logits_sorted = sorted(logits_valides, reverse=True)
    nb_partants = len(logits_valides)

    resultats = []
    for d in donnees:
        if "erreur" in d:
            continue
        ch = d["cheval"]
        logit = d["logit"]

        # Prob. modèle = position normalisée du logit (0-100%)
        prob = round((logit - min_logit) / range_logit * 100, 1)

        # Confiance = rang inversé (1er = haute confiance)
        rang = logits_sorted.index(logit) + 1 if logit in logits_sorted else nb_partants
        confiance = round((1 - (rang - 1) / nb_partants) * 100, 1)
        if champ_inconnu:
            confiance = round(confiance * 0.7, 1)

        # Cote conservée pour info et paris conseillés uniquement
        cote = d["cote"]
        prob_mkt = round(1 / cote * 100, 1) if cote and cote > 0 else 0

        entraineur_obj = ch.get("entraineur") or {}
        entraineur_id  = entraineur_obj.get("id") if isinstance(entraineur_obj, dict) else None

        resultats.append({
            "nom":           ch.get("nom", "?"),
            "num":           ch.get("numPmu"),
            "discipline":    disc,
            "prob":          prob,
            "prob_mkt":      prob_mkt,
            "value":         0,
            "confiance":     confiance,
            "cote":          cote,
            "logit":         round(logit, 1),
            "details":       d["details"],
            "champ_inconnu": champ_inconnu,
            "entraineur_id": entraineur_id,
        })

    return resultats

# =============== STREAMLIT UI =============
# Cache sélections du jour
def cache_key_today():
    return f"sel_{datetime.now().strftime('%Y%m%d')}"
def get_cached_sel():
    return st.session_state.get(cache_key_today())
def set_cached_sel(data):
    st.session_state[cache_key_today()] = data

st.set_page_config(
    page_title="🏇 Benter PMU",
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

st.title("🏇 Benter PMU — Sélections du jour")
st.caption(datetime.now().strftime("%A %d %B %Y"))

with st.sidebar:
    st.header("⚙️ Filtres")
    disc_filtre = st.multiselect(
        "Disciplines",
        ["TROT", "PLAT", "OBSTACLE"],
        default=["TROT", "PLAT", "OBSTACLE"]
    )
    if st.button("🗑️ Recalculer", use_container_width=True, help="Vider le cache et recalculer"):
        k = cache_key_today()
        if k in st.session_state: del st.session_state[k]
        st.rerun()
    min_score  = st.slider("Score Benter minimum", 0, 150, 30,
                           help="Jouer uniquement si le score objectif dépasse ce seuil")
    max_sel    = st.slider("Sélections max", 1, 10, MAX_SELECTIONS)
    min_conf   = 0
    min_value  = 0
    min_cote   = 0
    st.divider()
    st.caption("**Architecture : Benter-style**")
    st.caption("• Logits → Softmax → Probabilités relatives dans le champ")
    st.caption("• Tri par score objectif — cote exclue du calcul")
    st.caption("**Critères (52)**")
    st.caption("**Forme :** Forme · Régularité · Progression · Ratio placé · Taux victoires")
    st.caption("**Niveau :** Classe · Perf. relative · Baisse de classe · Musique/niveau")
    st.caption("**Physique :** RK (trot) · Déferré · Poids · Œillères · Âge · Corde · Recul")
    st.caption("**Connexion :** Jockey · Entraîneur · Combo J+E · Écurie")
    st.caption("**🆕 Bloc B :** Jockey/hippo · Trainer/discipline · Combo enrichi · Spécialiste piste★ · Tendance 3 mois · Repos optimal · Course prépa")
    st.caption("**🆕 Bloc C :** Saisonnalité · Série podiums · Baisse de classe · Signal mission 🎯 · Reprise hivernale")
    st.caption("**🔧 Correctifs niveau :** Forme/niveau course · Malus étranger · Musique gonflée · Classe vs champ")
    st.caption("**🆕 Blocs D/E :** Trotteur étranger★ · PSF · Ligne droite · Catégorie distance · Sexe/discipline · Haies vs steeple · Prépa obstacle · Débutant hcap · Saison★ · Favori récent · Corde affinée")
    st.caption("**🆕 Bloc F :** Fiabilité hippo 🟢🟠🔴 · Corde/hippo · Jument fav. · Distance ±50m · Météo/terrain · Driver★ · Trainer★ · Course série")

def hippo_badge(couleur):
    if couleur == "VERT":   return "🟢"
    if couleur == "ORANGE": return "🟠"
    if couleur == "ROUGE":  return "🔴"
    return "⚪"

def pill(label, val):
    if val is None or val == 0:
        return f'<span class="detail-pill">{label} 0</span>'
    cls  = "pos" if val > 0 else "neg"
    sign = "+" if val > 0 else ""
    return f'<span class="detail-pill {cls}">{label} {sign}{val}</span>'

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

    candidats       = []
    erreurs_api     = []
    erreurs_analyse = []
    bar = st.progress(0, text="Analyse des partants…")

    for i, (r, c) in enumerate(courses):
        bar.progress((i + 1) / len(courses), text=f"Course {i+1}/{len(courses)}…")

        # Filtre France uniquement
        pays_r = r.get("pays") or {}
        code_pays = pays_r.get("code","") if isinstance(pays_r,dict) else str(pays_r)
        if code_pays and code_pays.upper() not in ["FRA","FRANCE",""]:
            continue

        # Filtre courses à réclamer — ambitions des entraîneurs trop variables
        libelle_c = str(c.get("libelle") or c.get("conditions") or "").upper()
        if "RECLAM" in libelle_c or "CLAIMING" in libelle_c:
            continue

        # Filtre 2-3 ans au trot — musique trop courte pour être fiable
        disc_c_check = detect_discipline(c)
        cond_age = str(c.get("conditionAge") or c.get("conditions") or libelle_c).upper()
        if disc_c_check == "TROT" and any(x in cond_age for x in ["2 ANS","3 ANS","2ANS","3ANS","DEUX ANS","TROIS ANS"]):
            continue

        num_r = r.get("numOfficiel") or r.get("numOrdre") or r.get("numeroReunion") or r.get("num")
        num_c = c.get("numOfficiel") or c.get("numOrdre") or c.get("numCourse") or c.get("num")
        if not num_r or not num_c:
            continue
        try:
            pdata    = pmu_get(f"/{today()}/R{num_r}/C{num_c}/participants")
            partants = pdata.get("participants") or []
        except Exception as e:
            erreurs_api.append(f"R{num_r}C{num_c} — {e}")
            continue

        try:
            resultats = analyse_course(partants, c)
            hippo     = (r.get("hippodrome") or {}).get("nom") or "?"
            for res in resultats:
                res["course"] = f"R{num_r}C{num_c}"
                res["hippo"]  = hippo
            candidats.extend(resultats)
        except Exception as e:
            erreurs_analyse.append(f"R{num_r}C{num_c} — {type(e).__name__}: {e}")

    bar.empty()

    if erreurs_api or erreurs_analyse:
        with st.expander(f"⚠️ Diagnostics ({len(erreurs_api)} erreurs API · {len(erreurs_analyse)} erreurs analyse)"):
            for e in erreurs_api[:5]:
                st.caption(f"• API: {e}")
            for e in erreurs_analyse[:5]:
                st.caption(f"• Analyse: {e}")

    # Filtre Score Benter minimum
    gardes_brutes = [
        c for c in candidats
        if c["logit"] >= min_score
        and c.get("cote")
    ]

    # Déduplication écurie : si un même entraîneur a 2+ chevaux dans
    # la même course, on ne garde que celui avec le meilleur score
    # (l'autre sert souvent de "lièvre" pour emmener le train).
    vus_ecurie = {}
    for c in gardes_brutes:
        eid = c.get("entraineur_id")
        course_key = c.get("course", "")
        if eid:
            cle = (course_key, eid)
            if cle not in vus_ecurie or c["logit"] > vus_ecurie[cle]["logit"]:
                vus_ecurie[cle] = c
        else:
            vus_ecurie[(course_key, c.get("num"))] = c

    gardes_dedup_ecurie = sorted(vus_ecurie.values(), key=lambda x: x["logit"], reverse=True)

    # Diversification : 1 seule sélection par course maximum.
    # Les autres bons chevaux de la même course se retrouveront
    # naturellement dans les "partenaires" des paris conseillés.
    courses_vues = set()
    gardes = []
    for c in gardes_dedup_ecurie:
        course_key = c.get("course", "")
        if course_key in courses_vues:
            continue
        courses_vues.add(course_key)
        gardes.append(c)
        if len(gardes) >= max_sel:
            break

    # Calcul de la mise Kelly pour chaque garde
    # Fi = (Pi*Ri - 1) / (Ri - 1)  si Pi > 1/Ri, sinon ne pas jouer
    BUDGET_KELLY = 100  # base 100€ pour le calcul, affiché en %
    for c in gardes:
        cote_k = c.get("cote")
        prob_k = (c.get("prob") or 0) / 100  # prob modèle en proportion
        if cote_k and cote_k > 1 and prob_k > 1 / cote_k:
            ri = cote_k
            fi = (prob_k * ri - 1) / (ri - 1)
            fi = max(0, min(fi, 0.25))  # plafonné à 25% du budget par sécurité
            c["kelly_pct"] = round(fi * 100, 1)
        else:
            c["kelly_pct"] = 0
    # Figer les sélections pour la journée (stable malgré fluctuations cotes)
    set_cached_sel({"gardes": gardes, "candidats": candidats, "courses": courses, "heure": datetime.now().strftime("%H:%M")})
    st.session_state["gardes_du_jour"] = gardes

    st.divider()

    if not gardes:
        st.warning("❌ Aucun pari aujourd'hui — aucun cheval ne passe les filtres.")
        top3 = sorted([c for c in candidats if c.get("cote")], key=lambda x: x["logit"], reverse=True)[:3]
        if top3:
            st.caption("Top 3 des meilleures values aujourd'hui (hors filtres) :")
            for c in top3:
                st.caption(f"• N°{c['num']} {c['nom']} ({c.get('course','?')}) — Score Benter {c['logit']} · Cote {c.get('cote','?')}")
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 10
        st.success(f"🔥 {len(gardes)} sélection(s) du jour")

        for i, c in enumerate(gardes):
            is_fort = c["logit"] >= 60
            d       = c.get("details", {})
            badge   = '<span class="badge-fort">🔥 PARI FORT</span>' if is_fort else '<span class="badge-value">⚡ VALUE</span>'
            if d.get("is_mission"):
                badge += ' <span style="background:#1a1a3d;color:#a78bfa;padding:3px 8px;border-radius:5px;font-size:0.75rem;font-weight:700;display:inline-block;margin:2px;">🎯 EN MISSION</span>'
            if c.get("champ_inconnu"):
                badge += ' <span style="background:#1a2a1a;color:#86efac;padding:3px 10px;border-radius:6px;font-size:0.78rem;font-weight:700;display:inline-block;margin-top:6px;">⚠️ Champ d\'inconnus</span>'


            couleur_hippo = d.get("hippo_couleur", "INCONNU")
            badge_hippo   = hippo_badge(couleur_hippo)
            pills = "".join([
                pill("Forme",     d.get("forme")),
                pill("Rég.",      d.get("reg")),
                pill("Prog.",     d.get("prog")),
                # Marché et Drift retirés du scoring
                pill("Classe",    d.get("classe")),
                pill("Perf.rel.", d.get("perf_rel")),
                pill("RK",        d.get("rk")),
                pill("Distance",  d.get("dist_affinee") or d.get("dist")),
                pill("Piste",     d.get("piste")),
                pill("Terrain",   d.get("meteo_af") or d.get("terrain")),
                pill("Combo",     d.get("combo_enr") or d.get("combo")),
                pill("Rég.cond.", d.get("reg_cond")),
                pill("Fraîcheur", d.get("repos_opt") or d.get("fraich")),
                pill("Déferré",   d.get("deferre")),
                pill("Jockey",    d.get("jock_hippo") or d.get("jockey")),
                pill("Trainer",   d.get("train_disc") or d.get("trainer")),
                pill("Driver★",   d.get("driver_qual")),
                pill("Train.★",   d.get("trainer_qual")),
                pill("Spéc.★",   d.get("gazole")),
                pill("Tendance",  d.get("tendance3m")),
                pill("Prépa",     d.get("prepa")),
                pill("Poids",     d.get("poids")),
                pill("Œill.",     d.get("oeil")),
                pill("Série",     d.get("serie")),
                pill("Hippo",     d.get("hippo_fiab")),
                pill("Étr.trot",  d.get("trot_etr")),
                pill("PSF",       d.get("psf")),
                pill("L.droite",  d.get("ligne_dr")),
                pill("Cat.dist",  d.get("cat_dist")),
                pill("Sexe★",     d.get("sexe_disc")),
                pill("H/S obs",   d.get("spec_obs")),
                pill("Prépa.obs", d.get("rentree_pl")),
                pill("Déb.hcap",  d.get("debut_hcap")),
                pill("Saison★",   d.get("saison_conf")),
                pill("Fav.rec.",  d.get("fav_rec")),
                pill("Corde★",    d.get("corde_af")),
                pill("Saison",    d.get("vict_mois")),
                pill("Podiums",   d.get("serie_pod")),
                pill("Cl.↓",      d.get("baisse_cl")),
                pill("Mission",   d.get("mission")),
                pill("Hiver",     d.get("hiver")),
                pill("Niv.forme", d.get("forme_niv")),
                pill("Étranger",  d.get("etranger")),
                pill("Gonflée?",  d.get("gonfiee")),
                pill("Cl.champ",  d.get("classe_af")),
            ])

            hippo_str = f" · {c['hippo']}" if c.get("hippo") and c["hippo"] != "?" else ""
            st.markdown(f"""
            <div class="card">
                <div class="horse-name">{medals[i]} N°{c['num']} {c['nom']}</div>
                <div class="sub">{c['course']} · {c['discipline']}{hippo_str} {badge_hippo}</div>
                {badge}
                <div class="detail-grid">{pills}</div>
            </div>
            """, unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            col1.metric("Score Benter", f"{c['logit']}")
            col2.metric("Cote (info)", f"{c['cote']}×" if c.get('cote') else "—")
            kelly_val = c.get("kelly_pct", 0)
            col3.metric("Mise Kelly", f"{kelly_val}%" if kelly_val > 0 else "—",
                       help="% du budget à miser selon le critère de Kelly (plafonné à 25%)")

            # ── PARIS CONSEILLÉS ─────────────────────────────────
            # Trouver les partenaires pour la base (cote 4-22, value positive)
            course_id = c.get("course","")
            base_num  = c.get("num")
            partenaires = sorted(
                [
                    p for p in candidats
                    if p.get("course") == course_id
                    and p.get("num") != base_num
                    and p.get("cote") and 4 <= p["cote"] <= 22
                    and p.get("prob_mkt", 0) > 0.5
                ],
                key=lambda x: x.get("value", 0),
                reverse=True
            )[:4]

            if partenaires:
                base_str = f"N°{base_num} {c['nom'][:18]} ({c.get('cote','?')})"
                parts_couple = " / ".join([f"N°{p['num']} {p['nom'][:14]} ({p['cote']})" for p in partenaires[:3]])
                parts_trio   = " / ".join([f"N°{p['num']} {p['nom'][:14]} ({p['cote']})" for p in partenaires[:4]])
                st.markdown(f"""
                <div style="background:#0d1f0d;border:1px solid #1a3a1a;border-radius:10px;padding:14px;margin-bottom:8px">
                  <div style="font-size:11px;color:#4ade80;font-weight:800;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">🎯 Paris conseillés</div>
                  <div style="margin-bottom:10px">
                    <span style="color:#fbbf24;font-weight:800;font-size:13px">BASE : {base_str}</span>
                  </div>
                  <div style="margin-bottom:8px">
                    <span style="color:#60a5fa;font-weight:700;font-size:12px">🔗 Couplé Gagnant :</span><br/>
                    <span style="color:#e2e8f0;font-size:12px">Base / {parts_couple}</span>
                  </div>
                  <div>
                    <span style="color:#a78bfa;font-weight:700;font-size:12px">🎰 Trio :</span><br/>
                    <span style="color:#e2e8f0;font-size:12px">Base / {parts_trio}</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.caption("💡 Pas assez de partenaires (cote 4-22) dans cette course.")

            st.markdown("---")

    st.caption(f"Score Benter ≥ {min_score} · {len(candidats)} chevaux analysés · Cote = info seulement")
    st.caption(f"Tri : Score Benter objectif · Confiance ≥ {min_conf}% · Cote = information uniquement · {len(candidats)} chevaux analysés")

    # ========== JOURNAL DE RÉSULTATS ==========
    st.markdown("---")
    st.markdown("## 📓 Journal de résultats")

    journal = load_journal()

    tab_add, tab_stats = st.tabs(["➕ Enregistrer un résultat", "📊 Statistiques"])

    with tab_add:
        st.markdown("Après chaque course, note ici si ta sélection a gagné, été placée ou perdu.")

        # Sélections du jour disponibles pour pré-remplissage
        gardes_session = st.session_state.get("gardes_du_jour", [])
        choix_rapides  = ["— Saisie manuelle —"] + [
            f"N°{g['num']} {g['nom']} ({g['course']})" for g in gardes_session
        ]

        selection_rapide = st.selectbox(
            "Cheval du jour (pré-remplissage automatique)",
            choix_rapides
        )

        # Trouver le cheval sélectionné
        garde_sel = None
        if selection_rapide != "— Saisie manuelle —":
            idx = choix_rapides.index(selection_rapide) - 1
            if 0 <= idx < len(gardes_session):
                garde_sel = gardes_session[idx]

        with st.form("form_journal", clear_on_submit=True):
            col_a, col_b = st.columns(2)
            date_val   = col_a.date_input("Date", value=date.today())
            course_val = col_b.text_input(
                "Course",
                value=garde_sel["course"] + " " + garde_sel.get("discipline","") if garde_sel else "",
                placeholder="ex: R2C3 TROT Vincennes"
            )

            col_c, col_d, col_e = st.columns(3)
            cheval_val = col_c.text_input(
                "Cheval",
                value=garde_sel["nom"] if garde_sel else "",
                placeholder="ex: TOKAIDO"
            )
            num_val  = col_d.number_input(
                "N°", min_value=1, max_value=30,
                value=int(garde_sel["num"]) if garde_sel else 1, step=1
            )
            cote_val = col_e.number_input(
                "Cote (×)", min_value=1.0,
                value=float(garde_sel["cote"]) if garde_sel and garde_sel.get("cote") else 5.0,
                step=0.5
            )

            resultat_val = st.radio(
                "Résultat", ["gagné", "placé", "perdu"], horizontal=True
            )

            submitted = st.form_submit_button("💾 Enregistrer", use_container_width=True)
            if submitted:
                if not cheval_val.strip():
                    st.warning("Indique le nom du cheval.")
                else:
                    new_entry = {
                        "id":       str(int(time.time())),
                        "date":     str(date_val),
                        "course":   course_val.strip(),
                        "cheval":   cheval_val.strip().upper(),
                        "num":      int(num_val),
                        "cote":     float(cote_val),
                        "resultat": resultat_val,
                        "details":  garde_sel.get("details", {}) if garde_sel else {},
                        "value":    garde_sel.get("value", 0) if garde_sel else 0,
                        "prob":     garde_sel.get("prob", 0) if garde_sel else 0,
                    }
                    updated = journal + [new_entry]
                    ok, err = save_journal(updated)
                    if ok:
                        st.success(f"✅ {new_entry['cheval']} — {resultat_val} enregistré !")
                        load_journal.clear()
                    else:
                        st.error(f"❌ Sauvegarde impossible : {err or 'token GitHub manquant sur Streamlit Cloud'}")

    with tab_stats:
        if not journal:
            st.info("Aucun résultat enregistré pour l'instant — commence par ajouter des entrées.")
        else:
            # ---- Stats globales ----
            s = journal_stats(journal)
            roi_color = "green" if s["roi"] >= 0 else "red"
            st.markdown(f"""
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px">
              <div style="background:#1e1e2e;border-radius:12px;padding:16px 24px;text-align:center;min-width:110px">
                <div style="font-size:1.6rem;font-weight:800">{s['total']}</div>
                <div style="color:#94a3b8;font-size:0.8rem">Sélections</div>
              </div>
              <div style="background:#1e1e2e;border-radius:12px;padding:16px 24px;text-align:center;min-width:110px">
                <div style="font-size:1.6rem;font-weight:800;color:#4ade80">{s['taux_vict']}%</div>
                <div style="color:#94a3b8;font-size:0.8rem">Taux de victoire</div>
              </div>
              <div style="background:#1e1e2e;border-radius:12px;padding:16px 24px;text-align:center;min-width:110px">
                <div style="font-size:1.6rem;font-weight:800;color:#60a5fa">{s['taux_place']}%</div>
                <div style="color:#94a3b8;font-size:0.8rem">Taux de place</div>
              </div>
              <div style="background:#1e1e2e;border-radius:12px;padding:16px 24px;text-align:center;min-width:110px">
                <div style="font-size:1.6rem;font-weight:800;color:{roi_color}">{s['roi']:+.1f}%</div>
                <div style="color:#94a3b8;font-size:0.8rem">ROI / mise</div>
              </div>
              <div style="background:#1e1e2e;border-radius:12px;padding:16px 24px;text-align:center;min-width:110px">
                <div style="font-size:1.6rem;font-weight:800;color:{roi_color}">{s['roi_abs']:+.2f}u</div>
                <div style="color:#94a3b8;font-size:0.8rem">Bénéfice net</div>
              </div>
            </div>
            <div style="color:#64748b;font-size:0.8rem">
              ✅ {s['wins']} gagné · 🏅 {s['places']} placé · ❌ {s['pertes']} perdu
            </div>
            """, unsafe_allow_html=True)

            # ---- Stats par semaine ----
            st.markdown("### Par semaine")
            semaines = {}
            for e in journal:
                try:
                    d_obj = datetime.strptime(e["date"], "%Y-%m-%d").date()
                    # Lundi de la semaine ISO
                    lundi = d_obj - __import__('datetime').timedelta(days=d_obj.weekday())
                    key   = str(lundi)
                except Exception:
                    key = "?"
                semaines.setdefault(key, []).append(e)

            # Gérer la suppression d'une entrée
            if "supprimer_id" in st.session_state and st.session_state["supprimer_id"]:
                id_a_sup = st.session_state.pop("supprimer_id")
                journal_modifie = [e for e in journal if e.get("id") != id_a_sup]
                ok, err = save_journal(journal_modifie)
                if ok:
                    load_journal.clear()
                    st.rerun()
                else:
                    st.error(f"Erreur suppression : {err}")

            for semaine in sorted(semaines.keys(), reverse=True):
                es = semaines[semaine]
                ss = journal_stats(es)
                roi_c = "🟢" if ss["roi"] >= 0 else "🔴"
                label = f"Semaine du {semaine}"
                with st.expander(f"{roi_c} {label} — {ss['total']} sél. · ROI {ss['roi']:+.1f}%"):
                    for e in sorted(es, key=lambda x: x.get("date",""), reverse=True):
                        icon = "✅" if e["resultat"] == "gagné" else ("🏅" if e["resultat"] == "placé" else "❌")
                        col_txt, col_btn = st.columns([5, 1])
                        col_txt.markdown(
                            f"{icon} **{e.get('cheval','?')}** (N°{e.get('num','?')}) "
                            f"— {e.get('course','?')} · {e.get('cote','?')}× "
                            f"· {e.get('date','')}"
                        )
                        entry_id = e.get("id", "")
                        if col_btn.button("🗑️", key=f"del_{entry_id}", help="Supprimer cette entrée"):
                            st.session_state["supprimer_id"] = entry_id
                            st.rerun()

            # ---- Analyse par critère ----
            entrees_avec_details = [e for e in journal if e.get("details")]
            if entrees_avec_details:
                st.markdown("### 🔬 Analyse par critère")
                st.caption(
                    f"Sur {len(entrees_avec_details)} sélection(s) avec données de critères. "
                    "Taux de victoire quand ce critère était positif (> 0)."
                )

                # Tous les critères présents dans les entrées
                tous_criteres = {
                    "forme": "Forme", "reg": "Régularité", "prog": "Progression",
                    "marche": "Marché", "drift": "Drift cote", "classe": "Classe",
                    "perf_rel": "Perf. relative", "rk": "RK trot",
                    "dist": "Distance", "piste": "Piste", "terrain": "Terrain",
                    "combo": "Combo connexion", "reg_cond": "Rég. conditions",
                    "fraich": "Fraîcheur", "deferre": "Déferré",
                    "jockey": "Jockey", "trainer": "Entraîneur",
                    "poids": "Poids", "oeil": "Œillères",
                }

                lignes = []
                for key, label_c in tous_criteres.items():
                    avec = [e for e in entrees_avec_details if (e["details"].get(key) or 0) > 0]
                    if len(avec) < 2:
                        continue
                    wins_avec = sum(1 for e in avec if e["resultat"] == "gagné")
                    taux = round(wins_avec / len(avec) * 100, 1)
                    roi_c_abs = sum(
                        (e.get("cote", 1) - 1) if e["resultat"] == "gagné"
                        else (0 if e["resultat"] == "placé" else -1)
                        for e in avec
                    )
                    roi_c_pct = round(roi_c_abs / len(avec) * 100, 1)
                    lignes.append((label_c, len(avec), taux, roi_c_pct))

                if lignes:
                    # Trier par taux de victoire décroissant
                    lignes.sort(key=lambda x: x[2], reverse=True)
                    rows_html = ""
                    for label_c, n, taux, roi_c_pct in lignes:
                        bar_w  = int(taux)
                        bar_col = "#4ade80" if taux >= 30 else ("#facc15" if taux >= 15 else "#f87171")
                        roi_col = "#4ade80" if roi_c_pct >= 0 else "#f87171"
                        rows_html += f"""
                        <tr>
                          <td style="padding:6px 10px;color:#e2e8f0">{label_c}</td>
                          <td style="padding:6px 10px;color:#94a3b8;text-align:center">{n}</td>
                          <td style="padding:6px 10px">
                            <div style="background:#374151;border-radius:4px;height:14px;width:120px">
                              <div style="background:{bar_col};border-radius:4px;height:14px;width:{bar_w}%"></div>
                            </div>
                            <span style="color:{bar_col};font-size:0.8rem;font-weight:700">{taux}%</span>
                          </td>
                          <td style="padding:6px 10px;color:{roi_col};font-weight:700;text-align:right">{roi_c_pct:+.1f}%</td>
                        </tr>"""

                    st.markdown(f"""
                    <table style="width:100%;border-collapse:collapse;background:#111827;border-radius:12px;overflow:hidden">
                      <thead>
                        <tr style="background:#1e293b">
                          <th style="padding:8px 10px;text-align:left;color:#94a3b8;font-size:0.8rem">Critère</th>
                          <th style="padding:8px 10px;text-align:center;color:#94a3b8;font-size:0.8rem">N</th>
                          <th style="padding:8px 10px;text-align:left;color:#94a3b8;font-size:0.8rem">Taux victoire</th>
                          <th style="padding:8px 10px;text-align:right;color:#94a3b8;font-size:0.8rem">ROI</th>
                        </tr>
                      </thead>
                      <tbody>{rows_html}</tbody>
                    </table>
                    """, unsafe_allow_html=True)
                    st.caption("⚠️ Données fiables après 20+ sélections. Avant ça, les % fluctuent beaucoup.")
            else:
                if journal:
                    st.info("📊 L'analyse par critère sera disponible après avoir enregistré des résultats depuis l'app (les critères sont automatiquement sauvegardés).")


    if False:  # Backtesting désactivé
        st.markdown("### 🔬 Backtesting — Validation du modèle sur données réelles")
        st.info("Rejoue notre modèle sur les courses passées et compare aux arrivées réelles via l'API PMU (3 dernières années disponibles).")

        col_b1, col_b2, col_b3 = st.columns(3)
        nb_jours_back = col_b1.slider("Jours à analyser", 3, 14, 7)
        min_val_back  = col_b2.slider("Value minimum (%)", 0, 20, 8)
        min_cote_back = col_b3.slider("Cote minimum", 1.0, 10.0, 3.5, step=0.5)

        if st.button("🚀 Lancer le backtesting", type="primary", use_container_width=True):
            import datetime as dt_mod
            resultats_back = []
            erreurs_back   = []
            bar_back = st.progress(0, text="Initialisation...")

            for j in range(1, nb_jours_back + 1):
                date_j   = (datetime.now() - dt_mod.timedelta(days=j)).strftime("%d%m%Y")
                date_aff = (datetime.now() - dt_mod.timedelta(days=j)).strftime("%d/%m/%Y")
                bar_back.progress(j / nb_jours_back, text=f"Analyse du {date_aff}...")

                try:
                    data_rj = pmu_get(f"/{date_j}/reunions")
                    reunions_j = (data_rj.get("programme") or {}).get("reunions") or data_rj.get("reunions") or []
                except Exception:
                    continue

                for r in reunions_j:
                    for c in (r.get("courses") or []):
                        nr = r.get("numOfficiel") or r.get("numOrdre") or r.get("num")
                        nc = c.get("numOfficiel") or c.get("numOrdre") or c.get("num")
                        if not nr or not nc: continue
                        try:
                            pdata_j    = pmu_get(f"/{date_j}/R{nr}/C{nc}/participants")
                            partants_j = pdata_j.get("participants") or []
                            if len(partants_j) < 3: continue

                            # Vérifier arrivée disponible
                            arrivee = sorted(
                                [(p.get("ordreArrivee"), p.get("numPmu"), p.get("nom","?"))
                                 for p in partants_j
                                 if isinstance(p, dict) and isinstance(p.get("ordreArrivee"), int) and p.get("ordreArrivee", 0) > 0]
                            )
                            if not arrivee: continue

                            # Appliquer modèle
                            disc_j  = detect_discipline(c)
                            logits_j, data_j2 = [], []
                            for ch in partants_j:
                                if not isinstance(ch, dict): continue
                                try:
                                    lg    = compute_logit(ch, c, partants_j, disc_j)
                                    cote2 = safe_float((ch.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(ch.get("coteInitiale"))
                                    logits_j.append(lg); data_j2.append({"ch":ch,"cote":cote2,"logit":lg})
                                except Exception:
                                    logits_j.append(0); data_j2.append({"ch":ch,"cote":None,"logit":0})

                            probs_j  = softmax([l/10 for l in logits_j])
                            pb_j     = [1/d["cote"] if d["cote"] and d["cote"]>0 else 0 for d in data_j2]
                            sp_j     = sum(pb_j)
                            pmkt_j   = [p/sp_j if sp_j>0 else 0 for p in pb_j]

                            # Candidats
                            cands_j = []
                            for d2, pm, pmkt in zip(data_j2, probs_j, pmkt_j):
                                if not d2["cote"]: continue
                                val_j = (pm - pmkt) * 100
                                if val_j >= min_val_back and d2["cote"] >= min_cote_back:
                                    cands_j.append({
                                        "nom":    d2["ch"].get("nom","?"),
                                        "num":    d2["ch"].get("numPmu"),
                                        "cote":   d2["cote"],
                                        "value":  round(val_j, 1),
                                        "prob":   round(pm*100, 1),
                                        "course": f"R{nr}C{nc}",
                                        "disc":   disc_j,
                                        "date":   date_aff,
                                        "hippo":  get_hippo_name(r, c),
                                    })
                            if not cands_j: continue
                            cands_j.sort(key=lambda x: x["value"], reverse=True)
                            sel = cands_j[0]

                            # Résultat réel
                            gagnant_num   = arrivee[0][1]
                            places_nums   = [a[1] for a in arrivee[:3]]
                            gagne  = sel["num"] == gagnant_num
                            place  = sel["num"] in places_nums
                            roi_j  = (sel["cote"] - 1) if gagne else (0 if place else -1)
                            label  = "✅ GAGNÉ" if gagne else ("🏅 PLACÉ" if place else "❌ PERDU")

                            resultats_back.append({
                                **sel, "gagne": gagne, "place": place,
                                "roi": roi_j, "label": label,
                                "gagnant": arrivee[0][2], "gagnant_num": gagnant_num,
                            })
                        except Exception as e:
                            erreurs_back.append(f"R{nr}C{nc} {date_aff}: {str(e)[:60]}")

            bar_back.empty()

            if not resultats_back:
                st.warning("Aucune sélection trouvée. Essaie de baisser les filtres ou d'augmenter le nombre de jours.")
            else:
                total_b  = len(resultats_back)
                wins_b   = sum(1 for r in resultats_back if r["gagne"])
                places_b = sum(1 for r in resultats_back if r["place"] and not r["gagne"])
                roi_abs  = sum(r["roi"] for r in resultats_back)
                roi_pct  = roi_abs / total_b * 100
                cote_moy = sum(r["cote"] for r in resultats_back) / total_b
                val_moy  = sum(r["value"] for r in resultats_back) / total_b
                rc       = "#4ade80" if roi_pct >= 0 else "#f87171"

                st.markdown(f"""
                <div style="display:flex;gap:12px;flex-wrap:wrap;margin:14px 0">
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800">{total_b}</div>
                    <div style="color:#94a3b8;font-size:0.75rem">Sélections</div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800;color:#4ade80">{wins_b/total_b*100:.1f}%</div>
                    <div style="color:#94a3b8;font-size:0.75rem">Taux victoire</div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800;color:#60a5fa">{(wins_b+places_b)/total_b*100:.1f}%</div>
                    <div style="color:#94a3b8;font-size:0.75rem">Taux place</div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800;color:{rc}">{roi_pct:+.1f}%</div>
                    <div style="color:#94a3b8;font-size:0.75rem">ROI simulé</div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800;color:{rc}">{roi_abs:+.2f}u</div>
                    <div style="color:#94a3b8;font-size:0.75rem">Gain net</div>
                  </div>
                  <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:80px">
                    <div style="font-size:1.5rem;font-weight:800;color:#f59e0b">{cote_moy:.1f}</div>
                    <div style="color:#94a3b8;font-size:0.75rem">Cote moy.</div>
                  </div>
                </div>
                <div style="color:#64748b;font-size:0.8rem;margin-bottom:12px">
                  Value moyenne : {val_moy:.1f}% · {wins_b}V / {places_b}P / {total_b-wins_b-places_b}L
                </div>
                """, unsafe_allow_html=True)

                # Courbe ROI
                cumul, courbe = 0, []
                for r in resultats_back:
                    cumul += r["roi"]; courbe.append(round(cumul,2))
                if len(courbe) > 1:
                    st.markdown("**Évolution ROI cumulé (unités)**")
                    st.line_chart({"Bankroll": courbe}, height=180)

                # Détail
                st.markdown("**Détail des sélections**")
                for r in resultats_back:
                    c1, c2 = st.columns([4,1])
                    c1.markdown(f"{r['label']} **{r['nom']}** N°{r['num']} · {r['course']} {r['disc']} · {r['hippo']} · {r['date']} · Cote {r['cote']} · Value {r['value']:+.1f}%")
                    if not r["gagne"]: c2.caption(f"→ {r['gagnant']}")

                if erreurs_back:
                    with st.expander(f"⚠️ {len(erreurs_back)} erreur(s)"):
                        for e in erreurs_back[:10]: st.caption(f"• {e}")

            st.caption("⚠️ Limites : cotes API PMU (clôture) · 1u/pari simulé · Courses sans arrivée ignorées · 3 ans d'historique max")
