import urllib.request
import urllib.parse
import json
import math
import re
import os
import base64
import time
from datetime import datetime, date
import streamlit as st

# ═══════════════════════════════════════════════════════════════
#  BENTER PMU — v3.0
#  Étape 1 : Filtres discipline, poids par discipline, outsider, mobile
#  Étape 2 : Météo via Open-Meteo (gratuit, sans clé)
#  LeTROT  : Enrichissement données trot (RK détaillé, historique)
# ═══════════════════════════════════════════════════════════════

# ─── CONFIG ──────────────────────────────────────────────────────
PMU_BASE    = "https://online.turfinfo.api.pmu.fr/rest/client/1/programme"
METEO_BASE  = "https://api.open-meteo.com/v1/forecast"
LETROT_BASE = "https://www.letrot.com/stats/fiche-course"
TIMEOUT     = 12

# Coordonnées GPS des principaux hippodromes français
HIPPODROMES_GPS = {
    "VINCENNES":        (48.8432,  2.4411),
    "PARIS-VINCENNES":  (48.8432,  2.4411),
    "LONGCHAMP":        (48.8553,  2.2385),
    "AUTEUIL":          (48.8593,  2.2604),
    "SAINT-CLOUD":      (48.8442,  2.2003),
    "CHANTILLY":        (49.1936,  2.4711),
    "DEAUVILLE":        (49.3513, -0.0779),
    "CAGNES":           (43.6647,  7.1562),
    "CAGNES-SUR-MER":   (43.6647,  7.1562),
    "LYON":             (45.7640,  4.8357),
    "MARSEILLE":        (43.2965,  5.3698),
    "BORDEAUX":         (44.8378, -0.5792),
    "TOULOUSE":         (43.6047,  1.4442),
    "STRASBOURG":       (48.5734,  7.7521),
    "NANTES":           (47.2184, -1.5536),
    "CAEN":             (49.1829, -0.3707),
    "CABOURG":          (49.2853, -0.1265),
    "ENGHIEN":          (48.9711,  2.3078),
    "MAISONS-LAFFITTE": (48.9497,  2.1469),
    "COMPIEGNE":        (49.4183,  2.8247),
    "CRAON":            (47.8513, -0.9456),
    "LAVAL":            (48.0698, -0.7683),
    "LE LION":          (47.6289, -0.7145),
    "ARGENTAN":         (48.7447,  0.0197),
    "LA CAPELLE":       (49.9783,  3.9097),
    "AGEN":             (44.2005,  0.6228),
    "PAU":              (43.2951, -0.3708),
    "TARBES":           (43.2328,  0.0781),
    "VICHY":            (46.1273,  3.4267),
    "CLAIREFONTAINE":   (49.2980, -0.1097),
}

# ─── OUTILS ──────────────────────────────────────────────────────
def clamp(v, a, b): return max(a, min(v, b))
def safe_float(v):
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except Exception: return None
def safe_int(v, d=0):
    try: return int(v)
    except Exception: return d
def dict_id(v): return v.get("id") if isinstance(v, dict) else None

def detect_discipline(course):
    txt = str(course.get("discipline","")).upper()
    if "ATTEL" in txt or "MONT" in txt or "TROT" in txt: return "TROT"
    if "HAIE" in txt or "STEEPLE" in txt or "CROSS" in txt: return "OBSTACLE"
    return "PLAT"

def get_hippo_name(reunion, course):
    h = course.get("hippodrome") or reunion.get("hippodrome") or {}
    if isinstance(h, dict):
        return (h.get("nom") or h.get("libelle") or "").upper()
    return str(reunion.get("nom","")).upper()

def get_hippo_gps(nom):
    nom = nom.upper()
    for key, coords in HIPPODROMES_GPS.items():
        if key in nom or nom in key:
            return coords
    return None

# ─── API PMU ─────────────────────────────────────────────────────
def pmu_get(path):
    url = PMU_BASE + path
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept":     "application/json"
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

def today():
    return datetime.now().strftime("%d%m%Y")

# ─── MÉTÉO OPEN-METEO ────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_meteo(lat, lon):
    """
    Récupère météo du jour via Open-Meteo (gratuit, sans clé).
    Retourne : précipitations (mm), code météo, température max.
    """
    try:
        params = urllib.parse.urlencode({
            "latitude":    lat,
            "longitude":   lon,
            "daily":       "precipitation_sum,weathercode,temperature_2m_max,wind_speed_10m_max",
            "timezone":    "Europe/Paris",
            "forecast_days": 1
        })
        url = f"{METEO_BASE}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        daily = data.get("daily", {})
        return {
            "pluie":  (daily.get("precipitation_sum") or [0])[0] or 0,
            "code":   (daily.get("weathercode")       or [0])[0] or 0,
            "temp":   (daily.get("temperature_2m_max")or [15])[0] or 15,
            "vent":   (daily.get("wind_speed_10m_max") or [0])[0] or 0,
        }
    except Exception:
        return None

def meteo_label(m):
    if not m: return "Météo inconnue", "❓"
    code = m.get("code", 0)
    pluie = m.get("pluie", 0)
    if code == 0:   return "Ensoleillé", "☀️"
    if code <= 3:   return "Nuageux", "⛅"
    if code <= 48:  return "Brouillard", "🌫️"
    if code <= 67:  return f"Pluie {pluie:.1f}mm", "🌧️"
    if code <= 77:  return "Neige", "❄️"
    if code <= 82:  return f"Averses {pluie:.1f}mm", "🌦️"
    if code <= 99:  return "Orage", "⛈️"
    return "Variable", "🌤️"

def feat_meteo_terrain(ch, meteo, disc):
    """
    Croise l'état météo du jour avec les préférences terrain du cheval.
    En trot : la pluie améliore les pistes synthétiques (PSF → neutre).
    En galop : lourd favorise les chevaux qui aiment le gras.
    """
    if not meteo or disc == "TROT": return 0
    pluie = meteo.get("pluie", 0)
    terrain_pref = str(
        ch.get("terrainPrefere") or ch.get("typeTerrain") or ""
    ).upper()
    gt = ch.get("gainsParNaturePiste") or ch.get("gainsParTerrain") or {}

    # Déterminer si piste lourde ou bonne aujourd'hui
    piste_lourde = pluie > 5
    piste_bonne  = pluie < 1

    if terrain_pref:
        aime_lourd = any(x in terrain_pref for x in ["LOURD","MOU","HEAVY","SOFT"])
        aime_bon   = any(x in terrain_pref for x in ["BON","GOOD","FIRM","SEC"])
        if aime_lourd and piste_lourde: return 5
        if aime_bon   and piste_bonne:  return 3
        if aime_lourd and piste_bonne:  return -4
        if aime_bon   and piste_lourde: return -4
        return 0

    if isinstance(gt, dict) and gt:
        meilleur = max(gt, key=lambda k: safe_float(gt[k]) or 0)
        mk = str(meilleur).upper()
        aime_lourd = any(x in mk for x in ["LOURD","HEAVY","MOU"])
        aime_bon   = any(x in mk for x in ["BON","GOOD","FIRM"])
        if aime_lourd and piste_lourde: return 4
        if aime_bon   and piste_bonne:  return 3
        if aime_lourd and piste_bonne:  return -3
        if aime_bon   and piste_lourde: return -3
    return 0

