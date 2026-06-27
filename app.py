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
MIN_PROB_EDGE  = 0.03
MIN_CONF       = 60
TIMEOUT        = 12
BASE_URL       = "https://online.turfinfo.api.pmu.fr/rest/client/1/programme"

# ============== JOURNAL ===================
JOURNAL_RAW = "https://raw.githubusercontent.com/chapsizator-bit/pronostics-pmu/main/journal.json"
JOURNAL_API = "https://api.github.com/repos/chapsizator-bit/pronostics-pmu/contents/journal.json"

@st.cache_data(ttl=30)
def load_journal():
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
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        return False, "Token GitHub introuvable dans les secrets Streamlit"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    sha = None
    try:
        req = urllib.request.Request(JOURNAL_API, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            sha = json.loads(r.read()).get("sha")
    except Exception:
        pass
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
    if not entries:
        return {}
    total  = len(entries)
    wins   = sum(1 for e in entries if e.get("resultat") == "gagné")
    places = sum(1 for e in entries if e.get("resultat") == "placé")
    pertes = total - wins - places
    roi_abs = sum(
        (e.get("cote", 1) - 1) if e.get("resultat") == "gagné"
        else (0 if e.get("resultat") == "placé" else -1)
        for e in entries
    )
    return {
        "total": total, "wins": wins, "places": places, "pertes": pertes,
        "taux_vict":  round(wins / total * 100, 1),
        "taux_place": round((wins + places) / total * 100, 1),
        "roi":        round(roi_abs / total * 100, 1),
        "roi_abs":    round(roi_abs, 2),
    }

# ================= API ====================
def pmu_get(path):
    url = BASE_URL + path
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

def today():
    return datetime.now().strftime("%d%m%Y")

# =============== OUTILS ===================
def clamp(v, a, b): return max(a, min(v, b))
def dict_id(v): return v.get("id") if isinstance(v, dict) else None
def safe_float(v):
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except Exception: return None
def safe_int(v, default=0):
    try: return int(v)
    except Exception: return default

def detect_discipline(course):
    txt = str(course.get("discipline", "")).upper()
    if "ATTEL" in txt or "MONT" in txt or "TROT" in txt: return "TROT"
    if "HAIE" in txt or "STEEPLE" in txt or "CROSS" in txt: return "OBSTACLE"
    return "PLAT"

# =============== MUSIQUE ==================
def parse_musique(m):
    if not m: return []
    m = re.sub(r"\s", "", str(m))
    vals = []; i = 0
    while i < len(m):
        c = m[i]
        if c.isdigit():
            num = int(c)
            if i + 1 < len(m) and m[i+1].isdigit() and c != "0":
                num = int(c + m[i+1]); i += 1
            vals.append(("pos", num))
        elif c.lower() == "d": vals.append(("disq", None))
        elif c.lower() == "a": vals.append(("abs", None))
        i += 1
    return vals[:10]

# ============= SOFTMAX ====================
def softmax(scores):
    if not scores: return []
    max_s = max(scores)
    exps  = [math.exp(clamp(s - max_s, -30, 30)) for s in scores]
    total = sum(exps)
    return [e / total for e in exps] if total > 0 else [1 / len(scores)] * len(scores)

# ============= FEATURES ===================
def feat_forme(music):
    score = 0
    for i, (typ, pos) in enumerate(music):
        if typ != "pos": continue
        w = math.exp(-0.35 * i)
        if pos == 1:    score += 12 * w
        elif pos <= 3:  score += 8  * w
        elif pos <= 5:  score += 4  * w
        elif pos <= 8:  score += 1.5* w
    return clamp(score, 0, 20)

def feat_regularite(music):
    vals = [p for t, p in music if t == "pos"]
    if len(vals) < 4: return 0
    moy   = sum(vals) / len(vals)
    sigma = math.sqrt(sum((x - moy)**2 for x in vals) / len(vals))
    return clamp(8 - sigma, 0, 8)

def feat_progression(music):
    vals = [p for t, p in music if t == "pos"][:5]
    if len(vals) < 3: return 0
    n = len(vals); x_moy = (n-1)/2; y_moy = sum(vals)/n
    num = sum((i - x_moy) * (vals[i] - y_moy) for i in range(n))
    den = sum((i - x_moy)**2 for i in range(n))
    if den == 0: return 0
    pente = num / den
    if pente < -0.5: return 5
    elif pente < 0:  return 2
    elif pente > 0.5: return -2
    return 0

def feat_ratio_place(cheval):
    total  = safe_int(cheval.get("nombreCourses", 0))
    places = safe_int(cheval.get("nombrePlaces", 0)) or safe_int(cheval.get("nombrePlace", 0))
    if total < 3: return 0
    return clamp((places / total) * 8, 0, 8)

def feat_taux_victoires(cheval):
    total = safe_int(cheval.get("nombreCourses", 0))
    vict  = safe_int(cheval.get("nombreVictoires", 0))
    if total == 0: return 0
    return clamp((vict / total) * 12, 0, 10)

def feat_fraicheur(cheval):
    derniere = cheval.get("dateDerniereCourse") or cheval.get("derniereCourseDateFr")
    if not derniere: return 0
    try:
        if isinstance(derniere, (int, float)):
            d = datetime.fromtimestamp(derniere / 1000).date()
        else:
            raw = str(derniere)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try: d = datetime.strptime(raw[:10], fmt).date(); break
                except ValueError: continue
            else: return 0
        jours = (date.today() - d).days
        if jours < 0:          return 0
        if 14 <= jours <= 35:  return 4
        elif 7 <= jours < 14:  return 2
        elif 35 < jours <= 60: return 1
        elif jours > 90:       return -3
        elif jours > 60:       return -1
    except Exception: pass
    return 0

def feat_nb_partants(course):
    nb = safe_int(course.get("nombreDeclaresPartants", 0))
    if nb == 0:    return 0
    if nb <= 6:    return 3
    elif nb <= 9:  return 1
    elif nb <= 12: return 0
    elif nb <= 15: return -1
    else:          return -3

def feat_gains_annee(cheval):
    gains = safe_float(cheval.get("gainsAnneeEnCours") or cheval.get("gainsCourseAnneeCourante") or 0)
    if not gains: return 0
    return clamp(gains / 30000 * 5, 0, 5)

def feat_sexe(cheval):
    sexe = str(cheval.get("sexe") or cheval.get("indicateurSexe") or "").upper()
    return 2 if "HONG" in sexe or sexe == "H" else 0

def feat_entraineur(cheval, tous_partants):
    ent_id = dict_id(cheval.get("entraineur"))
    if not ent_id or not tous_partants: return 0
    victoires = sorties = 0
    for p in tous_partants:
        if dict_id(p.get("entraineur")) == ent_id:
            sorties += 1
            if parse_musique(p.get("musique")) and parse_musique(p.get("musique"))[0] == ("pos", 1):
                victoires += 1
    return round(clamp((victoires / sorties) * 8, 0, 5), 1) if sorties >= 3 else 0

def feat_forme_ecurie(cheval, tous_partants):
    prop_id = dict_id(cheval.get("proprietaire"))
    if not prop_id or not tous_partants: return 0
    for p in tous_partants:
        if dict_id(p.get("proprietaire")) == prop_id:
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1): return 3
    return 0

