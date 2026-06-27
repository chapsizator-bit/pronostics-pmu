import urllib.request
import json
import math
import re
from datetime import datetime, date
import streamlit as st

# ================= CONFIG =================
MAX_SELECTIONS = 3
MIN_PROB_EDGE  = 0.03   # avantage minimum sur le marché (3 points de proba)
MIN_CONF       = 60
TIMEOUT        = 12
BASE_URL       = "https://online.turfinfo.api.pmu.fr/rest/client/1/programme"

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

# ============= LOGIT BRUT PAR CHEVAL ======
def compute_logit(cheval, course, tous_partants, disc):
    """
    Calcule le logit brut d'un cheval = somme pondérée de ses features.
    Ces poids s'inspirent de la littérature (Benter 1994, Bolton & Chapman 1986).
    Le marché reçoit un poids fort (~35%) car très efficient en PMU.
    Pour les débutants (musique vide), le logit est ancré sur la cote.
    """
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
    f_marche  = clamp(1 / cote * 35, 0, 35) if cote else 0

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

    # Logit = somme pondérée (les poids reflètent l'importance relative)
    logit = (
        f_forme * 1.0
        + f_reg * 0.8
        + f_prog * 0.7
        + f_place * 0.7
        + f_tx_vict * 0.8
        + f_marche * 1.0          # fort poids marché
        + f_drift * 1.2           # mouvement de cote — signal fort
        + f_classe * 0.9          # indice de classe
        + f_perf_rel * 0.6
        + f_rk * 1.1              # RK normalisé — très pertinent en trot
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

    # Probabilités modèle (softmax sur les logits)
    probs_modele_raw = softmax(logits)

    # Plafond à 80% : aucun cheval ne peut avoir >80% de prob. modèle.
    # Si le softmax dépasse ce seuil (champ de débutants, etc.),
    # on redistribue le surplus proportionnellement aux autres.
    MAX_PROB = 0.80
    probs_modele = list(probs_modele_raw)
    for _ in range(10):   # itérations pour convergence
        total_sur = sum(max(0, p - MAX_PROB) for p in probs_modele)
        if total_sur < 1e-9:
            break
        sous_plafond = [i for i, p in enumerate(probs_modele) if p < MAX_PROB]
        if not sous_plafond:
            break
        redistrib = total_sur / len(sous_plafond)
        probs_modele = [min(p, MAX_PROB) for p in probs_modele]
        for i in sous_plafond:
            probs_modele[i] += redistrib

    # Détecter un champ à majorité de débutants (modèle peu fiable)
    nb_avec_musique = sum(
        1 for p in partants if parse_musique(p.get("musique"))
    )
    champ_inconnu = nb_avec_musique < len(partants) * 0.5

    # Probabilités marché (1/cote normalisées)
    cotes_brutes = [d["cote"] for d in donnees]
    probs_brutes = [1 / c if c and c > 0 else 0 for c in cotes_brutes]
    somme_brutes = sum(probs_brutes)
    probs_marche = [p / somme_brutes if somme_brutes > 0 else 0 for p in probs_brutes]

    resultats = []
    for d, pm, pmkt in zip(donnees, probs_modele, probs_marche):
        if "erreur" in d:
            continue
        ch      = d["cheval"]
        value   = round((pm - pmkt) * 100, 1)  # en points de %
        # Confiance réduite si le champ est majoritairement inconnu
        drift_bonus = d["details"].get("drift", 0)
        confiance   = round(clamp(pm * 100 + drift_bonus * 2, 0, 99), 1)
        if champ_inconnu:
            confiance = round(confiance * 0.6, 1)  # pénalité champ d'inconnus

        resultats.append({
            "nom":           ch.get("nom", "?"),
            "num":           ch.get("numPmu"),
            "discipline":    disc,
            "prob":          round(pm * 100, 1),
            "prob_mkt":      round(pmkt * 100, 1),
            "value":         value,
            "confiance":     confiance,
            "cote":          d["cote"],
            "logit":         round(d["logit"], 1),
            "details":       d["details"],
            "champ_inconnu": champ_inconnu,
        })

    return resultats

# =============== STREAMLIT UI =============
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
    min_value  = st.slider("Avantage marché minimum (%)", -10, 20, int(MIN_PROB_EDGE * 100))
    min_conf   = st.slider("Confiance minimum",           0, 99, MIN_CONF)
    max_sel    = st.slider("Sélections max",              1, 10, MAX_SELECTIONS)
    st.divider()
    st.caption("**Architecture : Benter-style**")
    st.caption("• Logits → Softmax → Probabilités relatives dans le champ")
    st.caption("• Value = prob. modèle − prob. marché")
    st.caption("**Critères (23)**")
    st.caption("Forme · Régularité · Progression · Ratio placé · Taux victoires")
    st.caption("Marché · Mouvement de cote · Indice de classe · Perf. relative")
    st.caption("RK normalisé (trot) · Fraîcheur · Partants · Gains/an")
    st.caption("Déferré · Jockey · Entraîneur · Poids · Œillères · Écurie")
    st.caption("Âge · Corde (plat) · Recul (trot) · Expérience (obstacle)")

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

    gardes = [
        c for c in candidats
        if c["value"] >= min_value
        and c["confiance"] >= min_conf
        and c.get("cote")
    ]
    gardes.sort(key=lambda x: (x["value"], x["confiance"]), reverse=True)
    gardes = gardes[:max_sel]

    st.divider()

    if not gardes:
        st.warning("❌ Aucun pari aujourd'hui — aucun cheval ne passe les filtres.")
        top3 = sorted(candidats, key=lambda x: x["value"], reverse=True)[:3]
        if top3:
            st.caption("Top 3 des meilleures values aujourd'hui (hors filtres) :")
            for c in top3:
                st.caption(
                    f"• N°{c['num']} {c['nom']} ({c['course']}) "
                    f"— Prob. modèle {c['prob']}% vs marché {c['prob_mkt']}% "
                    f"· Value {c['value']:+.1f}%"
                )
    else:
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 10
        st.success(f"🔥 {len(gardes)} sélection(s) du jour")

        for i, c in enumerate(gardes):
            is_fort = c["value"] >= 10 and c["confiance"] >= 75
            badge   = '<span class="badge-fort">🔥 PARI FORT</span>' if is_fort else '<span class="badge-value">⚡ VALUE</span>'
            if c.get("champ_inconnu"):
                badge += ' <span style="background:#1a2a1a;color:#86efac;padding:3px 10px;border-radius:6px;font-size:0.78rem;font-weight:700;display:inline-block;margin-top:6px;">⚠️ Champ d\'inconnus</span>'
            hippo   = f" · {c['hippo']}" if c.get("hippo") and c["hippo"] != "?" else ""
            d       = c.get("details", {})

            pills = "".join([
                pill("Forme",     d.get("forme")),
                pill("Rég.",      d.get("reg")),
                pill("Prog.",     d.get("prog")),
                pill("Marché",    d.get("marche")),
                pill("Drift",     d.get("drift")),
                pill("Classe",    d.get("classe")),
                pill("Perf.rel.", d.get("perf_rel")),
                pill("RK",        d.get("rk")),
                pill("Fraîcheur", d.get("fraich")),
                pill("Déferré",   d.get("deferre")),
                pill("Jockey",    d.get("jockey")),
                pill("Trainer",   d.get("trainer")),
                pill("Poids",     d.get("poids")),
                pill("Œill.",     d.get("oeil")),
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
            col1.metric("Prob. modèle",  f"{c['prob']}%")
            col2.metric("Prob. marché",  f"{c['prob_mkt']}%")
            col3.metric("Avantage",      f"{c['value']:+.1f}%")
            col4.metric("Cote",          f"{c['cote']}×" if c['cote'] else "—")
            st.markdown("---")

    st.caption(f"Filtres : value ≥ {min_value}% · confiance ≥ {min_conf}%")
    st.caption(f"{len(candidats)} chevaux analysés · {len(courses)} courses")