# ─── LETROT SCRAPING ─────────────────────────────────────────────
@st.cache_data(ttl=1800)
def get_letrot_data(nom_cheval):
    """
    Récupère les données supplémentaires LeTROT pour un cheval.
    Retourne dict avec rk_moyen, nb_victoires_tete, historique simplifié.
    Gracefully retourne None si échec.
    """
    try:
        # Recherche du cheval sur LeTROT
        slug = nom_cheval.lower().replace(" ", "-").replace("'", "-")
        url  = f"https://www.letrot.com/stats/chevaux/{slug}/courses"
        req  = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Android; Mobile)",
            "Accept":     "text/html"
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")

        result = {}

        # Extraction RK (réduction kilométrique) — pattern: "1'10''3" ou "1:10.3"
        rk_matches = re.findall(r"(\d['']\d{2}['']\d)", html)
        if rk_matches:
            # Convertir en secondes pour comparaison
            rks = []
            for rk in rk_matches[:5]:
                parts = re.findall(r"\d+", rk)
                if len(parts) >= 3:
                    secs = int(parts[0])*60 + int(parts[1]) + int(parts[2])/10
                    rks.append(secs)
            if rks:
                result["rk_moyen"]    = sum(rks)/len(rks)
                result["rk_meilleur"] = min(rks)

        # Extraction victoires en tête (pattern: "En tête" ou "tête")
        tete_count = len(re.findall(r"(?:en\s+t[êe]te|leading)", html, re.I))
        result["victoires_tete"] = min(tete_count, 10)

        # Extraction positions récentes
        pos_matches = re.findall(r'<td[^>]*>\s*(\d{1,2})\s*</td>', html)
        positions = [int(p) for p in pos_matches if 1 <= int(p) <= 20][:10]
        if positions:
            result["positions_letrot"] = positions

        # Nombre de courses sur cet hippodrome détectées
        result["source"] = "letrot"
        return result if result else None

    except Exception:
        return None

# ─── MUSIQUE ─────────────────────────────────────────────────────
def parse_musique(m):
    if not m: return []
    m = re.sub(r"\s","",str(m))
    vals=[]; i=0
    while i < len(m):
        c = m[i]
        if c.isdigit():
            num = int(c)
            if i+1<len(m) and m[i+1].isdigit() and c!="0":
                num=int(c+m[i+1]); i+=1
            vals.append(("pos",num))
        elif c.lower()=="d": vals.append(("disq",None))
        elif c.lower()=="a": vals.append(("abs",None))
        i+=1
    return vals[:10]

# ─── SOFTMAX ─────────────────────────────────────────────────────
def softmax(scores):
    if not scores: return []
    m=max(scores)
    e=[math.exp(clamp(s-m,-30,30)) for s in scores]
    t=sum(e)
    return [x/t for x in e] if t>0 else [1/len(scores)]*len(scores)

# ═══════════════════════════════════════════════════════════════
# FEATURES — organisées par discipline
# ═══════════════════════════════════════════════════════════════

def feat_forme(music):
    s=0
    for i,(t,p) in enumerate(music):
        if t!="pos": continue
        w=math.exp(-0.35*i)
        if p==1:   s+=12*w
        elif p<=3: s+=8*w
        elif p<=5: s+=4*w
        elif p<=8: s+=1.5*w
    return clamp(s,0,20)

def feat_regularite(music):
    v=[p for t,p in music if t=="pos"]
    if len(v)<4: return 0
    m=sum(v)/len(v)
    s=math.sqrt(sum((x-m)**2 for x in v)/len(v))
    return clamp(8-s,0,8)

def feat_progression(music):
    v=[p for t,p in music if t=="pos"][:5]
    if len(v)<3: return 0
    n=len(v); xm=(n-1)/2; ym=sum(v)/n
    num=sum((i-xm)*(v[i]-ym) for i in range(n))
    den=sum((i-xm)**2 for i in range(n))
    if den==0: return 0
    p=num/den
    if p<-0.5: return 5
    elif p<0:  return 2
    elif p>0.5: return -2
    return 0

def feat_taux_victoires(ch):
    t=safe_int(ch.get("nombreCourses",0))
    v=safe_int(ch.get("nombreVictoires",0))
    if t==0: return 0
    return clamp(v/t*12,0,10)

def feat_ratio_place(ch):
    t=safe_int(ch.get("nombreCourses",0))
    p=safe_int(ch.get("nombrePlaces",0)) or safe_int(ch.get("nombrePlace",0))
    if t<3: return 0
    return clamp(p/t*8,0,8)

def feat_fraicheur(ch):
    d=ch.get("dateDerniereCourse") or ch.get("derniereCourseDateFr")
    if not d: return 0
    try:
        if isinstance(d,(int,float)):
            dd=datetime.fromtimestamp(d/1000).date()
        else:
            raw=str(d)
            for fmt in ("%Y-%m-%d","%d/%m/%Y","%d-%m-%Y"):
                try: dd=datetime.strptime(raw[:10],fmt).date(); break
                except ValueError: continue
            else: return 0
        j=(date.today()-dd).days
        if j<0:         return 0
        if 14<=j<=35:   return 4
        elif 7<=j<14:   return 2
        elif 35<j<=60:  return 1
        elif j>90:      return -3
        elif j>60:      return -1
    except Exception: pass
    return 0

def feat_gains_annee(ch):
    g=safe_float(ch.get("gainsAnneeEnCours") or ch.get("gainsCourseAnneeCourante") or 0)
    return clamp((g or 0)/30000*5,0,5)

def feat_disette(ch):
    music=parse_musique(ch.get("musique",""))
    pos=[p for t,p in music if t=="pos"]
    if not pos: return 0
    for i,p in enumerate(pos):
        if p==1:
            if i==0:   return 4
            elif i<=2: return 2
            elif i<=4: return 0
            else:      return -2
    return -3

def feat_regularite_conditions(music):
    pos=[p for t,p in music if t=="pos"]
    if len(pos)<5: return 0
    top=sum(1 for p in pos[:5] if p<=3)
    hors=sum(1 for p in pos[:5] if p>5)
    if top>=3 and hors<=1: return 4
    elif top>=2 and hors<=2: return 2
    elif top>=2 and hors>=3: return -2
    elif top==0 and hors>=4: return -3
    return 0

def feat_vict_hippo(ch, hippo):
    if not hippo: return 0
    v=safe_int(ch.get("nombreVictoiresPiste") or ch.get("victoiresHippodrome"),0)
    if v>=3: return 5
    elif v>=1: return 3
    gph=ch.get("gainsParHippodrome") or {}
    if isinstance(gph,dict):
        code=str(hippo).upper()
        for k,val in gph.items():
            if str(k).upper()==code or code in str(k).upper():
                return 3 if (safe_float(val) or 0)>5000 else 1
    return 0

def feat_vict_dist(ch, course):
    da=safe_float(course.get("distance") or 0)
    if not da: return 0
    dp=safe_float(ch.get("distancePrefere"))
    if dp and abs(dp-da)<=150:
        v=safe_int(ch.get("nombreVictoires"),0)
        t=safe_int(ch.get("nombreCourses"),1)
        if t>0 and v/t>0.25: return 3
    return 0