def feat_deferre(cheval, disc):
    raw = str(cheval.get("deferre") or cheval.get("incident") or "").upper()
    if not raw or raw in ["", "NONE", "0", "NON"]: return 0
    if "DAP" in raw or "4 PATTE" in raw or "QUATRE" in raw: return 4
    if "DA" in raw or "ANT" in raw: return 2
    if "DP" in raw or "POST" in raw: return 1 if disc != "TROT" else 0
    if "DEFER" in raw or raw == "D": return 2
    return 0

def feat_jockey(cheval, tous_partants):
    jockey_id = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    if not jockey_id or not tous_partants: return 0
    victoires = montes = 0
    for p in tous_partants:
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        if j == jockey_id:
            montes += 1
            if parse_musique(p.get("musique")) and parse_musique(p.get("musique"))[0] == ("pos", 1):
                victoires += 1
    return round(clamp((victoires / montes) * 10, 0, 6), 1) if montes >= 3 else 0

def feat_poids(cheval, disc):
    poids = safe_float(cheval.get("handicapPoids") or cheval.get("poidsConditionMonte"))
    if not poids: return 0
    ref  = {"PLAT": 57.0, "OBSTACLE": 65.0, "TROT": 0}
    base = ref.get(disc, 0)
    if base == 0: return 0
    ecart = poids - base
    if ecart > 4:   return -5
    elif ecart > 2: return -3
    elif ecart > 0: return -1
    elif ecart < -2: return 2
    return 0

def feat_oeilleres(cheval, disc):
    if disc == "TROT": return 0
    oeilleres = str(cheval.get("oeilleres") or cheval.get("equipement") or "").upper()
    if "PREMIER" in oeilleres or "1ER" in oeilleres: return 5
    elif "OEIL" in oeilleres or "OEI" in oeilleres: return 2
    return 0

def feat_recul_trot(cheval):
    recul = safe_float(cheval.get("handicapDistance") or cheval.get("distanceHandicap") or 0)
    if not recul or recul <= 0: return 0
    if recul <= 25:   return -1
    elif recul <= 50: return -2
    elif recul <= 75: return -4
    else:             return -6

def feat_age_disc(cheval, disc):
    age = safe_float(cheval.get("age"))
    if not age: return 0
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
    if disc != "PLAT": return 0
    num = safe_int(cheval.get("numPmu", 0))
    nb  = safe_int(course.get("nombreDeclaresPartants", 12)) or 12
    if num and nb > 0: return round(clamp((nb - num) / 2, 0, 6), 1)
    return 0

def feat_experience_obs(cheval, disc):
    if disc != "OBSTACLE": return 0
    total = safe_int(cheval.get("nombreCourses", 0))
    return round(clamp(math.log(total + 1) * 3, 0, 8), 1) if total else 0

def feat_gains_carriere_trot(cheval, disc):
    if disc != "TROT": return 0
    gains = safe_float(cheval.get("gainsCarriere", 0))
    return round(clamp(gains / 200000 * 8, 0, 8), 1) if gains else 0

def feat_reduction_km(cheval, tous_partants, disc):
    if disc != "TROT": return 0, None
    rk = safe_float(cheval.get("reductionKilometrique") or cheval.get("rkActuel"))
    if not rk: return 0, None
    rks = [safe_float(p.get("reductionKilometrique") or p.get("rkActuel")) for p in tous_partants]
    rks = [r for r in rks if r]
    if len(rks) < 2: return 0, rk
    min_rk, max_rk = min(rks), max(rks)
    if max_rk == min_rk: return 0, rk
    rang = (max_rk - rk) / (max_rk - min_rk)
    return round(clamp(rang * 12, 0, 12), 1), rk

def feat_mouvement_cote(cheval):
    """
    ─── CORRECTIF SÉLECTIONS INSTABLES ───────────────────────────────
    Le poids du drift de cote est réduit : il ne représente plus que
    1.0x au lieu de 1.2x dans le logit, et son amplitude est plafonnée
    à ±4 (au lieu de ±8). Cela empêche les fluctuations de cote en
    cours de journée de faire monter/descendre les sélections.
    ──────────────────────────────────────────────────────────────────
    """
    cote_init = safe_float(cheval.get("coteInitiale"))
    rapport   = cheval.get("dernierRapportDirect") or {}
    cote_fin  = safe_float(rapport.get("rapport")) if isinstance(rapport, dict) else None
    if not cote_init or not cote_fin or cote_init <= 0: return 0, None
    chute = (cote_init - cote_fin) / cote_init
    # Amplitude plafonnée à ±4 (était ±8)
    if chute > 0.30:    return 4, chute
    elif chute > 0.15:  return 2, chute
    elif chute > 0.05:  return 1, chute
    elif chute < -0.25: return -3, chute
    elif chute < -0.10: return -1, chute
    return 0, chute

