"""
diagnose.py — Outil de diagnostic pour l'instance Tomate prod
==============================================================

Teste chaque couche du bridge indépendamment et produit un rapport
précis sur ce qui fonctionne, ce qui est cassé, et comment corriger.

Usage :
    python diagnose.py --url https://tomate-dev.ensaejunioretudes.fr \
                       --email admin@ensaeje.fr --password VOTRE_MDP

Sans credentials (test réseau uniquement) :
    python diagnose.py --url https://tomate-dev.ensaejunioretudes.fr

Résultat : rapport coloré dans le terminal + fichier diagnose_report.json
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ─────────────────────────────────────────────────────────────────────────────
# Couleurs terminal (fonctionne sur macOS, Linux, Windows 10+)
# ─────────────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
GRAY   = "\033[90m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN}✓{RESET}  {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET}  {msg}")
def err(msg):   print(f"  {RED}✗{RESET}  {msg}")
def info(msg):  print(f"  {BLUE}→{RESET}  {msg}")
def head(msg):  print(f"\n{BOLD}{msg}{RESET}")
def gray(msg):  print(f"  {GRAY}{msg}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Résultats
# ─────────────────────────────────────────────────────────────────────────────

class Check:
    def __init__(self, name: str):
        self.name    = name
        self.status  = "pending"   # ok | warn | fail | skip
        self.message = ""
        self.detail  = ""
        self.fix     = ""

    def passed(self, msg="", detail=""):
        self.status = "ok"; self.message = msg; self.detail = detail
        return self

    def warning(self, msg="", detail="", fix=""):
        self.status = "warn"; self.message = msg; self.detail = detail; self.fix = fix
        return self

    def failed(self, msg="", detail="", fix=""):
        self.status = "fail"; self.message = msg; self.detail = detail; self.fix = fix
        return self

    def skipped(self, reason=""):
        self.status = "skip"; self.message = reason
        return self

    def to_dict(self):
        return {k: v for k, v in vars(self).items() if v}


class DiagReport:
    def __init__(self, base_url: str):
        self.base_url  = base_url
        self.timestamp = datetime.now().isoformat()
        self.checks: list[Check] = []

    def add(self, c: Check) -> Check:
        self.checks.append(c)
        return c

    @property
    def n_ok(self):   return sum(1 for c in self.checks if c.status == "ok")
    @property
    def n_warn(self): return sum(1 for c in self.checks if c.status == "warn")
    @property
    def n_fail(self): return sum(1 for c in self.checks if c.status == "fail")

    def to_dict(self):
        return {
            "base_url":  self.base_url,
            "timestamp": self.timestamp,
            "summary":   {"ok": self.n_ok, "warn": self.n_warn, "fail": self.n_fail},
            "checks":    [c.to_dict() for c in self.checks],
        }

    def save(self, path="diagnose_report.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return path


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions de diagnostic
# ─────────────────────────────────────────────────────────────────────────────

def check_requests(report: DiagReport) -> bool:
    c = Check("requests_installed")
    if HAS_REQUESTS:
        import requests as r
        report.add(c.passed(f"requests {r.__version__} installé"))
        return True
    else:
        report.add(c.failed(
            "Module 'requests' manquant",
            fix="pip install requests"
        ))
        err("Module 'requests' non installé. Lancer : pip install requests")
        return False


def check_dns(report: DiagReport, base_url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(base_url).hostname
    c = Check("dns_resolution")
    try:
        import socket
        ip = socket.gethostbyname(host)
        report.add(c.passed(f"{host} → {ip}"))
        ok(f"DNS résolu : {host} → {ip}")
        return True
    except Exception as e:
        report.add(c.failed(
            f"Impossible de résoudre {host}",
            detail=str(e),
            fix="Vérifier que le domaine est accessible depuis votre réseau / VPN"
        ))
        err(f"DNS : impossible de résoudre {host}")
        return False


def check_https(report: DiagReport, session: "requests.Session", base_url: str) -> bool:
    c = Check("https_reachable")
    try:
        t0 = time.time()
        resp = session.get(base_url + "/SignIn/", timeout=10, allow_redirects=True)
        ms = int((time.time() - t0) * 1000)
        gray(f"GET /SignIn/ → HTTP {resp.status_code} ({ms} ms) — URL finale : {resp.url}")

        if resp.status_code == 200:
            # Vérifier que c'est bien du HTML Tomate et pas une page d'erreur Apache
            is_tomate = "Tomate" in resp.text or "SignIn" in resp.text or "angular" in resp.text.lower()
            if is_tomate:
                report.add(c.passed(f"HTTP 200, page Tomate détectée ({ms} ms)"))
                ok(f"Tomate accessible ({ms} ms)")
                return True
            else:
                report.add(c.warning(
                    f"HTTP 200 mais contenu inattendu",
                    detail=resp.text[:300],
                    fix="Vérifier que l'URL pointe bien vers la racine de Tomate, pas vers Apache"
                ))
                warn("HTTP 200 mais la page ne ressemble pas à Tomate")
                gray(f"Extrait : {resp.text[:150].strip()}")
                return True  # on continue quand même

        elif resp.status_code in (301, 302):
            location = resp.headers.get("Location", "?")
            report.add(c.warning(
                f"Redirect vers {location}",
                fix="Utiliser directement l'URL cible comme base_url"
            ))
            warn(f"Redirect {resp.status_code} → {location}")
            return True

        elif resp.status_code == 403:
            report.add(c.failed(
                "403 Forbidden sur /SignIn/",
                fix="Vérifier les permissions .htaccess ou le pare-feu du serveur"
            ))
            err("403 Forbidden — accès refusé par le serveur")
            return False

        elif resp.status_code == 404:
            report.add(c.failed(
                "404 sur /SignIn/ — route introuvable",
                fix="Vérifier que l'URL de base est correcte (pas de sous-dossier manquant)"
            ))
            err("404 — la route /SignIn/ n'existe pas sur ce serveur")
            return False

        else:
            report.add(c.failed(f"HTTP {resp.status_code} inattendu"))
            err(f"Réponse inattendue : HTTP {resp.status_code}")
            return False

    except requests.exceptions.SSLError as e:
        report.add(c.failed(
            "Erreur SSL",
            detail=str(e),
            fix="Le certificat SSL est peut-être expiré ou auto-signé. "
                "Ajouter verify=False en dev UNIQUEMENT."
        ))
        err(f"Erreur SSL : {e}")
        return False
    except requests.exceptions.ConnectionError as e:
        report.add(c.failed("Impossible de se connecter", detail=str(e)))
        err(f"Connexion refusée : {e}")
        return False
    except Exception as e:
        report.add(c.failed("Erreur inattendue", detail=str(e)))
        err(f"Erreur : {e}")
        return False


def check_login(report: DiagReport, session: "requests.Session",
                base_url: str, email: str, password: str) -> tuple[bool, dict]:
    """Tente le login et retourne (succès, cookies)."""
    LOGIN_PATH = "/Auth/AJAX/SignIn/"
    c = Check("login")
    url = base_url + LOGIN_PATH
    info(f"POST {LOGIN_PATH}")

    try:
        resp = session.post(
            url,
            json={"mail": email, "password": password},
            timeout=10,
            allow_redirects=True,
        )
        gray(f"HTTP {resp.status_code} — Content-Type: {resp.headers.get('Content-Type','?')}")

        # Cas 1 : réponse JSON attendue
        try:
            data = resp.json()
        except ValueError:
            # Cas 2 : HTML → route incorrecte ou redirect vers page login
            if resp.status_code in (301, 302):
                location = resp.headers.get("Location", "?")
                report.add(c.failed(
                    f"Redirect {resp.status_code} au lieu de JSON",
                    detail=f"Redirect vers : {location}",
                    fix=f"La route de login a peut-être changé. "
                        f"Vérifier dans modules/auth/config/routes.config sur le serveur. "
                        f"URL redirigée : {location}"
                ))
                err(f"Route de login renvoie un redirect → {location}")
            else:
                snippet = resp.text[:200].strip()
                report.add(c.failed(
                    "Réponse non-JSON",
                    detail=f"HTTP {resp.status_code} : {snippet}",
                    fix="Vérifier que la route /Auth/AJAX/SignIn/ existe bien sur cette instance. "
                        "Inspecter l'onglet Network du navigateur sur la page de login Tomate."
                ))
                err(f"Réponse non-JSON (HTTP {resp.status_code})")
                gray(f"Extrait : {snippet}")
            return False, {}

        gray(f"JSON reçu : {json.dumps(data)[:200]}")

        if data.get("res"):
            cookies = {k: v for k, v in session.cookies.items()}
            report.add(c.passed(
                "Login réussi",
                detail=f"Cookies : {list(cookies.keys())}"
            ))
            ok(f"Authentification réussie — cookies : {list(cookies.keys())}")
            return True, data
        else:
            msg = data.get("msg", "Identifiants invalides")
            report.add(c.failed(
                f"Identifiants refusés : {msg}",
                fix="Vérifier email/mot de passe. "
                    "Confirmer que le compte a level >= 2 dans la table etudiant."
            ))
            err(f"Login refusé : {msg}")
            return False, {}

    except Exception as e:
        report.add(c.failed("Erreur lors du login", detail=str(e)))
        err(f"Exception : {e}")
        return False, {}


def check_ajax_route(report: DiagReport, session: "requests.Session",
                     base_url: str, path: str, payload: dict,
                     expected_field: str | None = None) -> tuple[bool, dict]:
    """Teste un endpoint Ajax avec un payload minimal et analyse la réponse."""
    c = Check(f"ajax_{path.strip('/').replace('/', '_')}")
    url = base_url + path
    info(f"POST {path}")

    try:
        resp = session.post(url, json=payload, timeout=10, allow_redirects=True)
        gray(f"HTTP {resp.status_code}")

        # Redirect vers login = session expirée ou niveau insuffisant
        if resp.status_code in (301, 302) or (
            resp.status_code == 200 and "SignIn" in resp.url
        ):
            report.add(c.failed(
                f"Redirect vers login — session ou level insuffisant",
                detail=f"URL finale : {resp.url}",
                fix="Vérifier que le compte a bien level >= 2 (admin) dans Tomate. "
                    "Un level 1 (étudiant) n'a pas accès aux routes Admin/Ajax."
            ))
            err(f"{path} → redirect vers login (level insuffisant ?)")
            return False, {}

        try:
            data = resp.json()
        except ValueError:
            snippet = resp.text[:200].strip()
            report.add(c.failed(
                "Réponse non-JSON",
                detail=f"HTTP {resp.status_code} : {snippet}",
                fix=f"La route {path} n'existe peut-être pas sur cette version de Tomate."
            ))
            err(f"{path} — réponse non-JSON")
            gray(f"Extrait : {snippet}")
            return False, {}

        gray(f"JSON : {json.dumps(data)[:300]}")

        if data.get("res") is True:
            detail = ""
            if expected_field and expected_field in data:
                entity = data[expected_field]
                eid = entity.get("id") if isinstance(entity, dict) else None
                detail = f"id créé : {eid}"
            report.add(c.passed(f"{path} répond correctement", detail=detail))
            ok(f"{path} → OK {('('+detail+')') if detail else ''}")
            return True, data

        elif data.get("res") is False:
            msg = data.get("msg", "erreur inconnue")
            # "res: false" avec un message métier n'est pas un bug réseau —
            # la route fonctionne, le payload de test est juste invalide
            report.add(c.warning(
                f"Route accessible, réponse métier : {msg}",
                fix="La route répond mais rejette le payload de test (normal). "
                    "Le vrai push avec les données complètes devrait fonctionner."
            ))
            warn(f"{path} → route OK mais payload test rejeté : {msg}")
            return True, data  # la route existe et répond en JSON → c'est bon

        else:
            report.add(c.warning(f"Format de réponse inattendu", detail=str(data)[:200]))
            warn(f"{path} — format inattendu : {str(data)[:100]}")
            return True, data

    except Exception as e:
        report.add(c.failed(f"Exception sur {path}", detail=str(e)))
        err(f"{path} — exception : {e}")
        return False, {}


def check_admin_access(report: DiagReport, session: "requests.Session",
                        base_url: str) -> bool:
    """Vérifie qu'on peut accéder à une page admin (niveau 2)."""
    c = Check("admin_page_access")
    info("GET /Admin/Etudes/")
    try:
        resp = session.get(base_url + "/Admin/Etudes/", timeout=10, allow_redirects=True)
        gray(f"HTTP {resp.status_code} — URL finale : {resp.url}")

        if "SignIn" in resp.url or "signin" in resp.url.lower():
            report.add(c.failed(
                "Redirigé vers la page de login",
                fix="Le compte n'a pas level >= 2 dans Tomate. "
                    "Vérifier dans la BDD : SELECT level FROM etudiant WHERE mail='...';"
            ))
            err("Accès admin refusé — redirigé vers login")
            return False

        if resp.status_code == 200 and ("Etudes" in resp.text or "angular" in resp.text.lower()):
            report.add(c.passed("Accès aux pages admin confirmé (level >= 2)"))
            ok("Accès admin confirmé")
            return True

        report.add(c.warning(
            f"HTTP {resp.status_code} — résultat ambigu",
            detail=resp.text[:200]
        ))
        warn(f"Résultat ambigu sur /Admin/Etudes/ (HTTP {resp.status_code})")
        return True

    except Exception as e:
        report.add(c.failed("Exception", detail=str(e)))
        err(f"Exception : {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Rapport final
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(report: DiagReport):
    head("═══ RÉSUMÉ DU DIAGNOSTIC ═══")
    print(f"  URL cible  : {report.base_url}")
    print(f"  Horodatage : {report.timestamp}")
    print()

    for c in report.checks:
        if c.status == "ok":
            ok(c.message)
        elif c.status == "warn":
            warn(c.message)
            if c.fix:
                gray(f"    → {c.fix}")
        elif c.status == "fail":
            err(c.message)
            if c.fix:
                gray(f"    → {c.fix}")
        elif c.status == "skip":
            gray(f"  ○  {c.message} (ignoré)")

    print()
    total = len(report.checks)
    print(f"  {GREEN}{report.n_ok}/{total}{RESET} OK  "
          f"{YELLOW}{report.n_warn}/{total}{RESET} avertissements  "
          f"{RED}{report.n_fail}/{total}{RESET} échecs")

    print()
    if report.n_fail == 0 and report.n_warn == 0:
        print(f"  {GREEN}{BOLD}✓ Tout est opérationnel. Le bridge peut être utilisé.{RESET}")
    elif report.n_fail == 0:
        print(f"  {YELLOW}{BOLD}⚠ Le bridge devrait fonctionner mais vérifier les avertissements.{RESET}")
    else:
        print(f"  {RED}{BOLD}✗ {report.n_fail} problème(s) bloquant(s) à corriger avant de lancer l'export.{RESET}")

    print()
    info("Vérifier les routes Tomate si des endpoints Ajax échouent :")
    gray("  1. Ouvrir Tomate dans Chrome, se connecter en admin")
    gray("  2. Ouvrir DevTools → onglet Network → filtre XHR")
    gray("  3. Sauvegarder une étude → noter les URLs POST exactes")
    gray("  4. Comparer avec les routes dans tomate_bridge.py")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(base_url: str, email: str | None, password: str | None):
    report = DiagReport(base_url)

    head("1. Dépendances")
    if not check_requests(report):
        print_summary(report)
        return report

    import requests as req
    session = req.Session()
    session.headers.update({
        "Content-Type":     "application/json",
        "Accept":           "application/json",
        "X-Requested-With": "XMLHttpRequest",
    })

    head("2. Réseau")
    check_dns(report, base_url)
    reachable = check_https(report, session, base_url)
    if not reachable:
        print_summary(report)
        return report

    if not email or not password:
        warn("Pas de credentials fournis — diagnostic réseau uniquement.")
        c = Check("login"); report.add(c.skipped("pas de credentials"))
        print_summary(report)
        path = report.save()
        info(f"Rapport sauvegardé : {path}")
        return report

    head("3. Authentification")
    logged_in, _ = check_login(report, session, base_url, email, password)
    if not logged_in:
        print_summary(report)
        path = report.save()
        info(f"Rapport sauvegardé : {path}")
        return report

    head("4. Niveau d'accès")
    check_admin_access(report, session, base_url)

    head("5. Routes Ajax — test de chaque endpoint")

    # SaveEntreprise — payload minimal (nom vide → erreur métier attendue, pas 404)
    check_ajax_route(
        report, session, base_url,
        "/Ajax/SaveEntreprise/",
        payload={"id": None, "nom": "__DIAG_TEST__", "type": 3, "secteur": 6},
        expected_field="entreprise",
    )

    # SaveClient
    check_ajax_route(
        report, session, base_url,
        "/Ajax/SaveClient/",
        payload={"id": None, "nom": "__DIAG_TEST__", "prenom": "Test",
                 "mail": "", "titre": 1, "entreprise": None},
        expected_field="client",
    )

    # SaveEtude — payload minimal
    check_ajax_route(
        report, session, base_url,
        "/Ajax/SaveEtude/",
        payload={
            "id": None, "nom": "__DIAG_TEST__", "p_jeh": 400,
            "per_rem": 65, "fee": 0, "statut": 0, "lieu": 1,
            "domaines": [], "admins": [], "numero": 0,
        },
        expected_field="etude",
    )

    # SaveEtapes — sans etude_id valide → erreur métier attendue
    check_ajax_route(
        report, session, base_url,
        "/Ajax/SaveEtapes/",
        payload={"etude_id": None, "etapes": []},
    )

    # Nettoyage : on essaie de supprimer l'étude de test si elle a été créée
    for c in report.checks:
        if "SaveEtude" in c.name and c.status in ("ok", "warn"):
            # on ne supprime pas automatiquement — signaler à l'utilisateur
            warn("Une étude de test '__DIAG_TEST__' a peut-être été créée dans Tomate.")
            warn("→ La supprimer manuellement dans l'interface admin si nécessaire.")
            break

    print_summary(report)
    path = report.save()
    info(f"Rapport JSON sauvegardé : {path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Diagnostique la connexion entre le bridge Python et l'instance Tomate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Test réseau uniquement (pas de credentials)
  python diagnose.py --url https://tomate-dev.ensaejunioretudes.fr

  # Diagnostic complet avec authentification
  python diagnose.py --url https://tomate-dev.ensaejunioretudes.fr \\
                     --email admin@ensaeje.fr --password MON_MDP
        """
    )
    parser.add_argument("--url",      required=True,
                        help="URL base de l'instance Tomate (ex: https://tomate-dev.ensaejunioretudes.fr)")
    parser.add_argument("--email",    default=None, help="Email du compte admin Tomate")
    parser.add_argument("--password", default=None, help="Mot de passe du compte admin")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    result = run(base, args.email, args.password)
    sys.exit(0 if result.n_fail == 0 else 1)