def feat_saison(ch):
    mois=datetime.now().month
    d=ch.get("dateDerniereCourse")
    if not d: return 0
    try:
        if isinstance(d,(int,float)):
            dm=datetime.fromtimestamp(d/1000).month
        else:
            raw=str(d)
            for fmt in ("%Y-%m-%d","%d/%m/%Y"):
                try: dm=datetime.strptime(raw[:10],fmt).month; break
                except ValueError: continue
            else: return 0
        if abs(mois-dm)<=1 or abs(mois-dm)>=11: return 1
    except Exception: pass
    return 0

# ── Features par discipline ───────────────────────────────────────

def feat_age(ch, disc):
    age=safe_float(ch.get("age"))
    if not age: return 0
    # Poids calibrés par discipline (Étape 1)
    if disc=="TROT":
        if 5<=age<=8:   return 7   # pic trot : 5-8 ans
        elif age in [4,9]: return 4
        elif age<=3:    return 1
        else:           return 2
    elif disc=="PLAT":
        if 3<=age<=4:   return 7   # pic plat : 3-4 ans
        elif age==5:    return 5
        elif age<=7:    return 3
        else:           return 0
    elif disc=="OBSTACLE":
        if 5<=age<=9:   return 7   # pic obstacle : 5-9 ans
        elif age==4:    return 3
        elif age>=10:   return 1
        else:           return 4
    return 0

def feat_sexe(ch):
    s=str(ch.get("sexe") or ch.get("indicateurSexe") or "").upper()
    return 2 if "HONG" in s or s=="H" else 0

def feat_deferre(ch, disc):
    r=str(ch.get("deferre") or ch.get("incident") or "").upper()
    if not r or r in ["","NONE","0","NON"]: return 0
    if "DAP" in r or "4 PATTE" in r: return 4
    if "DA" in r or "ANT" in r: return 2
    if "DP" in r or "POST" in r: return 1 if disc!="TROT" else 0
    if "DEFER" in r or r=="D": return 2
    return 0

def feat_poids(ch, disc):
    p=safe_float(ch.get("handicapPoids") or ch.get("poidsConditionMonte"))
    if not p: return 0
    # Références calibrées par discipline (Étape 1)
    base={"PLAT":57.0,"OBSTACLE":65.0,"TROT":0}.get(disc,0)
    if base==0: return 0
    e=p-base
    if e>4:    return -6
    elif e>2:  return -4
    elif e>0:  return -2
    elif e<-2: return 3
    return 0

def feat_oeilleres(ch, disc):
    if disc=="TROT": return 0
    o=str(ch.get("oeilleres") or ch.get("equipement") or "").upper()
    if "PREMIER" in o or "1ER" in o: return 6  # 1ères œillères = gros signal
    elif "OEIL" in o: return 2
    return 0

def feat_recul_trot(ch):
    r=safe_float(ch.get("handicapDistance") or ch.get("distanceHandicap") or 0)
    if not r or r<=0: return 0
    if r<=25:   return -1
    elif r<=50: return -2
    elif r<=75: return -4
    return -6

def feat_corde_plat(ch, course, disc):
    if disc!="PLAT": return 0
    num=safe_int(ch.get("numPmu",0))
    nb=safe_int(course.get("nombreDeclaresPartants",12)) or 12
    return round(clamp((nb-num)/2,0,6),1) if num and nb else 0

def feat_corde_trot(ch, disc):
    if disc!="TROT": return 0
    num=safe_int(ch.get("numPmu",0))
    # En trot : avantage corde intérieure très marqué
    if num<=2:   return 4
    elif num<=4: return 2.5
    elif num<=7: return 1
    elif num<=10: return 0
    return -1

def feat_experience_obs(ch, disc):
    if disc!="OBSTACLE": return 0
    t=safe_int(ch.get("nombreCourses",0))
    return round(clamp(math.log(t+1)*3,0,8),1) if t else 0

def feat_gains_carriere_trot(ch, disc):
    if disc!="TROT": return 0
    g=safe_float(ch.get("gainsCarriere",0))
    return round(clamp((g or 0)/200000*8,0,8),1)

