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
        + f_dist * 0.8            # distance optimale
        + f_piste * 0.7           # historique sur la piste
        + f_terrain * 0.8         # terrain préféré (plat/obstacle)
        + f_combo * 0.7           # combo jockey+entraîneur
        + f_reg_cond * 0.6        # régularité par conditions
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

    # Probabilités modèle (softmax sur les logits avec température)
    # T=10 : compresse l'échelle des logits pour éviter qu'un seul critère
    # fort (ex: Forme +18) ne monopolise la softmax.
    # Sans température, un écart de +25 logit → prob > 99.99%.
    # Avec T=10, un bon cheval dans un champ ordinaire → 45-65%.
    TEMPERATURE = 10.0
    logits_tempered = [l / TEMPERATURE for l in logits]
    probs_modele_raw = softmax(logits_tempered)

    # Plafond à 70% : statistiquement, même le meilleur cheval gagne rarement
    # à plus de 65-70% dans une vraie course PMU.
    MAX_PROB = 0.70
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
    # Figer les sélections pour la journée (stable malgré fluctuations cotes)
    set_cached_sel({"gardes": gardes, "candidats": candidats, "courses": courses, "heure": datetime.now().strftime("%H:%M")})
    st.session_state["gardes_du_jour"] = gardes

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
                pill("Distance",  d.get("dist")),
                pill("Piste",     d.get("piste")),
                pill("Terrain",   d.get("terrain")),
                pill("Combo",     d.get("combo")),
                pill("Rég.cond.", d.get("reg_cond")),
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