def feat_classe(cheval, course):
    alloc = safe_float(course.get("allocation") or course.get("montantPrix") or course.get("totalOffert"))
    if not alloc: return 0
    gains    = safe_float(cheval.get("gainsCarriere", 0)) or 0
    nb_cours = safe_int(cheval.get("nombreCourses", 0))
    if nb_cours < 3 or not gains: return 0
    gain_moyen   = gains / nb_cours
    niveau_estim = gain_moyen / 0.25
    if niveau_estim <= 0: return 0
    ratio = alloc / niveau_estim
    if ratio < 0.4:    return 7
    elif ratio < 0.65: return 4
    elif ratio < 0.85: return 2
    elif ratio > 2.5:  return -5
    elif ratio > 1.5:  return -3
    elif ratio > 1.15: return -1
    return 0

def feat_performance_relative(cheval, tous_partants):
    total = safe_int(cheval.get("nombreCourses", 0))
    vict  = safe_int(cheval.get("nombreVictoires", 0))
    taux_vict = vict / total if total >= 5 else 0
    cote = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(cheval.get("coteInitiale"))
    if not cote or not tous_partants: return 0
    cotes_champ = [safe_float((p.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(p.get("coteInitiale")) for p in tous_partants]
    cotes_champ = [c for c in cotes_champ if c]
    if not cotes_champ: return 0
    cotes_champ.sort()
    rang = cotes_champ.index(min(cotes_champ, key=lambda x: abs(x - cote))) + 1
    ratio_rang = rang / len(cotes_champ)
    if taux_vict > 0.25 and ratio_rang > 0.5: return 4
    elif taux_vict > 0.15 and ratio_rang > 0.6: return 2
    return 0

def feat_distance_optimale(cheval, course):
    dist_auj = safe_float(course.get("distance") or course.get("distanceParcourue") or course.get("longueurPiste"))
    if not dist_auj: return 0
    dist_pref = safe_float(cheval.get("distancePrefere") or cheval.get("distanceOptimale"))
    if dist_pref:
        ecart = abs(dist_auj - dist_pref) / dist_pref
        if ecart < 0.08:   return 5
        elif ecart < 0.20: return 2
        elif ecart > 0.40: return -4
        return 0
    gpd = cheval.get("gainsParDistance") or cheval.get("gainsParTypeDistance")
    if isinstance(gpd, dict) and gpd:
        meilleure = max(gpd, key=lambda k: safe_float(gpd[k]) or 0)
        cat_to_m = {"COURT": 1400, "COURTE": 1400, "MOYEN": 1800, "MOYENNE": 1800, "LONG": 2400, "LONGUE": 2400, "TRES_LONG": 3000}
        dist_opt = cat_to_m.get(str(meilleure).upper())
        if dist_opt:
            ecart = abs(dist_auj - dist_opt) / dist_opt
            if ecart < 0.12:   return 4
            elif ecart < 0.25: return 1
            elif ecart > 0.40: return -3
    return 0

def feat_historique_piste(cheval, hippo_code):
    if not hippo_code: return 0
    gph = cheval.get("gainsParHippodrome") or cheval.get("performancesParHippodrome")
    if isinstance(gph, dict):
        code = str(hippo_code).upper()
        for k, v in gph.items():
            if str(k).upper() == code or code in str(k).upper():
                gains = safe_float(v) or 0
                if gains > 50000: return 5
                elif gains > 10000: return 3
                elif gains > 0: return 1
        return -1
    vict_piste = safe_int(cheval.get("nombreVictoiresPiste") or cheval.get("victoiresHippodrome"), 0)
    if vict_piste >= 2: return 4
    elif vict_piste == 1: return 2
    return 0

def feat_terrain(cheval, course, disc):
    if disc == "TROT": return 0
    terrain_auj = str(course.get("terrain") or course.get("etatPiste") or course.get("terrainGeneral") or course.get("nature") or "").upper()
    if not terrain_auj: return 0
    terrain_pref = str(cheval.get("terrainPrefere") or cheval.get("typeTerrain") or "").upper()
    if terrain_pref:
        lourd  = any(x in terrain_pref for x in ["LOURD", "MOU", "SOFT", "HEAVY"])
        souple = any(x in terrain_pref for x in ["SOUPLE", "GOOD_SOFT"])
        bon    = any(x in terrain_pref for x in ["BON", "GOOD", "FIRM"])
        lourd_auj  = any(x in terrain_auj for x in ["LOURD", "HEAVY", "MOU"])
        souple_auj = any(x in terrain_auj for x in ["SOUPLE", "SOFT"])
        bon_auj    = any(x in terrain_auj for x in ["BON", "GOOD", "FIRM"])
        if (lourd and lourd_auj) or (souple and souple_auj) or (bon and bon_auj): return 4
        if (lourd and bon_auj) or (bon and lourd_auj): return -4
        return 0
    gt = cheval.get("gainsParNaturePiste") or cheval.get("gainsParTerrain")
    if isinstance(gt, dict) and gt:
        meilleur = max(gt, key=lambda k: safe_float(gt[k]) or 0)
        mk = str(meilleur).upper()
        lourd_pref = any(x in mk for x in ["LOURD", "HEAVY"])
        bon_pref   = any(x in mk for x in ["BON", "GOOD", "FIRM"])
        lourd_auj  = any(x in terrain_auj for x in ["LOURD", "HEAVY"])
        bon_auj    = any(x in terrain_auj for x in ["BON", "GOOD", "FIRM"])
        if (lourd_pref and lourd_auj) or (bon_pref and bon_auj): return 3
        if (lourd_pref and bon_auj) or (bon_pref and lourd_auj): return -3
    return 0

def feat_combo_connexion(cheval, tous_partants):
    jock_id  = dict_id(cheval.get("jockey")) or dict_id(cheval.get("driver"))
    train_id = dict_id(cheval.get("entraineur"))
    if not jock_id or not train_id or not tous_partants: return 0
    wins = sorties = 0
    for p in tous_partants:
        j = dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        t = dict_id(p.get("entraineur"))
        if j == jock_id and t == train_id:
            sorties += 1
            music = parse_musique(p.get("musique"))
            if music and music[0] == ("pos", 1): wins += 1
    if sorties < 3: return 0
    taux = wins / sorties
    if taux > 0.30:   return 6
    elif taux > 0.20: return 3
    elif taux > 0.10: return 1
    elif taux < 0.05 and sorties >= 5: return -2
    return 0

def feat_regularite_conditions(music, cheval):
    positions = [p for t, p in music if t == "pos"]
    if len(positions) < 5: return 0
    top_3  = sum(1 for p in positions[:5] if p <= 3)
    hors_5 = sum(1 for p in positions[:5] if p > 5)
    if top_3 >= 3 and hors_5 <= 1: return 4
    elif top_3 >= 2 and hors_5 <= 2: return 2
    elif top_3 >= 2 and hors_5 >= 3: return -2
    elif top_3 == 0 and hors_5 >= 4: return -3
    return 0

# ─── NOUVEAUX CRITÈRES ────────────────────────────────────────────

def feat_victoires_hippodrome(cheval, hippo_code):
    """Victoires confirmées sur cet hippodrome précis."""
    if not hippo_code: return 0
    vict = safe_int(cheval.get("nombreVictoiresPiste") or cheval.get("victoiresHippodrome"), 0)
    if vict >= 3: return 5
    elif vict >= 1: return 3
    # Essai via gainsParHippodrome
    gph = cheval.get("gainsParHippodrome") or {}
    if isinstance(gph, dict):
        code = str(hippo_code).upper()
        for k, v in gph.items():
            if str(k).upper() == code or code in str(k).upper():
                return 3 if (safe_float(v) or 0) > 5000 else 1
    return 0

def feat_victoires_distance(cheval, course):
    """Victoires à cette distance exacte (±150m)."""
    dist_auj = safe_float(course.get("distance") or 0)
    if not dist_auj: return 0
    # Champ direct si l'API le fournit
    vd = cheval.get("victoiresParDistance") or cheval.get("nombreVictoiresDistance") or {}
    if isinstance(vd, dict):
        for k, v in vd.items():
            try:
                d = float(re.sub(r"[^0-9.]", "", str(k)))
                if abs(d - dist_auj) <= 150:
                    n = safe_int(v, 0)
                    if n >= 2: return 5
                    elif n == 1: return 3
            except Exception: pass
    # Proxy via distancePrefere
    dist_pref = safe_float(cheval.get("distancePrefere"))
    if dist_pref and abs(dist_pref - dist_auj) <= 150:
        vict = safe_int(cheval.get("nombreVictoires"), 0)
        total = safe_int(cheval.get("nombreCourses"), 1)
        if total > 0 and vict / total > 0.25: return 3
    return 0

def feat_jours_sans_victoire(cheval):
    """
    Cheval en manque de confiance = trop longtemps sans gagner.
    Cheval en série = a gagné récemment → bonus.
    """
    music = parse_musique(cheval.get("musique", ""))
    positions = [p for t, p in music if t == "pos"]
    if not positions: return 0
    # Combien de courses depuis la dernière victoire ?
    for i, p in enumerate(positions):
        if p == 1:
            if i == 0: return 4    # a gagné la dernière fois
            elif i <= 2: return 2  # a gagné il y a 2-3 courses
            elif i <= 4: return 0  # normal
            else: return -2        # longue disette
    return -3  # pas de victoire dans les 10 dernières

def feat_saison(cheval):
    """
    Certains chevaux sont saisonniers (performances meilleures en été/hiver).
    Proxy : compare mois actuel aux mois de victoires passées.
    Sans historique détaillé, on utilise l'âge + la date de dernière course.
    """
    mois_actuel = datetime.now().month
    # En l'absence de données détaillées, on favorise légèrement
    # les chevaux dont la dernière course était en forme (déjà capturé par feat_fraicheur)
    # et on pénalise légèrement les chevaux qui n'ont pas couru depuis plus de 6 mois
    derniere = cheval.get("dateDerniereCourse")
    if not derniere: return 0
    try:
        if isinstance(derniere, (int, float)):
            d = datetime.fromtimestamp(derniere / 1000)
        else:
            raw = str(derniere)
            for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                try: d = datetime.strptime(raw[:10], fmt); break
                except ValueError: continue
            else: return 0
        # Si le cheval a couru dans le même mois les années précédentes → bon signal
        mois_derniere = d.month
        if abs(mois_actuel - mois_derniere) <= 1 or abs(mois_actuel - mois_derniere) >= 11:
            return 1  # même période de l'année
    except Exception: pass
    return 0

def feat_meteo_piste(course):
    """
    État de la piste déclaré par l'hippodrome le jour de la course.
    Retourne l'état sous forme de string pour affichage.
    Pas de scoring direct ici — utilisé en combinaison avec feat_terrain.
    """
    etat = str(
        course.get("terrain") or course.get("etatPiste") or
        course.get("terrainGeneral") or course.get("nature") or "?"
    )
    return etat

# ============= LOGIT BRUT PAR CHEVAL ======
def compute_logit(cheval, course, tous_partants, disc):
    music        = parse_musique(cheval.get("musique"))
    est_debutant = len(music) == 0

    f_forme   = feat_forme(music)
    f_reg     = feat_regularite(music)
    f_prog    = feat_progression(music)
    f_place   = feat_ratio_place(cheval)
    f_tx_vict = feat_taux_victoires(cheval)

    cote     = safe_float((cheval.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(cheval.get("coteInitiale"))
    # ─── CORRECTIF SÉLECTIONS INSTABLES ────────────────────────────
    # Le poids de la cote dans le logit est réduit de 35 → 25.
    # La cote reste le signal le plus fort mais avec moins d'amplitude,
    # ce qui empêche les microvariations de cote de changer le classement.
    # ────────────────────────────────────────────────────────────────
    f_marche = clamp(1 / cote * 25, 0, 25) if cote else 0

    if est_debutant:
        f_forme   = f_marche * 0.4
        f_reg = f_prog = f_place = f_tx_vict = 0

    f_drift, drift_val   = feat_mouvement_cote(cheval)
    f_classe             = feat_classe(cheval, course)
    f_perf_rel           = feat_performance_relative(cheval, tous_partants)
    f_rk, rk_val         = feat_reduction_km(cheval, tous_partants, disc)
    f_fraich             = feat_fraicheur(cheval)
    f_partants           = feat_nb_partants(course)
    f_gains_an           = feat_gains_annee(cheval)
    f_sexe               = feat_sexe(cheval)
    f_deferre            = feat_deferre(cheval, disc)
    f_poids              = feat_poids(cheval, disc)
    f_oeil               = feat_oeilleres(cheval, disc)
    f_trainer            = feat_entraineur(cheval, tous_partants)
    f_ecurie             = feat_forme_ecurie(cheval, tous_partants)
    f_jockey             = feat_jockey(cheval, tous_partants)
    f_age                = feat_age_disc(cheval, disc)
    f_corde              = feat_corde_plat(cheval, course, disc)
    f_obs                = feat_experience_obs(cheval, disc)
    f_gains_c            = feat_gains_carriere_trot(cheval, disc)
    f_recul              = feat_recul_trot(cheval) if disc == "TROT" else 0

    hippo_code = (
        course.get("hippodrome", {}).get("code") or
        course.get("hippodrome", {}).get("nom") or
        course.get("codeHippodrome") or ""
    ) if isinstance(course.get("hippodrome"), dict) else str(course.get("hippodrome") or "")

    f_dist     = feat_distance_optimale(cheval, course)
    f_piste    = feat_historique_piste(cheval, hippo_code)
    f_terrain  = feat_terrain(cheval, course, disc)
    f_combo    = feat_combo_connexion(cheval, tous_partants)
    f_reg_cond = feat_regularite_conditions(music, cheval)

    # ─── NOUVEAUX CRITÈRES ─────────────────────────────────────────
    f_vict_hippo = feat_victoires_hippodrome(cheval, hippo_code)
    f_vict_dist  = feat_victoires_distance(cheval, course)
    f_disette    = feat_jours_sans_victoire(cheval)
    f_saison     = feat_saison(cheval)

    logit = (
        f_forme    * 1.0
        + f_reg      * 0.8
        + f_prog     * 0.7
        + f_place    * 0.7
        + f_tx_vict  * 0.8
        + f_marche   * 1.0   # réduit (était 1.0 × 35, maintenant 1.0 × 25)
        + f_drift    * 0.8   # réduit (était 1.2, maintenant 0.8)
        + f_classe   * 0.9
        + f_perf_rel * 0.6
        + f_rk       * 1.1
        + f_fraich   * 0.6
        + f_partants * 0.4
        + f_gains_an * 0.5
        + f_sexe     * 0.3
        + f_deferre  * 0.5
        + f_poids    * 0.6
        + f_oeil     * 0.5
        + f_trainer  * 0.5
        + f_ecurie   * 0.4
        + f_jockey   * 0.5
        + f_age      * 0.5
        + f_corde    * 0.4
        + f_obs      * 0.5
        + f_gains_c  * 0.6
        + f_recul    * 0.7
        + f_dist     * 0.8
        + f_piste    * 0.7
        + f_terrain  * 0.8
        + f_combo    * 0.7
        + f_reg_cond * 0.6
        # Nouveaux
        + f_vict_hippo * 0.8   # victoires sur cet hippodrome
        + f_vict_dist  * 0.7   # victoires à cette distance
        + f_disette    * 0.6   # manque de confiance / en série
        + f_saison     * 0.3   # saisonnalité
    )

    details = {
        "forme":      round(f_forme, 1),
        "reg":        round(f_reg, 1),
        "prog":       round(f_prog, 1),
        "place":      round(f_place, 1),
        "marche":     round(f_marche, 1),
        "drift":      round(f_drift, 1),
        "classe":     round(f_classe, 1),
        "perf_rel":   round(f_perf_rel, 1),
        "rk":         round(f_rk, 1),
        "fraich":     round(f_fraich, 1),
        "deferre":    round(f_deferre, 1),
        "jockey":     round(f_jockey, 1),
        "trainer":    round(f_trainer, 1),
        "poids":      round(f_poids, 1),
        "oeil":       round(f_oeil, 1),
        "dist":       round(f_dist, 1),
        "piste":      round(f_piste, 1),
        "terrain":    round(f_terrain, 1),
        "combo":      round(f_combo, 1),
        "reg_cond":   round(f_reg_cond, 1),
        "vict_hippo": round(f_vict_hippo, 1),
        "vict_dist":  round(f_vict_dist, 1),
        "disette":    round(f_disette, 1),
        "saison":     round(f_saison, 1),
    }
    return logit, cote, details

# ============= ANALYSE INTRA-COURSE =======
def analyse_course(partants, course):
    disc    = detect_discipline(course)
    logits  = []; donnees = []
    for ch in partants:
        try:
            logit, cote, details = compute_logit(ch, course, partants, disc)
            logits.append(logit)
            donnees.append({"cheval": ch, "cote": cote, "details": details, "logit": logit})
        except Exception as e:
            logits.append(0)
            donnees.append({"cheval": ch, "cote": None, "details": {}, "logit": 0, "erreur": str(e)})

    TEMPERATURE = 10.0
    probs_modele_raw = softmax([l / TEMPERATURE for l in logits])
    MAX_PROB = 0.70
    probs_modele = list(probs_modele_raw)
    for _ in range(10):
        total_sur = sum(max(0, p - MAX_PROB) for p in probs_modele)
        if total_sur < 1e-9: break
        sous_plafond = [i for i, p in enumerate(probs_modele) if p < MAX_PROB]
        if not sous_plafond: break
        redistrib = total_sur / len(sous_plafond)
        probs_modele = [min(p, MAX_PROB) for p in probs_modele]
        for i in sous_plafond: probs_modele[i] += redistrib

    nb_avec_musique = sum(1 for p in partants if parse_musique(p.get("musique")))
    champ_inconnu   = nb_avec_musique < len(partants) * 0.5

    cotes_brutes  = [d["cote"] for d in donnees]
    probs_brutes  = [1 / c if c and c > 0 else 0 for c in cotes_brutes]
    somme_brutes  = sum(probs_brutes)
    probs_marche  = [p / somme_brutes if somme_brutes > 0 else 0 for p in probs_brutes]

    resultats = []
    for d, pm, pmkt in zip(donnees, probs_modele, probs_marche):
        if "erreur" in d: continue
        ch    = d["cheval"]
        value = round((pm - pmkt) * 100, 1)
        drift_bonus = d["details"].get("drift", 0)
        confiance   = round(clamp(pm * 100 + drift_bonus * 2, 0, 99), 1)
        if champ_inconnu: confiance = round(confiance * 0.6, 1)
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

# ─── CACHE SÉLECTIONS DU JOUR ────────────────────────────────────
# Empêche les sélections de bouger en cours de journée une fois
# calculées. Clé = date du jour. Si l'utilisateur veut recalculer
# il peut vider le cache via le bouton dédié.

def cache_key_today():
    return f"selections_{datetime.now().strftime('%Y%m%d')}"

def get_cached_selections():
    key = cache_key_today()
    return st.session_state.get(key)

def set_cached_selections(data):
    key = cache_key_today()
    st.session_state[key] = data

# =============== STREAMLIT UI =============
st.set_page_config(page_title="🏇 Benter PMU", page_icon="🏇", layout="centered")
st.markdown("""
<style>
  .block-container { padding-top: 1.5rem; }
  .card { background:#161b27; border:1px solid #1e2535; border-radius:14px; padding:16px 18px; margin-bottom:14px; }
  .horse-name { font-size:1.25rem; font-weight:800; color:#ffffff; }
  .sub { color:#6b7280; font-size:0.82rem; margin-top:3px; }
  .badge-fort  { background:#3b1a1a; color:#f87171; padding:3px 10px; border-radius:6px; font-size:0.78rem; font-weight:700; display:inline-block; margin-top:6px; }
  .badge-value { background:#2d2a0f; color:#fbbf24; padding:3px 10px; border-radius:6px; font-size:0.78rem; font-weight:700; display:inline-block; margin-top:6px; }
  .detail-grid { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .detail-pill { background:#0f1117; border-radius:6px; padding:3px 8px; font-size:0.73rem; color:#9ca3af; }
  .detail-pill.pos { color:#4ade80; }
  .detail-pill.neg { color:#f87171; }
  .frozen-banner { background:#1a2a1a; border:1px solid #4ade8044; border-radius:10px; padding:10px 16px; margin-bottom:12px; color:#86efac; font-size:0.85rem; }
</style>
""", unsafe_allow_html=True)

st.title("🏇 Benter PMU — Sélections du jour")
st.caption(datetime.now().strftime("%A %d %B %Y"))

with st.sidebar:
    st.header("⚙️ Filtres")
    min_value = st.slider("Avantage marché minimum (%)", -10, 20, int(MIN_PROB_EDGE * 100))
    min_conf  = st.slider("Confiance minimum",           0, 99, MIN_CONF)
    max_sel   = st.slider("Sélections max",              1, 10, MAX_SELECTIONS)
    st.divider()
    if st.button("🗑️ Recalculer (vider cache)", use_container_width=True):
        key = cache_key_today()
        if key in st.session_state: del st.session_state[key]
        st.success("Cache vidé — relance l'analyse.")
    st.divider()
    st.caption("**Critères (27)**")
    st.caption("Forme · Régularité · Progression · Ratio placé · Taux victoires")
    st.caption("Marché · Drift cote · Classe · Perf. relative · RK trot")
    st.caption("Fraîcheur · Partants · Gains/an · Déferré · Jockey")
    st.caption("Entraîneur · Poids · Œillères · Écurie · Âge · Corde")
    st.caption("Distance optimale · Historique piste · Terrain")
    st.caption("Combo J+E · Rég. conditions")
    st.caption("**🆕 Victoires hippodrome · Victoires distance · Disette/Série · Saisonnalité**")

def pill(label, val):
    if val is None or val == 0: return f'<span class="detail-pill">{label} 0</span>'
    cls  = "pos" if val > 0 else "neg"
    sign = "+" if val > 0 else ""
    return f'<span class="detail-pill {cls}">{label} {sign}{val}</span>'

def afficher_selections(gardes, candidats, courses, min_value, min_conf):
    if not gardes:
        st.warning("❌ Aucun pari aujourd'hui — aucun cheval ne passe les filtres.")
        top3 = sorted(candidats, key=lambda x: x["value"], reverse=True)[:3]
        if top3:
            st.caption("Top 3 des meilleures values aujourd'hui (hors filtres) :")
            for c in top3:
                st.caption(f"• N°{c['num']} {c['nom']} ({c['course']}) — Prob. modèle {c['prob']}% vs marché {c['prob_mkt']}% · Value {c['value']:+.1f}%")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 10
    st.success(f"🔥 {len(gardes)} sélection(s) du jour")

    for i, c in enumerate(gardes):
        is_fort = c["value"] >= 10 and c["confiance"] >= 75
        badge   = '<span class="badge-fort">🔥 PARI FORT</span>' if is_fort else '<span class="badge-value">⚡ VALUE</span>'
        if c.get("champ_inconnu"):
            badge += ' <span style="background:#1a2a1a;color:#86efac;padding:3px 10px;border-radius:6px;font-size:0.78rem;font-weight:700;display:inline-block;margin-top:6px;">⚠️ Champ d\'inconnus</span>'
        hippo = f" · {c['hippo']}" if c.get("hippo") and c["hippo"] != "?" else ""
        d     = c.get("details", {})
        pills = "".join([
            pill("Forme",      d.get("forme")),
            pill("Rég.",       d.get("reg")),
            pill("Prog.",      d.get("prog")),
            pill("Marché",     d.get("marche")),
            pill("Drift",      d.get("drift")),
            pill("Classe",     d.get("classe")),
            pill("Perf.rel.",  d.get("perf_rel")),
            pill("RK",         d.get("rk")),
            pill("Distance",   d.get("dist")),
            pill("Piste",      d.get("piste")),
            pill("Terrain",    d.get("terrain")),
            pill("Combo",      d.get("combo")),
            pill("Rég.cond.",  d.get("reg_cond")),
            pill("Fraîcheur",  d.get("fraich")),
            pill("Déferré",    d.get("deferre")),
            pill("Jockey",     d.get("jockey")),
            pill("Trainer",    d.get("trainer")),
            pill("Poids",      d.get("poids")),
            pill("Œill.",      d.get("oeil")),
            pill("V.Hippo",    d.get("vict_hippo")),
            pill("V.Dist",     d.get("vict_dist")),
            pill("Série",      d.get("disette")),
            pill("Saison",     d.get("saison")),
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
        col1.metric("Prob. modèle", f"{c['prob']}%")
        col2.metric("Prob. marché", f"{c['prob_mkt']}%")
        col3.metric("Avantage",     f"{c['value']:+.1f}%")
        col4.metric("Cote",         f"{c['cote']}×" if c['cote'] else "—")
        st.markdown("---")

    st.caption(f"Filtres : value ≥ {min_value}% · confiance ≥ {min_conf}%")
    st.caption(f"{len(candidats)} chevaux analysés · {len(courses)} courses")

# ── Bouton principal
col_btn, col_recalc = st.columns([3, 1])
lancer = col_btn.button("🔍 Analyser les courses du jour", use_container_width=True, type="primary")
force  = col_recalc.button("↺ Forcer", help="Ignore le cache et recalcule tout")

cached = get_cached_selections()

if force:
    key = cache_key_today()
    if key in st.session_state: del st.session_state[key]
    cached = None
    lancer = True

if cached:
    gardes     = cached["gardes"]
    candidats  = cached["candidats"]
    courses    = cached["courses"]
    st.markdown(f'<div class="frozen-banner">🔒 Sélections figées à {cached["heure"]} — les cotes ont peut-être bougé mais les sélections restent stables. Clique sur ↺ Forcer pour recalculer.</div>', unsafe_allow_html=True)
    st.session_state["gardes_du_jour"] = gardes
    afficher_selections(gardes, candidats, courses, min_value, min_conf)

elif lancer:
    with st.spinner("Chargement du programme PMU…"):
        try:
            data     = pmu_get(f"/{today()}/reunions")
            reunions = (data.get("programme") or {}).get("reunions") or data.get("reunions") or []
        except Exception as e:
            st.error(f"Impossible de charger le programme PMU : {e}")
            st.stop()

    courses = []
    for r in reunions:
        for c in (r.get("courses") or []):
            courses.append((r, c))

    st.info(f"📋 {len(reunions)} réunions · {len(courses)} courses détectées")

    candidats = []; erreurs_api = []; erreurs_analyse = []
    bar = st.progress(0, text="Analyse des partants…")

    for i, (r, c) in enumerate(courses):
        bar.progress((i + 1) / len(courses), text=f"Course {i+1}/{len(courses)}…")
        num_r = r.get("numOfficiel") or r.get("numOrdre") or r.get("numeroReunion") or r.get("num")
        num_c = c.get("numOfficiel") or c.get("numOrdre") or c.get("numCourse") or c.get("num")
        if not num_r or not num_c: continue
        try:
            pdata    = pmu_get(f"/{today()}/R{num_r}/C{num_c}/participants")
            partants = pdata.get("participants") or []
        except Exception as e:
            erreurs_api.append(f"R{num_r}C{num_c} — {e}"); continue
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
        with st.expander(f"⚠️ Diagnostics ({len(erreurs_api)} err. API · {len(erreurs_analyse)} err. analyse)"):
            for e in erreurs_api[:5]: st.caption(f"• API: {e}")
            for e in erreurs_analyse[:5]: st.caption(f"• Analyse: {e}")

    gardes = [c for c in candidats if c["value"] >= min_value and c["confiance"] >= min_conf and c.get("cote")]
    gardes.sort(key=lambda x: (x["value"], x["confiance"]), reverse=True)
    gardes = gardes[:max_sel]

    # ─── FIGER LES SÉLECTIONS ────────────────────────────────────
    set_cached_selections({
        "gardes":    gardes,
        "candidats": candidats,
        "courses":   courses,
        "heure":     datetime.now().strftime("%H:%M"),
    })
    st.session_state["gardes_du_jour"] = gardes
    afficher_selections(gardes, candidats, courses, min_value, min_conf)

# ========== JOURNAL ==========
st.markdown("---")
st.markdown("## 📓 Journal de résultats")
journal = load_journal()
tab_add, tab_stats = st.tabs(["➕ Enregistrer un résultat", "📊 Statistiques"])

with tab_add:
    st.markdown("Après chaque course, note ici si ta sélection a gagné, été placée ou perdu.")
    gardes_session = st.session_state.get("gardes_du_jour", [])
    choix_rapides  = ["— Saisie manuelle —"] + [f"N°{g['num']} {g['nom']} ({g['course']})" for g in gardes_session]
    selection_rapide = st.selectbox("Cheval du jour (pré-remplissage automatique)", choix_rapides)
    garde_sel = None
    if selection_rapide != "— Saisie manuelle —":
        idx = choix_rapides.index(selection_rapide) - 1
        if 0 <= idx < len(gardes_session): garde_sel = gardes_session[idx]

    with st.form("form_journal", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        date_val   = col_a.date_input("Date", value=date.today())
        course_val = col_b.text_input("Course", value=garde_sel["course"] + " " + garde_sel.get("discipline","") if garde_sel else "", placeholder="ex: R2C3 TROT Vincennes")
        col_c, col_d, col_e = st.columns(3)
        cheval_val   = col_c.text_input("Cheval", value=garde_sel["nom"] if garde_sel else "", placeholder="ex: TOKAIDO")
        num_val      = col_d.number_input("N°", min_value=1, max_value=30, value=int(garde_sel["num"]) if garde_sel else 1, step=1)
        cote_val     = col_e.number_input("Cote (×)", min_value=1.0, value=float(garde_sel["cote"]) if garde_sel and garde_sel.get("cote") else 5.0, step=0.5)
        resultat_val = st.radio("Résultat", ["gagné", "placé", "perdu"], horizontal=True)
        submitted    = st.form_submit_button("💾 Enregistrer", use_container_width=True)
        if submitted:
            if not cheval_val.strip():
                st.warning("Indique le nom du cheval.")
            else:
                new_entry = {
                    "id": str(int(time.time())), "date": str(date_val),
                    "course": course_val.strip(), "cheval": cheval_val.strip().upper(),
                    "num": int(num_val), "cote": float(cote_val), "resultat": resultat_val,
                    "details": garde_sel.get("details", {}) if garde_sel else {},
                    "value":   garde_sel.get("value", 0) if garde_sel else 0,
                    "prob":    garde_sel.get("prob", 0) if garde_sel else 0,
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
        st.info("Aucun résultat enregistré pour l'instant.")
    else:
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

        st.markdown("### Par semaine")
        semaines = {}
        for e in journal:
            try:
                d_obj = datetime.strptime(e["date"], "%Y-%m-%d").date()
                import datetime as dt_mod
                lundi = d_obj - dt_mod.timedelta(days=d_obj.weekday())
                key   = str(lundi)
            except Exception: key = "?"
            semaines.setdefault(key, []).append(e)

        if "supprimer_id" in st.session_state and st.session_state["supprimer_id"]:
            id_a_sup        = st.session_state.pop("supprimer_id")
            journal_modifie = [e for e in journal if e.get("id") != id_a_sup]
            ok, err = save_journal(journal_modifie)
            if ok: load_journal.clear(); st.rerun()
            else: st.error(f"Erreur suppression : {err}")

        for semaine in sorted(semaines.keys(), reverse=True):
            es = semaines[semaine]; ss = journal_stats(es)
            roi_c = "🟢" if ss["roi"] >= 0 else "🔴"
            with st.expander(f"{roi_c} Semaine du {semaine} — {ss['total']} sél. · ROI {ss['roi']:+.1f}%"):
                for e in sorted(es, key=lambda x: x.get("date",""), reverse=True):
                    icon = "✅" if e["resultat"] == "gagné" else ("🏅" if e["resultat"] == "placé" else "❌")
                    col_txt, col_btn = st.columns([5, 1])
                    col_txt.markdown(f"{icon} **{e.get('cheval','?')}** (N°{e.get('num','?')}) — {e.get('course','?')} · {e.get('cote','?')}× · {e.get('date','')}")
                    entry_id = e.get("id", "")
                    if col_btn.button("🗑️", key=f"del_{entry_id}", help="Supprimer"):
                        st.session_state["supprimer_id"] = entry_id; st.rerun()

        entrees_avec_details = [e for e in journal if e.get("details")]
        if entrees_avec_details:
            st.markdown("### 🔬 Analyse par critère")
            tous_criteres = {
                "forme":"Forme","reg":"Régularité","prog":"Progression",
                "marche":"Marché","drift":"Drift cote","classe":"Classe",
                "perf_rel":"Perf. relative","rk":"RK trot",
                "dist":"Distance","piste":"Piste","terrain":"Terrain",
                "combo":"Combo connexion","reg_cond":"Rég. conditions",
                "fraich":"Fraîcheur","deferre":"Déferré",
                "jockey":"Jockey","trainer":"Entraîneur",
                "poids":"Poids","oeil":"Œillères",
                "vict_hippo":"V. Hippodrome","vict_dist":"V. Distance",
                "disette":"Série/Disette","saison":"Saisonnalité",
            }
            lignes = []
            for key, label_c in tous_criteres.items():
                avec = [e for e in entrees_avec_details if (e["details"].get(key) or 0) > 0]
                if len(avec) < 2: continue
                wins_avec = sum(1 for e in avec if e["resultat"] == "gagné")
                taux = round(wins_avec / len(avec) * 100, 1)
                roi_c_abs = sum((e.get("cote",1)-1) if e["resultat"]=="gagné" else (0 if e["resultat"]=="placé" else -1) for e in avec)
                roi_c_pct = round(roi_c_abs / len(avec) * 100, 1)
                lignes.append((label_c, len(avec), taux, roi_c_pct))
            if lignes:
                lignes.sort(key=lambda x: x[2], reverse=True)
                rows_html = ""
                for label_c, n, taux, roi_c_pct in lignes:
                    bar_w   = int(taux)
                    bar_col = "#4ade80" if taux >= 30 else ("#facc15" if taux >= 15 else "#f87171")
                    roi_col = "#4ade80" if roi_c_pct >= 0 else "#f87171"
                    rows_html += f"""<tr>
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
                st.markdown(f"""<table style="width:100%;border-collapse:collapse;background:#111827;border-radius:12px;overflow:hidden">
                  <thead><tr style="background:#1e293b">
                    <th style="padding:8px 10px;text-align:left;color:#94a3b8;font-size:0.8rem">Critère</th>
                    <th style="padding:8px 10px;text-align:center;color:#94a3b8;font-size:0.8rem">N</th>
                    <th style="padding:8px 10px;text-align:left;color:#94a3b8;font-size:0.8rem">Taux victoire</th>
                    <th style="padding:8px 10px;text-align:right;color:#94a3b8;font-size:0.8rem">ROI</th>
                  </tr></thead><tbody>{rows_html}</tbody></table>""", unsafe_allow_html=True)
                st.caption("⚠️ Données fiables après 20+ sélections.")