def feat_marche(ch):
    cote=safe_float((ch.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(ch.get("coteInitiale"))
    # Poids réduit (25 au lieu de 35) pour stabiliser les sélections
    return clamp(1/cote*25,0,25) if cote else 0, cote

def feat_drift(ch):
    ci=safe_float(ch.get("coteInitiale"))
    r=ch.get("dernierRapportDirect") or {}
    cf=safe_float(r.get("rapport")) if isinstance(r,dict) else None
    if not ci or not cf or ci<=0: return 0
    chute=(ci-cf)/ci
    # Amplitude plafonnée à ±4 pour stabilité
    if chute>0.30:    return 4
    elif chute>0.15:  return 2
    elif chute>0.05:  return 1
    elif chute<-0.25: return -3
    elif chute<-0.10: return -1
    return 0

def feat_classe(ch, course):
    alloc=safe_float(course.get("allocation") or course.get("montantPrix") or course.get("totalOffert"))
    if not alloc: return 0
    gains=safe_float(ch.get("gainsCarriere",0)) or 0
    nb=safe_int(ch.get("nombreCourses",0))
    if nb<3 or not gains: return 0
    niveau=(gains/nb)/0.25
    if niveau<=0: return 0
    ratio=alloc/niveau
    if ratio<0.4:    return 7
    elif ratio<0.65: return 4
    elif ratio<0.85: return 2
    elif ratio>2.5:  return -5
    elif ratio>1.5:  return -3
    elif ratio>1.15: return -1
    return 0

def feat_rk(ch, tous, disc, letrot_data=None):
    if disc!="TROT": return 0
    # Priorité aux données LeTROT si disponibles
    if letrot_data and letrot_data.get("rk_meilleur"):
        rk=letrot_data["rk_meilleur"]
        # Comparer avec les autres partants via données PMU
    rk=safe_float(ch.get("reductionKilometrique") or ch.get("rkActuel"))
    if not rk: return 0
    rks=[safe_float(p.get("reductionKilometrique") or p.get("rkActuel")) for p in tous]
    rks=[r for r in rks if r]
    if len(rks)<2: return 0
    mn,mx=min(rks),max(rks)
    if mx==mn: return 0
    return round(clamp((mx-rk)/(mx-mn)*14,0,14),1)  # Poids augmenté pour trot

def feat_distance(ch, course):
    da=safe_float(course.get("distance") or 0)
    if not da: return 0
    dp=safe_float(ch.get("distancePrefere") or ch.get("distanceOptimale"))
    if dp:
        e=abs(da-dp)/dp
        if e<0.08:   return 5
        elif e<0.20: return 2
        elif e>0.40: return -4
    return 0

def feat_jockey(ch, tous):
    jid=dict_id(ch.get("jockey")) or dict_id(ch.get("driver"))
    if not jid: return 0
    v=m=0
    for p in tous:
        j=dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        if j==jid:
            m+=1
            music=parse_musique(p.get("musique"))
            if music and music[0]==("pos",1): v+=1
    return round(clamp(v/m*10,0,6),1) if m>=3 else 0

def feat_trainer(ch, tous):
    tid=dict_id(ch.get("entraineur"))
    if not tid: return 0
    v=s=0
    for p in tous:
        if dict_id(p.get("entraineur"))==tid:
            s+=1
            music=parse_musique(p.get("musique"))
            if music and music[0]==("pos",1): v+=1
    return round(clamp(v/s*8,0,5),1) if s>=3 else 0

def feat_combo(ch, tous):
    jid=dict_id(ch.get("jockey")) or dict_id(ch.get("driver"))
    tid=dict_id(ch.get("entraineur"))
    if not jid or not tid: return 0
    v=s=0
    for p in tous:
        j=dict_id(p.get("jockey")) or dict_id(p.get("driver"))
        t=dict_id(p.get("entraineur"))
        if j==jid and t==tid:
            s+=1
            music=parse_musique(p.get("musique"))
            if music and music[0]==("pos",1): v+=1
    if s<3: return 0
    tx=v/s
    if tx>0.30:   return 6
    elif tx>0.20: return 3
    elif tx>0.10: return 1
    elif tx<0.05 and s>=5: return -2
    return 0

def feat_nb_partants(course):
    nb=safe_int(course.get("nombreDeclaresPartants",0))
    if nb==0:    return 0
    if nb<=6:    return 3
    elif nb<=9:  return 1
    elif nb<=12: return 0
    elif nb<=15: return -1
    else:        return -3

def feat_perf_rel(ch, tous):
    total=safe_int(ch.get("nombreCourses",0))
    vict=safe_int(ch.get("nombreVictoires",0))
    taux=vict/total if total>=5 else 0
    cote=safe_float((ch.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(ch.get("coteInitiale"))
    if not cote or not tous: return 0
    cotes=[safe_float((p.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(p.get("coteInitiale")) for p in tous]
    cotes=[c for c in cotes if c]
    if not cotes: return 0
    rang=sorted(cotes).index(min(cotes,key=lambda x:abs(x-cote)))+1
    ratio=rang/len(cotes)
    if taux>0.25 and ratio>0.5: return 4
    elif taux>0.15 and ratio>0.6: return 2
    return 0

def feat_outsider(ch, tous, disc):
    """
    ─── DÉTECTION OUTSIDER SYSTÉMATIQUE (Étape 1) ────────────────
    Cheval qui finit régulièrement dans les 5 premiers mais que le
    marché cote > 6. C'est le coeur du principe Benter : value.
    ──────────────────────────────────────────────────────────────
    """
    cote=safe_float((ch.get("dernierRapportDirect") or {}).get("rapport")) or safe_float(ch.get("coteInitiale"))
    if not cote or cote<6: return 0, False

    music=parse_musique(ch.get("musique",""))
    pos=[p for t,p in music if t=="pos"]
    if len(pos)<4: return 0, False

    top5_rate=sum(1 for p in pos[:6] if p<=5)/min(len(pos),6)
    total=safe_int(ch.get("nombreCourses",0))
    vict=safe_int(ch.get("nombreVictoires",0))
    tx_vict=vict/total if total>=5 else 0

    # Outsider confirmé : souvent dans top 5 + bon taux victoire + cote élevée
    if top5_rate>=0.6 and tx_vict>=0.15 and cote>=6:
        score=round(clamp(top5_rate*8+tx_vict*5,0,10),1)
        return score, True
    elif top5_rate>=0.5 and cote>=8:
        return round(top5_rate*5,1), False
    return 0, False

def feat_letrot_bonus(letrot_data):
    """Bonus depuis données LeTROT enrichies."""
    if not letrot_data: return 0
    bonus=0
    # Victoires en tête (cheval de tête = avantage en trot)
    vt=letrot_data.get("victoires_tete",0)
    bonus+=min(vt*1.5,5)
    # Positions LeTROT récentes
    pos=letrot_data.get("positions_letrot",[])
    if pos:
        moy=sum(pos)/len(pos)
        bonus+=max(0,(8-moy)*0.8)
    return round(clamp(bonus,0,8),1)

# ═══════════════════════════════════════════════════════════════
# LOGIT PRINCIPAL — calibré par discipline
# ═══════════════════════════════════════════════════════════════

def compute(ch, course, tous, disc, meteo=None, letrot_data=None):
    music=parse_musique(ch.get("musique",""))
    deb=len(music)==0

    f_forme =feat_forme(music)
    f_reg   =feat_regularite(music)
    f_prog  =feat_progression(music)
    f_place =feat_ratio_place(ch)
    f_txv   =feat_taux_victoires(ch)
    f_dis   =feat_disette(ch)
    f_rc    =feat_regularite_conditions(music)

    f_mar,cote=feat_marche(ch)
    if deb:
        f_forme=f_mar*0.4
        f_reg=f_prog=f_place=f_txv=0

    f_drift =feat_drift(ch)
    f_classe=feat_classe(ch,course)
    f_perf  =feat_perf_rel(ch,tous)
    f_fraich=feat_fraicheur(ch)
    f_gan   =feat_gains_annee(ch)
    f_sexe  =feat_sexe(ch)
    f_def   =feat_deferre(ch,disc)
    f_poids =feat_poids(ch,disc)
    f_oeil  =feat_oeilleres(ch,disc)
    f_train =feat_trainer(ch,tous)
    f_jock  =feat_jockey(ch,tous)
    f_combo =feat_combo(ch,tous)
    f_age   =feat_age(ch,disc)
    f_nb    =feat_nb_partants(course)
    f_dist  =feat_distance(ch,course)
    f_sais  =feat_saison(ch)

    hippo=""
    h=course.get("hippodrome")
    if isinstance(h,dict): hippo=h.get("code") or h.get("nom") or ""
    else: hippo=str(h or "")

    f_vh  =feat_vict_hippo(ch,hippo)
    f_vd  =feat_vict_dist(ch,course)
    f_out,is_out=feat_outsider(ch,tous,disc)

    # Features discipline-spécifiques
    f_rk    =feat_rk(ch,tous,disc,letrot_data)
    f_corde = feat_corde_trot(ch,disc) if disc=="TROT" else feat_corde_plat(ch,course,disc)
    f_obs   =feat_experience_obs(ch,disc)
    f_gc    =feat_gains_carriere_trot(ch,disc)
    f_recul =feat_recul_trot(ch) if disc=="TROT" else 0
    f_letrot=feat_letrot_bonus(letrot_data)

    # Météo (Étape 2)
    f_meteo=feat_meteo_terrain(ch,meteo,disc)

    # ── POIDS CALIBRÉS PAR DISCIPLINE (Étape 1) ─────────────────
    if disc=="TROT":
        logit=(
            f_forme *1.0 + f_reg*0.8  + f_prog*0.7  + f_place*0.7 + f_txv*0.8
            + f_mar *1.0 + f_drift*0.8 + f_classe*0.9
            + f_rk  *1.3               # RK très important en trot
            + f_fraich*0.6 + f_gan*0.5 + f_sexe*0.3
            + f_def *0.5  + f_poids*0.4
            + f_train*0.5 + f_jock*0.5 + f_combo*0.7
            + f_age *0.6               # Calibré trot
            + f_corde*0.8              # Corde très importante trot
            + f_gc  *0.7               # Gains carrière trot
            + f_recul*0.7
            + f_dist*0.8 + f_rc*0.6
            + f_vh  *0.8 + f_vd*0.7   + f_dis*0.6  + f_sais*0.3
            + f_out *0.9 + f_letrot*0.9 + f_nb*0.3
            + f_meteo*0.3              # Météo faible en trot (piste PSF)
        )
    elif disc=="OBSTACLE":
        logit=(
            f_forme *1.1 + f_reg*0.9  + f_prog*0.8  + f_place*0.8 + f_txv*0.9
            + f_mar *1.0 + f_drift*0.8 + f_classe*1.0
            + f_obs *1.0               # Expérience obstacle cruciale
            + f_fraich*0.7 + f_gan*0.5 + f_sexe*0.3
            + f_def *0.7               # Déferré important obstacle
            + f_train*0.6 + f_jock*0.6 + f_combo*0.7
            + f_age *0.7               # Calibré obstacle
            + f_poids*0.8              # Poids crucial obstacle
            + f_dist*0.9 + f_rc*0.7
            + f_vh  *0.8 + f_vd*0.8   + f_dis*0.5  + f_sais*0.3
            + f_out *0.8 + f_nb*0.3
            + f_meteo*0.6              # Météo importante obstacle (terrain lourd)
        )
    else:  # PLAT
        logit=(
            f_forme *1.0 + f_reg*0.8  + f_prog*0.7  + f_place*0.7 + f_txv*0.8
            + f_mar *1.0 + f_drift*0.8 + f_classe*0.9
            + f_perf*0.6
            + f_fraich*0.6 + f_gan*0.5 + f_sexe*0.2
            + f_def *0.4  + f_poids*0.7 + f_oeil*0.6  # Poids + œillères importants plat
            + f_train*0.5 + f_jock*0.5 + f_combo*0.7
            + f_age *0.6               # Calibré plat
            + f_corde*0.5              # Corde importante plat
            + f_dist*0.8 + f_rc*0.6
            + f_vh  *0.8 + f_vd*0.7   + f_dis*0.6  + f_sais*0.3
            + f_out *0.9 + f_nb*0.3
            + f_meteo*0.8              # Météo très importante plat
        )

    details={
        "forme":   round(f_forme,1),
        "reg":     round(f_reg,1),
        "prog":    round(f_prog,1),
        "marche":  round(f_mar,1),
        "drift":   round(f_drift,1),
        "classe":  round(f_classe,1),
        "rk":      round(f_rk,1),
        "fraich":  round(f_fraich,1),
        "deferre": round(f_def,1),
        "poids":   round(f_poids,1),
        "oeil":    round(f_oeil,1),
        "jockey":  round(f_jock,1),
        "trainer": round(f_train,1),
        "combo":   round(f_combo,1),
        "age":     round(f_age,1),
        "corde":   round(f_corde,1),
        "dist":    round(f_dist,1),
        "vh":      round(f_vh,1),
        "vd":      round(f_vd,1),
        "disette": round(f_dis,1),
        "meteo":   round(f_meteo,1),
        "outsider":round(f_out,1),
        "letrot":  round(f_letrot,1),
    }
    return logit, cote, details, is_out

# ═══════════════════════════════════════════════════════════════
# ANALYSE COURSE
# ═══════════════════════════════════════════════════════════════

def analyser_course(partants, course, reunion, meteo=None):
    disc=detect_discipline(course)
    hippo_nom=get_hippo_name(reunion,course)

    logits=[]; data=[]
    for ch in partants:
        # Enrichissement LeTROT pour le trot
        letrot_data=None
        if disc=="TROT":
            nom=ch.get("nom","")
            if nom:
                letrot_data=get_letrot_data(nom)
        try:
            l,cote,det,is_out=compute(ch,course,partants,disc,meteo,letrot_data)
            logits.append(l)
            data.append({"ch":ch,"cote":cote,"det":det,"logit":l,"is_out":is_out,"letrot":letrot_data})
        except Exception as e:
            logits.append(0)
            data.append({"ch":ch,"cote":None,"det":{},"logit":0,"is_out":False,"letrot":None})

    TEMPERATURE=10.0
    probs_m=softmax([l/TEMPERATURE for l in logits])
    MAX_P=0.70
    pm=list(probs_m)
    for _ in range(10):
        surplus=sum(max(0,p-MAX_P) for p in pm)
        if surplus<1e-9: break
        sous=[i for i,p in enumerate(pm) if p<MAX_P]
        if not sous: break
        r=surplus/len(sous)
        pm=[min(p,MAX_P) for p in pm]
        for i in sous: pm[i]+=r

    cotes_b=[d["cote"] for d in data]
    pb=[1/c if c and c>0 else 0 for c in cotes_b]
    sp=sum(pb)
    pm_mkt=[p/sp if sp>0 else 0 for p in pb]

    res=[]
    for d,p_mod,p_mkt in zip(data,pm,pm_mkt):
        if not d.get("cote"): continue
        ch=d["ch"]
        value=round((p_mod-p_mkt)*100,1)
        confiance=round(clamp(p_mod*100+d["det"].get("drift",0)*2,0,99),1)
        res.append({
            "nom":     ch.get("nom","?"),
            "num":     ch.get("numPmu","?"),
            "cote":    d["cote"],
            "prob":    round(p_mod*100,1),
            "prob_m":  round(p_mkt*100,1),
            "value":   value,
            "confiance":confiance,
            "logit":   round(d["logit"],1),
            "musique": ch.get("musique","")[:8],
            "jockey":  ((ch.get("jockey") or ch.get("driver") or {}).get("nom","?")),
            "age":     ch.get("age","?"),
            "det":     d["det"],
            "is_out":  d["is_out"],
            "disc":    disc,
            "hippo":   hippo_nom,
            "letrot":  d.get("letrot"),
        })
    return res, disc

# ─── CACHE SÉLECTIONS ─────────────────────────────────────────────
def cache_key(): return f"sel_{datetime.now().strftime('%Y%m%d')}"
def get_cache(): return st.session_state.get(cache_key())
def set_cache(d): st.session_state[cache_key()]=d

# ─── JOURNAL ──────────────────────────────────────────────────────
JOURNAL_RAW="https://raw.githubusercontent.com/chapsizator-bit/pronostics-pmu/main/journal.json"
JOURNAL_API="https://api.github.com/repos/chapsizator-bit/pronostics-pmu/contents/journal.json"

@st.cache_data(ttl=30)
def load_journal():
    try:
        req=urllib.request.Request(JOURNAL_RAW+"?t="+str(int(time.time())),headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception: return []

def save_journal(entries):
    token=os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token: return False,"Token GitHub manquant"
    headers={"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json","User-Agent":"Mozilla/5.0"}
    sha=None
    try:
        req=urllib.request.Request(JOURNAL_API,headers=headers)
        with urllib.request.urlopen(req,timeout=8) as r:
            sha=json.loads(r.read()).get("sha")
    except Exception: pass
    cb64=base64.b64encode(json.dumps(entries,ensure_ascii=False,indent=2).encode()).decode()
    payload={"message":"journal: résultat ajouté","content":cb64}
    if sha: payload["sha"]=sha
    try:
        req2=urllib.request.Request(JOURNAL_API,data=json.dumps(payload).encode(),headers=headers,method="PUT")
        with urllib.request.urlopen(req2,timeout=10) as r:
            return r.status in(200,201),""
    except Exception as e: return False,str(e)

def journal_stats(entries):
    if not entries: return {}
    total=len(entries)
    wins=sum(1 for e in entries if e.get("resultat")=="gagné")
    places=sum(1 for e in entries if e.get("resultat")=="placé")
    pertes=total-wins-places
    roi=sum((e.get("cote",1)-1) if e.get("resultat")=="gagné" else(0 if e.get("resultat")=="placé" else -1) for e in entries)
    return {"total":total,"wins":wins,"places":places,"pertes":pertes,
            "taux_v":round(wins/total*100,1),"taux_p":round((wins+places)/total*100,1),
            "roi":round(roi/total*100,1),"roi_abs":round(roi,2)}

# ═══════════════════════════════════════════════════════════════
# INTERFACE STREAMLIT
# ═══════════════════════════════════════════════════════════════

st.set_page_config(page_title="🏇 Benter PMU", page_icon="🏇", layout="centered")
st.markdown("""
<style>
.block-container{padding-top:1rem;padding-bottom:1rem}
.card{background:#161b27;border:1px solid #1e2535;border-radius:12px;padding:14px 16px;margin-bottom:12px}
.horse-name{font-size:1.2rem;font-weight:800;color:#fff}
.sub{color:#6b7280;font-size:0.8rem;margin-top:2px}
.badge-fort{background:#3b1a1a;color:#f87171;padding:2px 8px;border-radius:5px;font-size:0.75rem;font-weight:700;display:inline-block;margin:3px 2px}
.badge-val{background:#2d2a0f;color:#fbbf24;padding:2px 8px;border-radius:5px;font-size:0.75rem;font-weight:700;display:inline-block;margin:3px 2px}
.badge-out{background:#0d2a1f;color:#34d399;padding:2px 8px;border-radius:5px;font-size:0.75rem;font-weight:700;display:inline-block;margin:3px 2px}
.badge-letrot{background:#1a1a3d;color:#818cf8;padding:2px 8px;border-radius:5px;font-size:0.75rem;font-weight:700;display:inline-block;margin:3px 2px}
.pills{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.pill{background:#0f1117;border-radius:5px;padding:2px 7px;font-size:0.7rem;color:#9ca3af}
.pill.pos{color:#4ade80}.pill.neg{color:#f87171}
.frozen{background:#1a2a1a;border:1px solid #4ade8044;border-radius:8px;padding:8px 14px;margin-bottom:10px;color:#86efac;font-size:0.82rem}
.meteo-box{background:#0f1a2a;border:1px solid #1e3a5f;border-radius:8px;padding:8px 14px;margin-bottom:10px;font-size:0.82rem;color:#93c5fd}
/* Mobile */
@media(max-width:600px){
  .horse-name{font-size:1rem}
  .pills{gap:3px}
  .pill{font-size:0.65rem;padding:1px 5px}
}
</style>
""", unsafe_allow_html=True)

st.title("🏇 Benter PMU")
st.caption(datetime.now().strftime("%A %d %B %Y"))

# ─── SIDEBAR ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Filtres")

    # ÉTAPE 1 : Filtre par discipline
    disc_filtre=st.multiselect(
        "Disciplines",
        ["TROT","PLAT","OBSTACLE"],
        default=["TROT","PLAT","OBSTACLE"],
        help="Filtrer par type de course"
    )

    min_value=st.slider("Avantage marché min (%)",-10,20,3)
    min_conf =st.slider("Confiance minimum",0,99,60)
    max_sel  =st.slider("Sélections max",1,10,3)

    st.divider()
    afficher_outsiders=st.checkbox("⚡ Afficher outsiders détectés",value=True)
    afficher_letrot   =st.checkbox("🔵 Enrichissement LeTROT (trot)",value=True,help="Ralentit légèrement l'analyse")

    st.divider()
    if st.button("🗑️ Recalculer",use_container_width=True):
        k=cache_key()
        if k in st.session_state: del st.session_state[k]
        st.rerun()

    st.divider()
    st.caption("**v3.0 — 31 critères**")
    st.caption("Forme · Régularité · Progression · Ratio placé · Taux victoires · Disette/Série")
    st.caption("Marché · Drift · Classe · Perf.rel. · RK trot")
    st.caption("Fraîcheur · Partants · Gains/an · Déferré · Poids · Œillères")
    st.caption("Jockey · Entraîneur · Combo J+E · Âge (calibré) · Corde (calibrée)")
    st.caption("Distance · V.Hippodrome · V.Distance · Saisonnalité · Rég.conditions")
    st.caption("🆕 **Météo · Outsider · LeTROT · Poids discipline**")

# ─── AFFICHAGE SÉLECTIONS ─────────────────────────────────────────
def pill(l,v):
    if v is None or v==0: return f'<span class="pill">{l} 0</span>'
    c="pos" if v>0 else "neg"
    s="+" if v>0 else ""
    return f'<span class="pill {c}">{l} {s}{v}</span>'

def afficher_card(c, rank, medals):
    b=c.get("det",{})
    m=medals[rank] if rank<len(medals) else f"#{rank+1}"
    badges=""
    if c.get("value",0)>=10 and c.get("confiance",0)>=75:
        badges+='<span class="badge-fort">🔥 PARI FORT</span>'
    else:
        badges+='<span class="badge-val">⚡ VALUE</span>'
    if c.get("is_out"):
        badges+='<span class="badge-out">🎯 OUTSIDER</span>'
    if c.get("letrot"):
        badges+='<span class="badge-letrot">🔵 LeTROT</span>'

    hippo=f" · {c['hippo']}" if c.get("hippo") else ""
    disc_badge={"TROT":"🏃","PLAT":"🐎","OBSTACLE":"🚧"}.get(c.get("disc",""),"")

    pills="".join([
        pill("Forme",    b.get("forme")),
        pill("Rég.",     b.get("reg")),
        pill("Prog.",    b.get("prog")),
        pill("Marché",   b.get("marche")),
        pill("Drift",    b.get("drift")),
        pill("Classe",   b.get("classe")),
        pill("RK",       b.get("rk")),
        pill("Dist.",    b.get("dist")),
        pill("Corde",    b.get("corde")),
        pill("Âge",      b.get("age")),
        pill("Fraîch.",  b.get("fraich")),
        pill("Déf.",     b.get("deferre")),
        pill("Poids",    b.get("poids")),
        pill("Œill.",    b.get("oeil")),
        pill("Jockey",   b.get("jockey")),
        pill("Trainer",  b.get("trainer")),
        pill("Combo",    b.get("combo")),
        pill("V.Piste",  b.get("vh")),
        pill("V.Dist.",  b.get("vd")),
        pill("Série",    b.get("disette")),
        pill("Météo",    b.get("meteo")),
        pill("Outsider", b.get("outsider")),
        pill("LeTROT",   b.get("letrot")),
    ])

    st.markdown(f"""
    <div class="card">
      <div class="horse-name">{m} N°{c['num']} {c['nom']} {disc_badge}</div>
      <div class="sub">{c.get('course','?')}{hippo} · {c.get('jockey','?')} · {c.get('age','?')}a</div>
      <div style="margin:6px 0">{badges}</div>
      <div class="pills">{pills}</div>
    </div>
    """, unsafe_allow_html=True)

    col1,col2,col3,col4=st.columns(4)
    col1.metric("Prob. modèle",f"{c['prob']}%")
    col2.metric("Prob. marché",f"{c['prob_m']}%")
    col3.metric("Avantage",    f"{c['value']:+.1f}%")
    col4.metric("Cote",        f"{c['cote']}×" if c.get('cote') else "—")

def afficher_resultats(gardes, candidats, courses, meteo_cache):
    if not gardes:
        st.warning("❌ Aucun pari — aucun cheval ne passe les filtres.")
        top3=sorted(candidats,key=lambda x:x["value"],reverse=True)[:3]
        if top3:
            st.caption("Top 3 toutes courses confondues :")
            for c in top3:
                st.caption(f"• N°{c['num']} {c['nom']} ({c.get('course','?')}) — Value {c['value']:+.1f}%")
        return

    medals=["🥇","🥈","🥉"]+["🔹"]*20
    disc_gardes=list({c.get("disc") for c in gardes})
    st.success(f"🔥 {len(gardes)} sélection(s) · disciplines : {', '.join(disc_gardes)}")

    # Affichage météo par hippodrome
    hippos_vus=set()
    for c in gardes:
        hippo=c.get("hippo","")
        if hippo and hippo not in hippos_vus:
            hippos_vus.add(hippo)
            m=meteo_cache.get(hippo)
            if m:
                lab,ico=meteo_label(m)
                st.markdown(f'<div class="meteo-box">{ico} <b>{hippo}</b> — {lab} · {m["temp"]:.0f}°C · Vent {m["vent"]:.0f}km/h</div>',unsafe_allow_html=True)

    for i,c in enumerate(gardes):
        afficher_card(c, i, medals)
        st.markdown("---")

    # Outsiders supplémentaires
    if afficher_outsiders:
        out_extras=[c for c in candidats if c.get("is_out") and c not in gardes]
        if out_extras:
            with st.expander(f"🎯 {len(out_extras)} outsider(s) supplémentaire(s) détecté(s)"):
                for c in out_extras[:5]:
                    st.markdown(f"**N°{c['num']} {c['nom']}** ({c.get('course','?')}) — Cote {c['cote']} · Value {c['value']:+.1f}% · Prob {c['prob']}% vs {c['prob_m']}% marché")

    st.caption(f"Filtres : value ≥ {min_value}% · confiance ≥ {min_conf}% · {', '.join(disc_filtre)}")
    st.caption(f"{len(candidats)} chevaux analysés · {len(courses)} courses")

# ─── BOUTONS PRINCIPAUX ───────────────────────────────────────────
col_btn,col_force=st.columns([3,1])
lancer=col_btn.button("🔍 Analyser les courses du jour",use_container_width=True,type="primary")
force =col_force.button("↺ Forcer",help="Recalcule tout")

cached=get_cache()

if force:
    k=cache_key()
    if k in st.session_state: del st.session_state[k]
    cached=None; lancer=True

if cached:
    gardes    =cached["gardes"]
    candidats =cached["candidats"]
    courses   =cached["courses"]
    meteo_c   =cached.get("meteo_cache",{})
    st.markdown(f'<div class="frozen">🔒 Sélections figées à {cached["heure"]} — stables toute la journée. ↺ Forcer pour recalculer.</div>',unsafe_allow_html=True)
    st.session_state["gardes_du_jour"]=gardes
    afficher_resultats(gardes,candidats,courses,meteo_c)

elif lancer:
    with st.spinner("Chargement du programme PMU…"):
        try:
            data=pmu_get(f"/{today()}/reunions")
            reunions=(data.get("programme") or {}).get("reunions") or data.get("reunions") or []
        except Exception as e:
            st.error(f"Impossible de charger le programme PMU : {e}")
            st.stop()

    courses=[]
    for r in reunions:
        for c in (r.get("courses") or []):
            courses.append((r,c))

    st.info(f"📋 {len(reunions)} réunions · {len(courses)} courses")

    # Chargement météo par hippodrome
    meteo_cache={}
    with st.spinner("Météo des hippodromes…"):
        hippos_traites=set()
        for r,c in courses:
            nom=get_hippo_name(r,c)
            if nom not in hippos_traites:
                hippos_traites.add(nom)
                gps=get_hippo_gps(nom)
                if gps:
                    m=get_meteo(gps[0],gps[1])
                    if m: meteo_cache[nom]=m

    candidats=[]; erreurs=[]
    bar=st.progress(0,text="Analyse…")

    for i,(r,c) in enumerate(courses):
        bar.progress((i+1)/len(courses),text=f"Course {i+1}/{len(courses)}…")

        # Filtre discipline (Étape 1)
        disc_c=detect_discipline(c)
        if disc_c not in disc_filtre: continue

        nr=r.get("numOfficiel") or r.get("numOrdre") or r.get("numeroReunion") or r.get("num")
        nc=c.get("numOfficiel") or c.get("numOrdre") or c.get("numCourse") or c.get("num")
        if not nr or not nc: continue

        try:
            pdata=pmu_get(f"/{today()}/R{nr}/C{nc}/participants")
            partants=pdata.get("participants") or []
            if not partants: continue
        except Exception as e:
            erreurs.append(f"R{nr}C{nc} API: {e}"); continue

        try:
            hippo_nom=get_hippo_name(r,c)
            meteo=meteo_cache.get(hippo_nom)
            res,disc=analyser_course(partants,c,r,meteo)
            for rr in res:
                rr["course"]=f"R{nr}C{nc}"
            candidats.extend(res)
        except Exception as e:
            erreurs.append(f"R{nr}C{nc} analyse: {type(e).__name__}: {e}")

    bar.empty()
    if erreurs:
        with st.expander(f"⚠️ {len(erreurs)} erreur(s)"):
            for e in erreurs[:8]: st.caption(f"• {e}")

    gardes=[c for c in candidats if c["value"]>=min_value and c["confiance"]>=min_conf and c.get("cote")]
    gardes.sort(key=lambda x:(x["value"],x["confiance"]),reverse=True)
    gardes=gardes[:max_sel]

    set_cache({"gardes":gardes,"candidats":candidats,"courses":courses,"heure":datetime.now().strftime("%H:%M"),"meteo_cache":meteo_cache})
    st.session_state["gardes_du_jour"]=gardes
    afficher_resultats(gardes,candidats,courses,meteo_cache)

# ═══════════════════════════════════════════════════════════════
# JOURNAL
# ═══════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## 📓 Journal")
journal=load_journal()
tab_add,tab_stats=st.tabs(["➕ Enregistrer","📊 Statistiques"])

with tab_add:
    gardes_s=st.session_state.get("gardes_du_jour",[])
    choix=["— Saisie manuelle —"]+[f"N°{g['num']} {g['nom']} ({g.get('course','?')})" for g in gardes_s]
    sel=st.selectbox("Cheval du jour",choix)
    garde_sel=None
    if sel!="— Saisie manuelle —":
        idx=choix.index(sel)-1
        if 0<=idx<len(gardes_s): garde_sel=gardes_s[idx]

    with st.form("form_j",clear_on_submit=True):
        c1,c2=st.columns(2)
        d_val=c1.date_input("Date",value=date.today())
        course_val=c2.text_input("Course",value=garde_sel["course"]+" "+garde_sel.get("disc","") if garde_sel else "",placeholder="R2C3 TROT")
        c3,c4,c5=st.columns(3)
        cheval_val=c3.text_input("Cheval",value=garde_sel["nom"] if garde_sel else "")
        num_val=c4.number_input("N°",min_value=1,max_value=30,value=int(garde_sel["num"]) if garde_sel else 1,step=1)
        cote_val=c5.number_input("Cote",min_value=1.0,value=float(garde_sel["cote"]) if garde_sel and garde_sel.get("cote") else 5.0,step=0.5)
        res_val=st.radio("Résultat",["gagné","placé","perdu"],horizontal=True)
        sub=st.form_submit_button("💾 Enregistrer",use_container_width=True)
        if sub:
            if not cheval_val.strip():
                st.warning("Indique le nom du cheval.")
            else:
                entry={"id":str(int(time.time())),"date":str(d_val),"course":course_val.strip(),
                       "cheval":cheval_val.strip().upper(),"num":int(num_val),"cote":float(cote_val),
                       "resultat":res_val,"details":garde_sel.get("det",{}) if garde_sel else {},
                       "value":garde_sel.get("value",0) if garde_sel else 0}
                ok,err=save_journal(journal+[entry])
                if ok:
                    st.success(f"✅ {entry['cheval']} — {res_val}")
                    load_journal.clear()
                else:
                    st.error(f"❌ {err or 'Token GitHub manquant'}")

with tab_stats:
    if not journal:
        st.info("Aucun résultat enregistré.")
    else:
        s=journal_stats(journal)
        roi_c="green" if s["roi"]>=0 else "red"
        st.markdown(f"""
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px">
          <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:90px">
            <div style="font-size:1.5rem;font-weight:800">{s['total']}</div>
            <div style="color:#94a3b8;font-size:0.75rem">Sélections</div>
          </div>
          <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:90px">
            <div style="font-size:1.5rem;font-weight:800;color:#4ade80">{s['taux_v']}%</div>
            <div style="color:#94a3b8;font-size:0.75rem">Taux victoire</div>
          </div>
          <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:90px">
            <div style="font-size:1.5rem;font-weight:800;color:#60a5fa">{s['taux_p']}%</div>
            <div style="color:#94a3b8;font-size:0.75rem">Taux place</div>
          </div>
          <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:90px">
            <div style="font-size:1.5rem;font-weight:800;color:{roi_c}">{s['roi']:+.1f}%</div>
            <div style="color:#94a3b8;font-size:0.75rem">ROI</div>
          </div>
          <div style="background:#1e1e2e;border-radius:10px;padding:12px 20px;text-align:center;min-width:90px">
            <div style="font-size:1.5rem;font-weight:800;color:{roi_c}">{s['roi_abs']:+.2f}u</div>
            <div style="color:#94a3b8;font-size:0.75rem">Bénéfice</div>
          </div>
        </div>
        <div style="color:#64748b;font-size:0.8rem">✅ {s['wins']} gagné · 🏅 {s['places']} placé · ❌ {s['pertes']} perdu</div>
        """,unsafe_allow_html=True)

        # Par semaine
        st.markdown("### Par semaine")
        import datetime as dt_mod
        semaines={}
        for e in journal:
            try:
                dobj=datetime.strptime(e["date"],"%Y-%m-%d").date()
                lundi=dobj-dt_mod.timedelta(days=dobj.weekday())
                semaines.setdefault(str(lundi),[]).append(e)
            except Exception: semaines.setdefault("?",[]).append(e)

        if "del_id" in st.session_state and st.session_state["del_id"]:
            did=st.session_state.pop("del_id")
            jmod=[e for e in journal if e.get("id")!=did]
            ok,err=save_journal(jmod)
            if ok: load_journal.clear(); st.rerun()
            else: st.error(f"Erreur suppression : {err}")

        for sem in sorted(semaines.keys(),reverse=True):
            es=semaines[sem]; ss=journal_stats(es)
            ico="🟢" if ss["roi"]>=0 else "🔴"
            with st.expander(f"{ico} Semaine {sem} — {ss['total']} sél. · ROI {ss['roi']:+.1f}%"):
                for e in sorted(es,key=lambda x:x.get("date",""),reverse=True):
                    icon="✅" if e["resultat"]=="gagné" else("🏅" if e["resultat"]=="placé" else "❌")
                    ct,cb=st.columns([5,1])
                    ct.markdown(f"{icon} **{e.get('cheval','?')}** (N°{e.get('num','?')}) — {e.get('course','?')} · {e.get('cote','?')}× · {e.get('date','')}")
                    if cb.button("🗑️",key=f"d_{e.get('id','')}"):
                        st.session_state["del_id"]=e.get("id",""); st.rerun()

        # Analyse par critère
        avec_det=[e for e in journal if e.get("details")]
        if avec_det:
            st.markdown("### 🔬 Analyse par critère")
            criteres={
                "forme":"Forme","reg":"Régularité","prog":"Progression",
                "marche":"Marché","drift":"Drift","classe":"Classe","rk":"RK trot",
                "dist":"Distance","fraich":"Fraîcheur","deferre":"Déferré",
                "poids":"Poids","oeil":"Œillères","jockey":"Jockey","trainer":"Entraîneur",
                "combo":"Combo J+E","age":"Âge","corde":"Corde",
                "vh":"V.Hippodrome","vd":"V.Distance","disette":"Série/Disette",
                "meteo":"Météo","outsider":"Outsider","letrot":"LeTROT",
            }
            lignes=[]
            for key,label in criteres.items():
                avec=[e for e in avec_det if (e["details"].get(key) or 0)>0]
                if len(avec)<2: continue
                wv=sum(1 for e in avec if e["resultat"]=="gagné")
                taux=round(wv/len(avec)*100,1)
                roi_c2=sum((e.get("cote",1)-1) if e["resultat"]=="gagné" else(0 if e["resultat"]=="placé" else -1) for e in avec)
                roi_p=round(roi_c2/len(avec)*100,1)
                lignes.append((label,len(avec),taux,roi_p))
            if lignes:
                lignes.sort(key=lambda x:x[2],reverse=True)
                rows=""
                for lbl,n,tx,roi_p in lignes:
                    bc="#4ade80" if tx>=30 else("#facc15" if tx>=15 else "#f87171")
                    rc="#4ade80" if roi_p>=0 else "#f87171"
                    rows+=f"""<tr>
                      <td style="padding:5px 10px;color:#e2e8f0">{lbl}</td>
                      <td style="padding:5px 10px;color:#94a3b8;text-align:center">{n}</td>
                      <td style="padding:5px 10px">
                        <div style="background:#374151;border-radius:3px;height:12px;width:100px">
                          <div style="background:{bc};border-radius:3px;height:12px;width:{int(tx)}%"></div>
                        </div>
                        <span style="color:{bc};font-size:0.75rem;font-weight:700">{tx}%</span>
                      </td>
                      <td style="padding:5px 10px;color:{rc};font-weight:700;text-align:right">{roi_p:+.1f}%</td>
                    </tr>"""
                st.markdown(f"""<table style="width:100%;border-collapse:collapse;background:#111827;border-radius:10px;overflow:hidden">
                  <thead><tr style="background:#1e293b">
                    <th style="padding:7px 10px;text-align:left;color:#94a3b8;font-size:0.75rem">Critère</th>
                    <th style="padding:7px 10px;text-align:center;color:#94a3b8;font-size:0.75rem">N</th>
                    <th style="padding:7px 10px;text-align:left;color:#94a3b8;font-size:0.75rem">Taux victoire</th>
                    <th style="padding:7px 10px;text-align:right;color:#94a3b8;font-size:0.75rem">ROI</th>
                  </tr></thead><tbody>{rows}</tbody></table>""",unsafe_allow_html=True)
                st.caption("⚠️ Fiable après 20+ sélections enregistrées.")
